"""Query builder — JOIN, so sánh, NULL, và câu SQL THẬT chạy dưới database.

`repo.find()` chỉ so bằng (`=`). Khi cần hơn thế thì dùng cái này:

    query = repo.query().join(Camera).where(score__gte=0.8, label="person")
    events = await query.where(camera__name__like="Cổng%").order_by("-created_at").all()

Kết quả là `list[Event]` như `find()` — JOIN ở đây để **lọc**, không đổi kiểu
trả về. Cần cột của bảng kia thì nói rõ bằng `.select(...)`.

`.join(Camera)` không cần nói cột nối: khoá ngoại khai bằng `reference(Camera)`
đã đủ. Muốn chỉ rõ thì `on=Event.camera_id` — cột thật, không phải chuỗi, nên
đổi tên trường là gãy ngay lúc import chứ không phải lúc chạy câu lệnh.

## Mọi thứ chạy DƯỚI database

Đây là khác biệt đáng kể nhất so với `find(match=...)`. `match=` là hàm Python
nên không đẩy xuống SQL được: backend phải **kéo cả bảng về RAM** rồi mới lọc,
và `limit` cũng chỉ áp sau khi đã kéo về. Builder này sinh ra `WHERE`, `JOIN`,
`ORDER BY`, `LIMIT` thật, database làm hết.

Xem tận mắt câu lệnh sinh ra — không phải đoán:

    print(repo.query().where(score__gte=0.8).limit(5).sql())
    # SELECT events.id, events.score, ... FROM events
    # WHERE events.score >= 0.8 ORDER BY events.created_at LIMIT 5

## Hai cách viết điều kiện, dùng lẫn nhau được

Kiểu ngắn (`kwargs`) cho phần lớn trường hợp:

    .where(score__gte=0.8, label="person", deleted_at__isnull=True)

Kiểu đối tượng cột khi kwargs không diễn đạt nổi — OR, hoặc so **cột với cột**:

    E, C = F(Event), F(Camera)
    .where(or_(E.score >= 0.8, E.label == "fire"))
    .where(E.score > C.threshold)

## Backend nào chạy được

| Backend | Hỗ trợ |
|---|---|
| `postgres`, `sqlite` | đầy đủ, sinh SQL thật |
| `memory` | đầy đủ, tính bằng Python — để `fam test` chạy được mà không cần server |
| `mongodb` | **không** — ném lỗi nói rõ, xem `run_query` ở `mongo.py` |

MongoDB có `$lookup` nhưng ngữ nghĩa lệch đủ nhiều để bản giả lập sẽ đúng ở
demo và sai ở production. Thà nói không.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from fastapi_modular.core.exceptions import BadRequestError
from fastapi_modular.infrastructure.database.base import bind_value, check_value, mapping_for

if TYPE_CHECKING:
    from fastapi_modular.infrastructure.database.repository import Database

E = TypeVar("E")

# Toán tử viết được ở đuôi kwargs: `score__gte=0.8`.
OPERATORS = {
    "eq": "=",
    "ne": "!=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "in": "IN",
    "nin": "NOT IN",
    "like": "LIKE",
    "ilike": "ILIKE",
    "startswith": "LIKE",
    "endswith": "LIKE",
    "contains": "LIKE",
    "isnull": "IS NULL",
    "between": "BETWEEN",
}


# --------------------------------------------------------------------- cột
class Column:
    """Một cột của một entity. Lấy bằng `F(Event).score`.

    So sánh bằng toán tử Python thật (`>=`, `!=`), nên đọc gần như SQL. Vế
    phải là giá trị thường, hoặc là một `Column` khác để so **cột với cột**.
    """

    __slots__ = ("alias", "entity", "field")
    __hash__ = None                      # type: ignore[assignment]

    def __init__(self, entity: type, field: str, alias: str = "") -> None:
        fields = mapping_for(entity).fields
        if field not in fields:
            raise BadRequestError(
                f"{entity.__name__} không có trường {field!r}. "
                f"Có: {', '.join(sorted(fields))}"
            )
        self.entity = entity
        self.field = field
        # Tên bảng trong truy vấn. Rỗng = bảng mặc định của entity này. Chỉ
        # khác rỗng khi một entity xuất hiện hai lần (self join).
        self.alias = alias

    # Trả về Condition chứ không phải bool — đây là điểm của lớp này.
    def __eq__(self, other: Any) -> Condition:      # type: ignore[override]
        return Compare(self, "isnull", True) if other is None else Compare(self, "eq", other)

    def __ne__(self, other: Any) -> Condition:      # type: ignore[override]
        return Compare(self, "isnull", False) if other is None else Compare(self, "ne", other)

    def __gt__(self, other: Any) -> Condition:
        return Compare(self, "gt", other)

    def __ge__(self, other: Any) -> Condition:
        return Compare(self, "gte", other)

    def __lt__(self, other: Any) -> Condition:
        return Compare(self, "lt", other)

    def __le__(self, other: Any) -> Condition:
        return Compare(self, "lte", other)

    def is_null(self, yes: bool = True) -> Condition:
        return Compare(self, "isnull", yes)

    def is_not_null(self) -> Condition:
        return Compare(self, "isnull", False)

    def like(self, pattern: str) -> Condition:
        return Compare(self, "like", pattern)

    def ilike(self, pattern: str) -> Condition:
        return Compare(self, "ilike", pattern)

    def in_(self, values: Iterable[Any]) -> Condition:
        return Compare(self, "in", list(values))

    def not_in(self, values: Iterable[Any]) -> Condition:
        return Compare(self, "nin", list(values))

    def between(self, low: Any, high: Any) -> Condition:
        return Compare(self, "between", [low, high])

    def asc(self) -> Order:
        return Order(self, descending=False)

    def desc(self) -> Order:
        return Order(self, descending=True)

    def __repr__(self) -> str:
        if self.alias:
            return f"{self.alias}.{self.field}"
        return f"{self.entity.__name__}.{self.field}"


class _Fields:
    """Cái `F(Event)` trả về. `__getattr__` dựng `Column` và kiểm tên ngay."""

    __slots__ = ("_alias", "_entity")

    def __init__(self, entity: type, alias: str = "") -> None:
        self._entity = entity
        self._alias = alias

    def __getattr__(self, name: str) -> Column:
        return Column(self._entity, name, self._alias)

    def __repr__(self) -> str:
        return f"F({self._entity.__name__}, alias={self._alias!r})"


def F(entity: type, alias: str = "") -> Any:
    """Cổng vào các cột của một entity: `F(Event).score >= 0.8`.

    `alias` chỉ cần khi một bảng xuất hiện hai lần trong cùng truy vấn — nối
    bảng với chính nó. Phải trùng `alias=` đã truyền cho `.join(...)`:

        Cha = F(Camera, "cha")
        cameras.query().join(Camera, on=Camera.parent_id, alias="cha").where(Cha.zone == "A")

    Trả về `Any` để type checker không cản `F(Event).score` — tên trường được
    kiểm lúc chạy, ngay khi dựng, và lỗi liệt kê đủ tên hợp lệ.
    """
    return _Fields(entity, alias)


def column_of(value: Any, *, fallback: type | None = None) -> Column:
    """Chuyển thứ người dùng đưa vào thành `Column`.

    Nhận ba cách viết một cột, để chỗ nào nhận cột thì nhận cả ba:

        Event.camera_id          # gọn nhất, IDE tự gợi ý, đổi tên trường là gãy ngay
        F(Event).camera_id       # khi entity không khai `slots=True`
        "camera_id"              # chuỗi, chỉ hiểu được khi biết bảng gốc (`fallback`)

    Cách đầu chạy được vì `@dataclass(slots=True)` để lại một `member_descriptor`
    ở thân lớp, và descriptor đó mang sẵn tên trường lẫn lớp chủ. Entity KHÔNG
    khai `slots=True` thì `Event.camera_id` là giá trị mặc định của trường (hoặc
    `AttributeError` nếu trường không có mặc định) — không cứu được, nên lỗi ở
    đây nói thẳng cách viết khác.
    """
    if isinstance(value, Column):
        return value
    if inspect.ismemberdescriptor(value):
        return Column(value.__objclass__, value.__name__)
    if isinstance(value, str):
        if fallback is None:
            raise BadRequestError(
                f"Cột {value!r} chưa rõ thuộc bảng nào. Viết `Entity.{value}` "
                f"hoặc `F(Entity).{value}`."
            )
        return Column(fallback, value)
    raise BadRequestError(
        f"{value!r} không phải một cột. Viết `Event.ten_cot` (được, nếu entity khai "
        f"`@dataclass(slots=True)` hoặc kế thừa `Entity`), `F(Event).ten_cot`, "
        f"hoặc chuỗi \"ten_cot\"."
    )


@dataclasses.dataclass(frozen=True, slots=True)
class Order:
    column: Any                      # Column hoặc Aggregate
    descending: bool = False


# --------------------------------------------------------------- hàm gộp
AGGREGATES = ("count", "sum", "avg", "min", "max")


@dataclasses.dataclass(frozen=True, slots=True)
class Aggregate:
    """`count()`, `avg(Event.score)`... — chỉ dùng được sau `group_by`.

    So sánh được như một cột, nhưng kết quả phải đặt trong `having()` chứ
    không phải `where()`: `where` lọc TỪNG DÒNG trước khi gộp, `having` lọc
    TỪNG NHÓM sau khi gộp. Đó là khác biệt duy nhất giữa hai cái, và cũng là
    thứ hay bị nhầm nhất.
    """

    func: str
    column: Column | None = None
    distinct: bool = False

    def __post_init__(self) -> None:
        if self.func not in AGGREGATES:
            raise BadRequestError(f"Hàm gộp {self.func!r} không có. Có: {', '.join(AGGREGATES)}")
        if self.func != "count" and self.column is None:
            raise BadRequestError(f"`{self.func}` cần một cột: {self.func}(Event.score)")

    def __eq__(self, other: Any) -> Condition:      # type: ignore[override]
        return Compare(self, "eq", other)

    def __ne__(self, other: Any) -> Condition:      # type: ignore[override]
        return Compare(self, "ne", other)

    def __gt__(self, other: Any) -> Condition:
        return Compare(self, "gt", other)

    def __ge__(self, other: Any) -> Condition:
        return Compare(self, "gte", other)

    def __lt__(self, other: Any) -> Condition:
        return Compare(self, "lt", other)

    def __le__(self, other: Any) -> Condition:
        return Compare(self, "lte", other)

    def between(self, low: Any, high: Any) -> Condition:
        return Compare(self, "between", [low, high])

    def asc(self) -> Order:
        return Order(self, descending=False)

    def desc(self) -> Order:
        return Order(self, descending=True)

    def __repr__(self) -> str:
        inner = "" if self.column is None else repr(self.column)
        return f"{self.func}({'DISTINCT ' if self.distinct else ''}{inner})"


def count(column: Any = None, *, distinct: bool = False) -> Aggregate:
    """`count()` đếm dòng; `count(Event.id)` bỏ qua NULL; `distinct=True` đếm giá trị khác nhau."""
    return Aggregate("count", None if column is None else column_of(column), distinct)


def sum_(column: Any) -> Aggregate:
    """Tên có gạch dưới vì `sum` là hàm sẵn có của Python."""
    return Aggregate("sum", column_of(column))


def avg(column: Any) -> Aggregate:
    return Aggregate("avg", column_of(column))


def min_(column: Any) -> Aggregate:
    return Aggregate("min", column_of(column))


def max_(column: Any) -> Aggregate:
    return Aggregate("max", column_of(column))


# ---------------------------------------------------------------- điều kiện
class Condition:
    """Gốc của cây điều kiện. Nối bằng `&`, `|`, hoặc `and_`/`or_`/`not_`."""

    __slots__ = ()

    def __and__(self, other: Condition) -> Condition:
        return Group("and", (self, other))

    def __or__(self, other: Condition) -> Condition:
        return Group("or", (self, other))

    def __invert__(self) -> Condition:
        return Not(self)


@dataclasses.dataclass(frozen=True, slots=True)
class Compare(Condition):
    column: Any                      # Column hoặc Aggregate
    op: str
    value: Any

    def __post_init__(self) -> None:
        if self.op not in OPERATORS:
            raise BadRequestError(
                f"Toán tử {self.op!r} không có. Có: {', '.join(sorted(OPERATORS))}"
            )
        if not isinstance(self.value, Column):
            check_value(self.value, f"Điều kiện trên {self.column!r}")


@dataclasses.dataclass(frozen=True, slots=True)
class Group(Condition):
    op: str                          # "and" | "or"
    parts: tuple[Condition, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class Not(Condition):
    part: Condition


def as_condition(value: Any) -> Condition:
    """Kiểm một điều kiện, và bắt cái bẫy `Entity.cot == x` khi chưa kế thừa `Entity`.

    `==` giữa hai đối tượng bất kỳ luôn trả `True`/`False` chứ không báo lỗi,
    nên `where(Event.score == 0.8)` với entity chưa kế thừa `Entity` sẽ lặng lẽ
    thành `where(False)`. Chặn ngay ở đây, đắt nhất là một `isinstance`.
    """
    if isinstance(value, Condition):
        return value
    if isinstance(value, bool):
        raise BadRequestError(
            f"Điều kiện là {value!r} chứ không phải phép so sánh. Viết "
            "`Event.score == 0.8` chỉ ra điều kiện khi entity kế thừa `Entity` "
            "(`class Event(Entity):`); chưa kế thừa thì dùng "
            "`F(Event).score == 0.8` hoặc `.where(score=0.8)`."
        )
    raise BadRequestError(
        f"{value!r} không phải điều kiện. Dùng `Event.cot >= x`, `F(Event).cot >= x`, "
        "hoặc kiểu ngắn `.where(cot__gte=x)`."
    )


def is_null(column: Any) -> Condition:
    """`WHERE cot IS NULL`, viết được cả khi entity chưa kế thừa `Entity`.

        .where(is_null(Camera.rtsp))          # entity nào cũng dùng được
        .where(Camera.rtsp.is_null())         # gọn hơn, cần `class Camera(Entity)`

    Có hàm này vì `Camera.rtsp == None` tuy chạy đúng nhưng bị IDE gạch chân
    ("Comparison with None performed with equality operators"), còn `is None`
    thì Python không cho nạp chồng.
    """
    return Compare(column_of(column), "isnull", True)


def is_not_null(column: Any) -> Condition:
    """`WHERE cot IS NOT NULL`. Xem `is_null`."""
    return Compare(column_of(column), "isnull", False)


def like(column: Any, pattern: str) -> Condition:
    """`WHERE cot LIKE 'mẫu'` — PHÂN BIỆT hoa thường ở cả ba backend.

        .where(like(Camera.name, "Cổng%"))

    `%` là "bất kỳ bao nhiêu ký tự", `_` là "đúng một ký tự".
    """
    return Compare(column_of(column), "like", pattern)


def ilike(column: Any, pattern: str) -> Condition:
    """Như `like` nhưng BỎ QUA hoa thường: `ilike(Camera.name, "cổng%")`."""
    return Compare(column_of(column), "ilike", pattern)


def in_(column: Any, values: Iterable[Any]) -> Condition:
    """`WHERE cot IN (...)`. Tên có gạch dưới vì `in` là từ khoá của Python."""
    return Compare(column_of(column), "in", list(values))


def not_in(column: Any, values: Iterable[Any]) -> Condition:
    """`WHERE cot NOT IN (...)`."""
    return Compare(column_of(column), "nin", list(values))


def between(column: Any, low: Any, high: Any) -> Condition:
    """`WHERE cot BETWEEN a AND b` — hai đầu đều TÍNH VÀO."""
    return Compare(column_of(column), "between", [low, high])


def and_(*parts: Any) -> Condition:        # `Any`: xem ghi chú ở `Query.where`
    return Group("and", tuple(as_condition(p) for p in parts))


def or_(*parts: Any) -> Condition:
    """`WHERE a OR b` — thứ mà kwargs không viết được.

        .where(or_(F(Event).score >= 0.8, F(Event).label == "fire"))
    """
    if not parts:
        raise BadRequestError("`or_()` cần ít nhất một điều kiện")
    return Group("or", tuple(as_condition(p) for p in parts))


def not_(part: Any) -> Condition:
    return Not(as_condition(part))


# ------------------------------------------------------------------- join
class _Infer:
    """Giá trị mặc định của `on=`: chưa nói gì, đọc khoá ngoại mà suy.

    Không dùng `None` được vì entity không khai `slots=True` thì
    `Event.camera_id` chính là `None` — sẽ nuốt mất một lỗi gõ nhầm thành
    "tự suy".
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "<suy từ khoá ngoại>"


_INFER = _Infer()


JOIN_KINDS = ("inner", "left", "right", "outer")


@dataclasses.dataclass(frozen=True, slots=True)
class Join:
    entity: type
    alias: str
    on: Condition
    kind: str = "inner"          # inner | left | right | outer (FULL OUTER)

    @property
    def keeps_left(self) -> bool:
        """Giữ cả dòng bên TRÁI không khớp ai (LEFT và FULL)."""
        return self.kind in ("left", "outer")

    @property
    def keeps_right(self) -> bool:
        """Giữ cả dòng bên PHẢI không khớp ai (RIGHT và FULL)."""
        return self.kind in ("right", "outer")


def _alias_of(entity: type) -> str:
    """`Camera` -> `camera`. Đây là tiền tố dùng trong kwargs: `camera__name`."""
    return entity.__name__.lower()


def table_of(column: Column) -> str:
    """Tên bảng mà một cột trỏ tới trong truy vấn — alias nếu có, không thì mặc định.

    Backend tra bảng bằng CHUỖI này chứ không bằng `column.entity`: nối bảng với
    chính nó thì hai cột cùng entity mà là hai bảng khác nhau.
    """
    return column.alias or _alias_of(column.entity)


# ---------------------------------------------------------------- include
@dataclasses.dataclass(frozen=True, slots=True)
class Include:
    """Một bảng được lấy kèm và gắn vào kết quả dưới dạng trường lồng nhau."""

    entity: type
    name: str                        # tên trường trong kết quả; "" = chưa đặt
    root_field: str                  # cột bên bảng gốc dùng để ghép
    other_field: str                 # cột bên bảng kia
    to_list: bool                    # một-nhiều -> list, nhiều-một -> một object
    fields: tuple[tuple[str, str], ...] = ()      # (tên kết quả, tên cột)
    conditions: tuple[Condition, ...] = ()
    orders: tuple[Order, ...] = ()
    # Khác rỗng = bảng này KHÔNG có khoá ngoại trực tiếp với bảng gốc. Chưa
    # phải lỗi ngay: nếu nó nằm trong chuỗi `nest_under(...)` thì mắt xích của
    # chuỗi lo việc ghép. Chỉ khi đứng một mình mới ném thông điệp này ra.
    defer_error: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class NestLink:
    """Một mắt xích giữa hai lớp cạnh nhau trong chuỗi lồng nhau.

    Ghép bằng `dòng_ngoài[outer_field] == dòng_trong[inner_field]`. Chiều khoá
    ngoại quyết định lớp trong là DANH SÁCH hay MỘT object: khoá ngoại nằm bên
    lớp trong thì một dòng ngoài có nhiều dòng trong (list), nằm bên lớp ngoài
    thì ngược lại (object).
    """

    outer: type
    inner: type
    outer_field: str
    inner_field: str
    inner_is_list: bool


@dataclasses.dataclass(frozen=True, slots=True)
class NestLevel:
    """Một lớp trong chuỗi: bảng nào, trả cột nào, nằm dưới tên trường nào."""

    entity: type
    name: str                                     # tên trường chứa lớp NÀY
    fields: tuple[tuple[str, str], ...] = ()      # (tên kết quả, tên cột)


# Số id nhét vào một câu `WHERE ... IN (...)`. SQLite mặc định chỉ cho 999 tham
# số một câu lệnh, nên phải chia mẻ chứ không thể ném cả nghìn id vào một lần.
IN_CHUNK = 500


def _field_name(entity: type, item: Any) -> str:
    """`"name"` hoặc `Camera.name` hoặc `F(Camera).name` -> `"name"`, có kiểm tên.

    Cột của bảng KHÁC bị chặn ngay: `include(Event, fields=[Camera.name])` gần
    như luôn là gõ nhầm, và nếu để lọt thì kết quả chỉ thiếu trường một cách
    khó hiểu.
    """
    known = mapping_for(entity).fields
    if not isinstance(item, str):
        column = column_of(item)
        if column.entity is not entity:
            raise BadRequestError(
                f"{column!r} là cột của {column.entity.__name__}, không phải "
                f"{entity.__name__}."
            )
        return column.field
    if item not in known:
        raise BadRequestError(
            f"{entity.__name__} không có trường {item!r}. Có: {', '.join(sorted(known))}"
        )
    return item


def _fields_of(
    entity: type,
    fields: Any,
    exclude: Sequence[Any],
    rename: dict[str, Any] | None = None,
) -> tuple[tuple[str, str], ...]:
    """Chốt các cột sẽ trả về, dạng `(tên kết quả, tên cột)`.

    Ba tham số, ba việc khác nhau — dùng riêng hay dùng chung đều được:

        fields=["id", Camera.name]        chỉ những cột này
        exclude=["raw"]                   mọi cột TRỪ những cột này
        rename={"ma": "id"}               giữ nguyên tập cột, chỉ đổi TÊN TRẢ VỀ

    `rename` có mặt vì `fields={"ma": "id"}` bắt liệt kê hết mọi cột khi bạn chỉ
    muốn đổi tên một hai cái.

    **Chiều của dict là `{tên bạn muốn: tên cột có thật}`** — giống hệt
    `select(ma=Camera.id)`. Viết ngược thì `"ma"` không phải tên cột nên báo lỗi
    ngay, không âm thầm cho ra tên sai.

    Tên cột sai bị bắt NGAY tại đây, kèm danh sách tên đúng.
    """
    known = mapping_for(entity).fields
    if isinstance(fields, dict):
        chosen = [(out_name, _field_name(entity, column_name)) for out_name, column_name in fields.items()]
    else:
        chosen = [(_field_name(entity, f),) * 2 for f in fields] or [(f, f) for f in known]

    dropped = {_field_name(entity, f) for f in exclude}
    chosen_kept = [(out_name, column_name) for out_name, column_name in chosen if column_name not in dropped]

    # Đổi tên TẠI CHỖ, không xoá rồi thêm vào cuối: thứ tự khoá trong dict trả
    # về chính là thứ tự trường trong response JSON, đổi vị trí là người ta thấy.
    renamed_by_column = {_field_name(entity, column_name): out_name for out_name, column_name in (rename or {}).items()}
    kept_columns: dict[str, str] = {}
    for out_name, column_name in chosen_kept:
        kept_columns[renamed_by_column.get(column_name, out_name)] = column_name
    for column_name, out_name in renamed_by_column.items():
        if column_name not in kept_columns.values():
            kept_columns[out_name] = column_name
    return tuple(kept_columns.items())


# ------------------------------------------------------------------- spec
@dataclasses.dataclass(slots=True)
class QuerySpec:
    """Mô tả truy vấn, không dính tới backend nào.

    Backend nhận cái này rồi tự dịch: SQL dịch sang SQLAlchemy Core, memory
    dịch sang vòng lặp Python. Nhờ vậy cùng một câu lệnh chạy được ở `fam test`
    (memory) lẫn production (postgres) mà không phải viết hai lần.
    """

    entity: type
    joins: list[Join] = dataclasses.field(default_factory=list)
    conditions: list[Condition] = dataclasses.field(default_factory=list)
    orders: list[Order] = dataclasses.field(default_factory=list)
    limit: int | None = None
    offset: int = 0
    selects: dict[str, Any] = dataclasses.field(default_factory=dict)
    distinct: bool = False
    groups: list[Column] = dataclasses.field(default_factory=list)
    havings: list[Condition] = dataclasses.field(default_factory=list)
    includes: list[Include] = dataclasses.field(default_factory=list)
    nest: tuple[type, ...] = ()          # chuỗi lồng nhau, NGOÀI -> TRONG
    nest_names: dict[type, str] = dataclasses.field(default_factory=dict)
    nest_cols: dict[type, tuple[tuple[str, str], ...]] = dataclasses.field(
        default_factory=dict
    )
    nest_link: NestLink | None = None    # mắt xích tự khai bằng `on=` (bảng tự trỏ)

    def entity_of(self, alias: str) -> type:
        if alias == _alias_of(self.entity):
            return self.entity
        for join in self.joins:
            if join.alias == alias:
                return join.entity
        raise BadRequestError(
            f"Chưa `join` bảng {alias!r}. Đang có: "
            f"{', '.join([_alias_of(self.entity), *(j.alias for j in self.joins)])}"
        )


# ----------------------------------------------------------------- builder
class Query(Generic[E]):
    """Builder. Mỗi method trả về CHÍNH nó, gọi nối đuôi nhau.

    Lấy từ `repo.query()`. Không có gì chạy cho tới khi bạn gọi `.all()`,
    `.first()`, `.count()` hay `.exists()`.
    """

    __slots__ = ("_db", "_having", "_spec", "_where")

    def __init__(self, entity: type[E], database: Database) -> None:
        self._db = database
        self._spec = QuerySpec(entity=entity)
        # Mỗi phần tử là một nhóm điều kiện nối bằng AND; các nhóm nối với nhau
        # bằng OR. `where` thêm vào nhóm đang mở, `or_where` mở nhóm mới.
        self._where: list[list[Condition]] = [[]]
        self._having: list[list[Condition]] = [[]]

    # ------------------------------------------------------------- dựng
    def join(
        self,
        entity: type,
        *,
        on: Any = _INFER,
        alias: str = "",
    ) -> Query[E]:
        """`INNER JOIN` — chỉ giữ dòng khớp được cả hai bên.

        Bốn kiểu nối là bốn method riêng, không phải cờ: `join`, `left_join`,
        `right_join`, `outer_join`. Đọc tên là biết ra SQL gì.

            .join(Camera)                          # đã khai `reference(Camera)` thì đủ
            .join(Camera, on=Event.camera_id)      # chỉ đúng cột nối
            .join(Camera, on="camera_id")          # cũng được, nhưng đổi tên trường không gãy
            .join(Camera, on=("camera_id", "id"))  # nói rõ cả hai vế
            .join(Camera, on=F(Event).camera_id == F(Camera).id)

        Không truyền `on` thì cột nối đọc từ khoá ngoại đã khai bằng
        `reference(...)`. Có đúng một khoá ngoại khớp thì dùng; không có hoặc
        có nhiều thì báo lỗi kèm danh sách, chứ không đoán bừa.

        `on=Event.camera_id` nhận cột của bảng nào cũng được: cột thuộc bảng
        đang nối thì vế kia là khoá chính của bảng gốc, và ngược lại. Nhờ vậy
        chiều một-nhiều (`.join(Event, on=Event.camera_id)`) viết y hệt.

        `alias=` để nối một bảng với CHÍNH NÓ — xem `F(Camera, "cha")`.
        """
        return self._add_join(entity, on, alias, "inner")

    def left_join(self, entity: type, *, on: Any = _INFER, alias: str = "") -> Query[E]:
        """`LEFT JOIN` — giữ cả dòng bên trái không khớp ai, cột bên phải là NULL.

        Đây là cách tìm "cái nào còn trống":

            .left_join(Event).where(F(Event).id.is_null())    # camera CHƯA có sự kiện
        """
        return self._add_join(entity, on, alias, "left")

    def right_join(self, entity: type, *, on: Any = _INFER, alias: str = "") -> Query[E]:
        """`RIGHT JOIN` — giữ cả dòng bên phải không khớp ai. Bắt buộc `.select(...)`.

        Cần `.select` vì có dòng KHÔNG có bản ghi nào của bảng gốc, mà mặc định
        truy vấn trả về entity của bảng gốc — trả một entity toàn `None` là bịa.
        Muốn nhận entity thì đảo lại: lấy bảng kia làm gốc rồi `left_join`.

        SQL sinh ra là `LEFT JOIN` với hai vế đảo chỗ, vì hai câu đó bằng nhau
        và không phải database nào cũng có `RIGHT JOIN` (SQLite chỉ có từ 3.39).
        """
        return self._add_join(entity, on, alias, "right")

    def outer_join(self, entity: type, *, on: Any = _INFER, alias: str = "") -> Query[E]:
        """`FULL OUTER JOIN` — giữ dòng không khớp của CẢ HAI bên. Bắt buộc `.select(...)`.

        Cần SQLite từ 3.39 trở lên; Postgres thì lúc nào cũng có.
        """
        return self._add_join(entity, on, alias, "outer")

    def _add_join(self, entity: type, on: Any, alias: str, kind: str) -> Query[E]:
        name = alias or _alias_of(entity)
        if name in {_alias_of(self._spec.entity), *(j.alias for j in self._spec.joins)}:
            raise BadRequestError(
                f"Đã có bảng tên {name!r} trong truy vấn. Truyền `alias=` để đặt tên khác."
            )

        # Cột chỉ mang alias khi alias KHÁC tên mặc định của bảng. Nhờ vậy
        # truy vấn thường không có alias nào bám vào cột, và hai cách viết cùng
        # một phép nối cho ra đúng một điều kiện giống hệt nhau.
        rieng = "" if name == _alias_of(entity) else name
        self._spec.joins.append(
            Join(entity=entity, alias=name, on=self._on_condition(entity, on, rieng), kind=kind)
        )
        return self

    def _known(self) -> list[type]:
        """Các bảng đã có trong truy vấn, bảng gốc trước."""
        return [self._spec.entity, *(j.entity for j in self._spec.joins)]

    def _on_condition(self, entity: type, on: Any, name: str) -> Condition:
        if on is _INFER:
            return self._infer_on(entity, name)
        if isinstance(on, Condition):
            return on
        if isinstance(on, tuple):
            left, right = on
            return Column(self._spec.entity, left) == Column(entity, right, name)
        if isinstance(on, str):
            return Column(self._spec.entity, on) == Column(entity, "id", name)

        column = column_of(on)
        # Thứ tự hai nhánh này quyết định ca nối bảng với CHÍNH NÓ: ở đó cả hai
        # nhánh đều đúng kiểu, và cột không mang alias thì hiểu là bảng gốc.
        if column.entity is self._spec.entity and not column.alias:
            return column == Column(entity, "id", name)
        if column.entity is entity:
            # Cột nằm ở bảng ĐANG nối: chiều một-nhiều, vế kia là khoá chính bảng gốc.
            return Column(entity, column.field, name) == Column(self._spec.entity, "id")
        if column.entity in self._known():
            return column == Column(entity, "id", name)
        raise BadRequestError(
            f"`on={column!r}` không dùng được: {column.entity.__name__} không phải "
            f"{entity.__name__} lẫn bảng nào đã có trong truy vấn "
            f"({', '.join(k.__name__ for k in self._known())})."
        )

    def _infer_on(self, entity: type, name: str) -> Condition:
        """Đọc `reference(...)` để tìm cột nối, khi người dùng không nói."""
        known = self._known()
        if len(set(known)) != len(known):
            raise BadRequestError(
                "Truy vấn đang có cùng một bảng hai lần nên không suy được cột "
                "nối. Nói rõ bằng `on=...`."
            )

        pairs: list[tuple[Column, Column]] = []
        for owner in known:
            for field, ref in mapping_for(owner).references:
                if ref.target is entity:
                    pairs.append((Column(owner, field), Column(entity, ref.column, name)))
        for field, ref in mapping_for(entity).references:
            if ref.target in known:
                pairs.append((Column(entity, field, name), Column(ref.target, ref.column)))

        if len(pairs) == 1:
            left, right = pairs[0]
            return left == right
        names = ", ".join(k.__name__ for k in known)
        if not pairs:
            raise BadRequestError(
                f"Không biết nối {entity.__name__} vào đâu: giữa nó và {names} chưa "
                f"có khoá ngoại nào khai bằng `reference(...)`. Nói rõ bằng "
                f"`on={entity.__name__}.ten_cot` hoặc `on=\"ten_cot\"`."
            )
        raise BadRequestError(
            f"Nối {entity.__name__} với {names} theo cột nào? Có {len(pairs)} khoá "
            f"ngoại khớp: " + "; ".join(f"{left} = {right}" for left, right in pairs)
            + ". Chọn một bằng `on=...`."
        )

    def where(self, *conditions: Any, **lookups: Any) -> Query[E]:
        """Thêm điều kiện. Nhiều lời gọi `where` nối với nhau bằng AND.

            .where(score__gte=0.8, label="person")      # kiểu ngắn
            .where(or_(E.score >= 0.8, E.label == "fire"))   # kiểu đối tượng

        Đuôi `__<toán tử>`: `gt gte lt lte ne in nin like ilike startswith
        endswith contains isnull between`. Không có đuôi thì là so bằng.

        Tiền tố là tên bảng đã `join`: `camera__name__like="Cổng%"`.

        `conditions` khai `Any` chứ không phải `Condition`, cố ý: type checker
        đọc annotation `score: float` nên với nó `Camera.score > 1` là `bool`,
        và khai `Condition` ở đây làm IDE gạch đỏ một câu hoàn toàn đúng. Sai
        kiểu thật thì `as_condition` bắt lúc chạy, kèm lời chỉ cách viết lại.
        """
        self._where[-1].extend(self._as_conditions(conditions, lookups))
        self._spec.conditions = _compose(self._where)
        return self

    def or_where(self, *conditions: Any, **lookups: Any) -> Query[E]:
        """Mở một nhánh OR mới. Các `where` sau đó lại nối AND vào nhánh này.

            .where(Event.label == "person").where(Event.score >= 0.9)
            .or_where(Event.label == "fire").where(Event.score >= 0.3)

        cho ra `(label='person' AND score>=0.9) OR (label='fire' AND score>=0.3)`.

        Gọi `or_where` khi chưa có `where` nào thì nó chỉ là `where`.
        """
        self._where.append(list(self._as_conditions(conditions, lookups)))
        self._spec.conditions = _compose(self._where)
        return self

    # Bảy toán tử dưới đây là những thứ Python không có ký hiệu: `LIKE`, `IN`,
    # `IS NULL`, `BETWEEN`. Để thẳng trên builder vì đây là chỗ DUY NHẤT IDE gợi
    # ý được — `repo.query()` có kiểu `Query[E]` nên gõ `.` là hiện ra, còn
    # `Camera.name` thì type checker vẫn tưởng là `str`.
    def like(self, column: Any, pattern: str) -> Query[E]:
        """`WHERE cot LIKE 'mẫu'`, PHÂN BIỆT hoa thường ở cả ba backend.

            .like(Camera.name, "Cổng%")

        `%` là "bao nhiêu ký tự cũng được", `_` là "đúng một ký tự".
        """
        return self.where(like(column, pattern))

    def ilike(self, column: Any, pattern: str) -> Query[E]:
        """Như `like` nhưng BỎ QUA hoa thường: `.ilike(Camera.name, "cổng%")`."""
        return self.where(ilike(column, pattern))

    def is_null(self, column: Any) -> Query[E]:
        """`WHERE cot IS NULL`. Đừng viết `cot == None` — IDE gạch chân câu đó."""
        return self.where(is_null(column))

    def is_not_null(self, column: Any) -> Query[E]:
        """`WHERE cot IS NOT NULL`."""
        return self.where(is_not_null(column))

    def in_(self, column: Any, values: Iterable[Any]) -> Query[E]:
        """`WHERE cot IN (...)`. Gạch dưới vì `in` là từ khoá của Python."""
        return self.where(in_(column, values))

    def not_in(self, column: Any, values: Iterable[Any]) -> Query[E]:
        """`WHERE cot NOT IN (...)`."""
        return self.where(not_in(column, values))

    def between(self, column: Any, low: Any, high: Any) -> Query[E]:
        """`WHERE cot BETWEEN a AND b` — hai đầu đều TÍNH VÀO."""
        return self.where(between(column, low, high))

    def _as_conditions(self, conditions: tuple, lookups: dict) -> list[Condition]:
        return [
            *(as_condition(c) for c in conditions),
            *(self._parse_lookup(key, value) for key, value in lookups.items()),
        ]

    def _parse_lookup(self, key: str, value: Any) -> Condition:
        parts = key.split("__")
        op = "eq"
        if len(parts) > 1 and parts[-1] in OPERATORS:
            op = parts.pop()
        if not parts:
            raise BadRequestError(f"Điều kiện {key!r} thiếu tên cột")

        # Tiền tố là tên bảng đã join; không có thì là bảng gốc.
        if len(parts) > 1:
            aliases = {_alias_of(self._spec.entity), *(j.alias for j in self._spec.joins)}
            if parts[0] not in aliases:
                # Ca thường gặp nhất ở đây KHÔNG phải quên join, mà là gõ sai
                # toán tử: `score__lonhon=0.8`. Báo "chưa join bảng 'score'"
                # thì người ta đi tìm nhầm hướng hoàn toàn.
                if len(parts) == 2 and parts[0] in mapping_for(self._spec.entity).fields:
                    raise BadRequestError(
                        f"Điều kiện {key!r}: {parts[1]!r} không phải toán tử. "
                        f"Có: {', '.join(sorted(OPERATORS))}"
                    )
                raise BadRequestError(
                    f"Điều kiện {key!r}: {parts[0]!r} không phải bảng đã `join` "
                    f"(đang có: {', '.join(sorted(aliases))}) và cũng không phải "
                    f"toán tử. Xem lại tên cột hay tên bảng."
                )
            entity = self._spec.entity_of(parts[0])
            field = "__".join(parts[1:])
            alias = "" if parts[0] == _alias_of(entity) else parts[0]
        else:
            entity, field, alias = self._spec.entity, parts[0], ""

        if op in {"startswith", "endswith", "contains"}:
            pattern = {"startswith": f"{value}%", "endswith": f"%{value}",
                       "contains": f"%{value}%"}[op]
            return Compare(Column(entity, field, alias), "like", pattern)
        return Compare(Column(entity, field, alias), op, value)

    def order_by_asc(self, *fields: Any) -> Query[E]:
        """Sắp tăng dần: `order_by_asc("name")`, `order_by_asc(Event.score)`.

        Nhiều cột thì gọi nối tiếp, thứ tự gọi là thứ tự ưu tiên:

            .order_by_desc("score").order_by_asc("created_at")
        """
        return self._add_orders(fields, descending=False)

    def order_by_desc(self, *fields: Any) -> Query[E]:
        """Sắp giảm dần. Nhận cả hàm gộp: `order_by_desc(count())`."""
        return self._add_orders(fields, descending=True)

    def _add_orders(self, fields: tuple, descending: bool) -> Query[E]:
        self._spec.orders.extend(_orders_of(self._spec.entity, fields, descending))
        return self

    def include(
        self,
        entity: type,
        *,
        name: str = "",
        on: Any = _INFER,
        fields: Any = (),
        exclude: Sequence[Any] = (),
        rename: dict[str, Any] | None = None,
        where: Any = None,
        order_by_asc: Any = None,
        order_by_desc: Any = None,
    ) -> Query[E]:
        """Lấy kèm bảng khác và gắn vào kết quả thành trường lồng nhau.

            # mỗi camera kèm danh sách sự kiện của nó, trường "events"
            await cameras.query().include(Event).all()
            # [{"id": "c1", ..., "events": [{...}, {...}]}]

            # mỗi sự kiện kèm camera của nó, trường "camera"
            await events.query().include(Camera).all()
            # [{"id": "e1", ..., "camera": {...}}]

        Chiều nào là do khoá ngoại quyết định, không phải bạn khai: khoá ngoại
        nằm bên `Event` thì một camera có NHIỀU sự kiện (trả list), nằm bên bảng
        gốc thì ngược lại (trả một object hoặc `None`).

        Tên trường mặc định là tên class viết thường, thêm `s` nếu là list
        (`events`, `camera`). Đổi bằng `name="su_kien"`.

        Chọn cột của bảng ĐƯỢC LẤY KÈM bằng ba tham số giống hệt `select`:

            fields=["id", Event.label]           chỉ những cột này
            exclude=["raw"]                      mọi cột trừ những cột này
            rename={"nhan": "label"}             đủ cột, chỉ đổi tên trả về

        `where=`, `order_by_asc=`, `order_by_desc=` lọc và sắp bảng đó.

        **Có `include` thì kết quả là `list[dict]`, không phải `list[Entity]`** —
        entity là dataclass `slots=True`, không gắn thêm trường vào được.

        Mỗi `include` là MỘT câu lệnh nữa (`WHERE khoá IN (...)`), không phải
        một câu cho mỗi dòng. Mười camera thì hai câu, không phải mười một.
        """
        try:
            root_field, other_field, to_list = self._relation(entity, on)
            defer_error = ""
        except BadRequestError as exc:
            # Chưa chắc là lỗi: `cmts.include(User).nest_under(User, Post, Cmt)`
            # thì User ghép qua Post chứ không qua bảng gốc. Ghi lại thông điệp,
            # đến `.all()` mà nó vẫn đứng ngoài chuỗi thì mới ném.
            root_field, other_field, to_list = "", "", True
            defer_error = str(exc)
        self._spec.includes.append(Include(
            entity=entity,
            name=name,
            root_field=root_field,
            other_field=other_field,
            to_list=to_list,
            defer_error=defer_error,
            fields=_fields_of(entity, fields, exclude, rename),
            conditions=tuple(as_condition(c) for c in (
                where if isinstance(where, (list, tuple)) else [where] if where is not None else []
            )),
            orders=tuple((
                *_orders_of(entity, order_by_desc, descending=True),
                *_orders_of(entity, order_by_asc, descending=False),
            )),
        ))
        return self

    def _relation(self, entity: type, on: Any) -> tuple[str, str, bool]:
        """(cột bảng gốc, cột bảng kia, có phải một-nhiều không)."""
        if on is not _INFER:
            column = column_of(on)
            # Bảng gốc xét trước: khi một bảng trỏ về CHÍNH NÓ (`parent_id`),
            # cả hai nhánh đều đúng kiểu, và cột đó là cột của bảng gốc.
            if column.entity is self._spec.entity:
                return column.field, "id", False
            if column.entity is entity:
                return "id", column.field, True
            raise BadRequestError(
                f"`on={column!r}` không thuộc {entity.__name__} lẫn "
                f"{self._spec.entity.__name__}."
            )

        root = self._spec.entity
        pairs: list[tuple[str, str, bool]] = [
            *((ref.column, field, True)
              for field, ref in mapping_for(entity).references if ref.target is root),
            *((field, ref.column, False)
              for field, ref in mapping_for(root).references if ref.target is entity),
        ]
        if len(pairs) == 1:
            return pairs[0]
        if not pairs:
            raise BadRequestError(
                f"Không biết lấy {entity.__name__} theo cột nào: giữa nó và "
                f"{root.__name__} chưa có khoá ngoại nào khai bằng `reference(...)`. "
                f"Nói rõ bằng `on={entity.__name__}.ten_cot`."
            )
        raise BadRequestError(
            f"Lấy {entity.__name__} theo cột nào? Có {len(pairs)} khoá ngoại khớp. "
            f"Chọn một bằng `on=...`."
        )

    def nest_under(
        self,
        *entities: type,
        name: str = "",
        on: Any = _INFER,
        fields: Any = (),
        exclude: Sequence[Any] = (),
        rename: dict[str, Any] | None = None,
    ) -> Query[E]:
        """Xếp thứ tự lồng nhau của kết quả: bảng nào ra ngoài, bảng nào vào trong.

        Kể tên từ NGOÀI vào TRONG. Bảng gốc (`repo`) không kể thì hiểu là nằm
        trong cùng:

            events.query().nest_under(Camera)
            # [{"id": "c1", …, "events": [{…}, {…}]}]

            logs.query().nest_under(Camera, CameraLog, ItemLog)
            # [{"id": "c1", …, "cameralogs": [{…, "itemlogs": [{…}]}]}]

        Hai bảng cạnh nhau phải có khoá ngoại **trực tiếp**; chiều khoá ngoại
        quyết định lớp trong là danh sách hay một object. Không có thì báo lỗi
        kèm chuỗi đi được, chứ không tự sắp lại giúp — sắp sai thì dữ liệu sai
        mà không ai thấy.

        `nest_under` chỉ nói THỨ TỰ. Cột và tên trường của từng bảng khai bằng
        `include(X, fields=…, name=…)`; cột của bảng gốc khai bằng `.select(...)`.
        Dạng một bảng thì truyền thẳng `fields=`/`name=` ở đây cũng được.

        Ba điều dễ vấp:

        - **`limit` vẫn đếm theo bảng GỐC.** `.limit(20).nest_under(Camera)` là
          20 sự kiện gom lại thành vài camera, không phải 20 camera.
        - **Dòng có khoá ngoại NULL bị bỏ** — nó không thuộc cha nào để gom vào.
        - Thứ tự lớp ngoài theo thứ tự xuất hiện của dòng bảng gốc, nên
          `.order_by_*(...)` vẫn có tác dụng.
        """
        if self._spec.groups:
            raise BadRequestError("`nest_under` và `group_by` không dùng chung được.")
        if not entities:
            raise BadRequestError("`nest_under()` cần ít nhất một bảng.")
        if len(set(entities)) != len(entities):
            raise BadRequestError("`nest_under(...)` có bảng bị lặp.")
        if len(entities) > 1 and (name or fields or exclude or rename
                                  or on is not _INFER):
            raise BadRequestError(
                "Nhiều bảng thì khai cột và tên cho TỪNG bảng bằng "
                '`include(X, fields=…, name=…)`; `nest_under(...)` chỉ nói thứ tự.'
            )

        if fields or exclude or rename:
            self._spec.nest_cols[entities[0]] = _fields_of(
                entities[0], fields, exclude, rename
            )
        if name:
            # `name` ở đây đặt tên cho danh sách các dòng của BẢNG GỐC, vì bảng
            # gốc mới là thứ nằm bên trong bảng vừa nêu.
            self._spec.nest_names[self._spec.entity] = name
        if on is not _INFER:
            # Bảng tự trỏ về chính nó: hai lớp cùng một entity nên phải nói rõ cột.
            column = column_of(on)
            self._spec.nest_link = NestLink(
                outer=entities[0], inner=self._spec.entity,
                outer_field="id", inner_field=column.field, inner_is_list=True,
            )
        self._spec.nest = tuple(entities)
        return self

    def group_by(self, *fields: Any) -> Query[E]:
        """Gộp dòng thành nhóm. Bắt buộc đi kèm `.select(...)`.

            rows = await (events.query()
                          .group_by(Event.camera_id)
                          .select("camera_id", so_luong=count(), diem_tb=avg(Event.score))
                          .having(count() > 5)
                          .all())
            # [{"camera_id": "c1", "so_luong": 12, "diem_tb": 0.83}, ...]

        Phải có `.select(...)` vì sau khi gộp thì một dòng không còn là một bản
        ghi nữa — trả về `Event` lúc này chỉ là bịa.
        """
        for item in fields:
            self._spec.groups.append(column_of(item, fallback=self._spec.entity))
        return self

    def having(self, *conditions: Any) -> Query[E]:
        """Lọc theo kết quả gộp: `.having(count() > 5, avg(Event.score) >= 0.8)`.

        Khác `where` ở chỗ lọc lúc nào: `where` bỏ bớt DÒNG trước khi gộp,
        `having` bỏ bớt NHÓM sau khi gộp. "Camera có hơn 5 sự kiện điểm cao"
        là `.where(Event.score >= 0.8).having(count() > 5)` — đổi chỗ hai cái
        cho ra con số khác hẳn.
        """
        self._having[-1].extend(as_condition(c) for c in conditions)
        self._spec.havings = _compose(self._having)
        return self

    def or_having(self, *conditions: Any) -> Query[E]:
        """Mở một nhánh OR mới cho `having`, cùng luật với `or_where`."""
        self._having.append([as_condition(c) for c in conditions])
        self._spec.havings = _compose(self._having)
        return self

    def limit(self, count: int | None) -> Query[E]:
        self._spec.limit = count
        return self

    def offset(self, count: int) -> Query[E]:
        self._spec.offset = count
        return self

    def distinct(self, yes: bool = True) -> Query[E]:
        """Bỏ dòng trùng. Hay cần sau `join` một-nhiều: mỗi camera có 10 sự
        kiện thì camera đó hiện 10 lần."""
        self._spec.distinct = yes
        return self

    def select(
        self,
        *columns: Any,
        fields: Any = (),
        exclude: Sequence[Any] = (),
        rename: dict[str, Any] | None = None,
        add: dict[str, Any] | None = None,
        **renamed: Any,
    ) -> Query[E]:
        """Chọn cột trả về. Có gọi `select` thì kết quả là `list[dict]`.

            .select("id", "score")                    # CHỈ hai cột này
            .select(fields=["id", "score"])           # y hệt, dạng danh sách
            .select(fields={"ma": "id", "diem": "score"})   # chỉ 2 cột, đổi tên luôn
            .select(exclude=["raw_payload"])          # mọi cột TRỪ cái này
            .select(rename={"ma": "id"})              # ĐỦ cột, chỉ đổi tên `id`
            .select(add={"ten_camera": Camera.name})  # ĐỦ cột, THÊM một cột nữa
            .select("id", camera_name=Camera.name)    # cột bảng đã join, đặt tên
            .select(so_luong=count())                 # hàm gộp, bắt buộc đặt tên

        **`select` là CHỌN, không phải THÊM.** Kể tên cột nào thì kết quả chỉ có
        những cột đó — giống hệt `SELECT` của SQL. Muốn giữ đủ cột rồi thêm một
        cột nữa thì dùng `add=`, muốn giữ đủ mà đổi tên thì `rename=`.

        `fields=` và `exclude=` đặt tên giống hệt `include(...)`, để chỗ nào
        chọn cột cũng viết một kiểu. `exclude=` lấy mọi cột của **bảng gốc** rồi
        bỏ đi những cái kể tên — nó cần biết "đủ bộ" là gì nên chỉ làm được với
        bảng gốc; cột bảng đã join thì thêm bằng `columns`/`fields=`.

        Không gọi `select` thì trả về `list[Event]` như `find()`.

        Một hạn chế nhỏ: không đặt được tên cột kết quả là `fields`, `exclude`,
        `rename` hay `add`, vì bốn tên đó đã là tham số. Cần thì đặt tên khác,
        hoặc đưa vào `add={"exclude": ...}`.
        """
        if exclude or rename or add or isinstance(fields, dict):
            # `rename`/`add` không kèm `fields` nghĩa là "giữ đủ cột bảng gốc".
            for out_name, column_name in _fields_of(self._spec.entity, fields, exclude, rename):
                self._spec.selects[out_name] = Column(self._spec.entity, column_name)
            fields = ()

        for out_name, item in (add or {}).items():
            if isinstance(item, Aggregate):
                raise BadRequestError(
                    f"`add={{{out_name!r}: {item!r}}}` không được: hàm gộp làm câu lệnh "
                    f"thành câu GỘP, mà đã gộp thì không còn 'mọi cột' nào để giữ "
                    f"— `SELECT *, {item!r}` là câu lỗi ở PostgreSQL. Viết "
                    f"`.group_by(cot).select('cot', {out_name}={item!r})` để đếm theo "
                    f"nhóm, hoặc `.select({out_name}={item!r})` để gộp cả bảng thành "
                    f"một dòng."
                )
            self._spec.selects[out_name] = column_of(item, fallback=self._spec.entity)

        for item in (*columns, *fields):
            if isinstance(item, Aggregate):
                raise BadRequestError(
                    f"`{item!r}` phải được đặt tên: `.select(so_luong={item!r})`"
                )
            column = column_of(item, fallback=self._spec.entity)
            self._spec.selects[column.field] = column

        for name, item in renamed.items():
            self._spec.selects[name] = (
                item if isinstance(item, Aggregate)
                else column_of(item, fallback=self._spec.entity)
            )
        return self

    # ------------------------------------------------------------- chạy
    def _need_select(self) -> None:
        """Có hai ca một dòng kết quả KHÔNG còn là một bản ghi của bảng gốc."""
        if self._spec.selects:
            return
        if self._spec.groups:
            raise BadRequestError(
                "`group_by` phải đi với `.select(...)`: sau khi gộp thì một dòng "
                "không còn là một bản ghi, trả về "
                f"{self._spec.entity.__name__} lúc này chỉ là bịa. Ví dụ: "
                ".select('camera_id', so_luong=count())"
            )
        missing = [j for j in self._spec.joins if j.keeps_right]
        if missing:
            out_name = "right_join" if missing[0].kind == "right" else "outer_join"
            raise BadRequestError(
                f"`{out_name}` phải đi với `.select(...)`: nó sinh cả những dòng KHÔNG "
                f"có bản ghi {self._spec.entity.__name__} nào, mà mặc định truy vấn "
                f"trả về {self._spec.entity.__name__}. Hoặc đảo lại: lấy "
                f"{missing[0].entity.__name__} làm bảng gốc rồi `left_join`."
            )

    def _check_grouped(self) -> None:
        """Sau `group_by`, mỗi cột trả về phải là cột đã gộp hoặc một hàm gộp.

        Cột khác thì mỗi nhóm có NHIỀU giá trị khác nhau, lấy cái nào cũng là
        bịa. SQLite im lặng trả về giá trị của một dòng bất kỳ trong nhóm,
        Postgres thì từ chối hẳn — tức là câu lệnh chạy được ở dev rồi đổ ở
        production. Chặn ở đây để hai nơi giống nhau.
        """
        if not self._spec.groups:
            return
        grouped = {(table_of(c), c.field) for c in self._spec.groups}

        for name, item in self._spec.selects.items():
            if isinstance(item, Aggregate) or (table_of(item), item.field) in grouped:
                continue
            raise BadRequestError(
                f"`select({name}={item!r})`: sau `group_by` thì {item!r} phải nằm trong "
                f"`group_by`, hoặc phải bọc trong hàm gộp — mỗi nhóm có nhiều giá trị "
                f"{item.field!r} khác nhau. Ví dụ: `group_by({item!r})` hay "
                f"`{name}=max_({item!r})`."
            )

        for inc in self._spec.includes:
            if (_alias_of(self._spec.entity), inc.root_field) in grouped:
                continue
            raise BadRequestError(
                f"`include({inc.entity.__name__})` cần cột ghép {inc.root_field!r} nằm "
                f"trong `group_by` — nếu không thì một nhóm có thể trải trên nhiều "
                f"{inc.entity.__name__}, lấy cái nào cũng là bịa. Thêm "
                f"`group_by(\"{inc.root_field}\")`, hoặc bỏ `include`."
            )

    def _resolve_nest(self) -> tuple[list[NestLevel], list[NestLink], list[Include], int]:
        """Chuỗi `nest_under(...)` -> các lớp + mắt xích, và phần `include` còn lại.

        `include(X, fields=…, name=…)` khai X TRẢ VỀ NHỮNG CỘT NÀO;
        `nest_under(...)` khai THỨ TỰ. Bảng nào có mặt trong chuỗi thì lấy cột
        từ `include` của nó rồi bỏ include đó đi — X chỉ hiện một lần, đúng chỗ
        chuỗi xếp cho nó.
        """
        includes = list(self._spec.includes)
        if not self._spec.nest:
            return [], [], includes, -1

        root = self._spec.entity
        chain = list(self._spec.nest)
        if self._spec.nest_link is not None or root not in chain:
            # Bảng tự trỏ về chính nó thì bảng gốc vẫn là một lớp RIÊNG, dù trùng
            # tên với lớp ngoài — nên nhớ VỊ TRÍ chứ không tìm lại bằng tên.
            chain.append(root)
            root_idx = len(chain) - 1
        else:
            root_idx = chain.index(root)

        if self._spec.nest_link is not None:
            links = [self._spec.nest_link]
        else:
            links = [_link_between(chain[i], chain[i + 1]) for i in range(len(chain) - 1)]

        levels: list[NestLevel] = []
        for i, entity in enumerate(chain):
            own = [x for x in includes if x.entity is entity]
            from_nest = self._spec.nest_cols.get(entity)
            if len(own) > 1 or (own and from_nest is not None):
                raise BadRequestError(
                    f"Cột của {entity.__name__} đang khai ở HAI chỗ — `include(...)` và "
                    f"`nest_under(...)`. Bỏ một trong hai."
                )
            inc = own[0] if own else None
            if inc is not None:
                if inc.conditions or inc.orders:
                    raise BadRequestError(
                        f"`include({entity.__name__}, where=…/order_by_…=…)` không "
                        f"dùng được khi {entity.__name__} nằm trong `nest_under(...)`: "
                        f"ở đó nó lấy đúng bản ghi liên quan của từng nhóm, không lọc "
                        f"hay sắp được ở lớp NGOÀI. Bỏ `where=`/`order_by_*=` đi."
                    )
                includes.remove(inc)
            default_name = _default_field_name(entity, i == 0 or links[i - 1].inner_is_list)
            column_name = from_nest if from_nest is not None else (
                inc.fields if inc is not None else _fields_of(entity, (), ())
            )
            levels.append(NestLevel(
                entity=entity,
                name=self._spec.nest_names.get(entity)
                     or (inc.name if inc is not None else "")
                     or default_name,
                fields=column_name,
            ))
        return levels, links, includes, root_idx

    async def all(self) -> list[Any]:
        """Chạy và trả về mọi dòng khớp."""
        self._need_select()
        self._check_grouped()
        levels, links, includes, root_idx = self._resolve_nest()
        for inc in includes:
            if inc.defer_error:
                # Đứng ngoài chuỗi `nest_under` mà không có khoá ngoại trực
                # tiếp — giờ mới chắc chắn là lỗi.
                raise BadRequestError(inc.defer_error)
        if not includes and not levels:
            return await self._backend().run_query(self._spec)

        # `order_by_*(Cam.name)` khi Cam là MỘT LỚP của chuỗi lồng nhau: cột đó
        # không nằm trong câu lệnh của bảng gốc (trừ khi đã `join`), nên phải
        # tách ra và xếp ở đúng lớp — để nguyên thì sqlite ném lỗi "bảng chưa
        # có trong truy vấn" còn memory lặng lẽ bỏ qua, tệ cả hai.
        level_orders: dict[int, list[Order]] = {}
        if levels:
            known = {_alias_of(self._spec.entity), *(j.alias for j in self._spec.joins)}
            kept: list[Order] = []
            for order in self._spec.orders:
                alias = order.column if isinstance(order.column, Aggregate) else None
                if alias is not None or table_of(order.column) in known                         or isinstance(order.column, str):
                    kept.append(order)
                    continue
                idx = next((i for i, lv in enumerate(levels)
                            if _alias_of(lv.entity) == table_of(order.column)), None)
                if idx is None:
                    raise BadRequestError(
                        f"Cột {order.column!r} không thuộc bảng gốc, bảng đã "
                        f"`join`, hay lớp nào trong `nest_under(...)`."
                    )
                level_orders.setdefault(idx, []).append(order)
            self._spec.orders = kept

        # Cột dùng để ghép phải có trong kết quả mới ghép được. Người dùng
        # không xin thì tự thêm rồi bỏ đi lúc trả về.
        needed = [inc.root_field for inc in includes]
        if root_idx > 0:
            needed.append(links[root_idx - 1].inner_field)
        if 0 <= root_idx < len(levels) - 1:
            needed.append(links[root_idx].outer_field)
        added = {f for f in needed if self._spec.selects and f not in self._spec.selects}
        for name in added:
            self._spec.selects[name] = Column(self._spec.entity, name)
        try:
            rows = await self._backend().run_query(self._spec)
            rows = [_as_dict(self._spec.entity, row) for row in rows]
            for inc in includes:
                await self._attach(inc, rows)
        finally:
            for name in added:
                del self._spec.selects[name]
        # Dựng cây TRƯỚC rồi mới bỏ cột ghép: chính nó là cột dùng để ghép.
        if levels:
            tree = await self._nest(levels, links, root_idx, rows, level_orders)
            for row in rows:
                for name in added:
                    row.pop(name, None)
            return tree

        for row in rows:
            for name in added:
                row.pop(name, None)
        return rows

    async def _nest(
        self,
        levels: list[NestLevel],
        links: list[NestLink],
        root_idx: int,
        rows: list[dict[str, Any]],
        level_orders: dict[int, list[Order]] | None = None,
    ) -> list[dict[str, Any]]:
        """Lấy về từng lớp rồi ghép thành cây.

        Mỗi lớp đúng MỘT câu lệnh `WHERE khoá IN (...)`, đi từ lớp gốc ra hai
        phía. Không có câu nào chạy cho mỗi dòng.
        """
        # (giá trị các cột dùng để ghép, dict sẽ trả về)
        level_rows: list[list[tuple[dict[str, Any], dict[str, Any]]]] = [[] for _ in levels]
        level_rows[root_idx] = [(row, row) for row in rows]

        needed = _link_columns(levels, links)

        level_orders = level_orders or {}

        async def fetch_level(i: int, field: str, keys: list[Any]) -> None:
            objs = await self._fetch_in(levels[i].entity, field, keys)
            # Sắp nhiều khoá bằng nhiều lượt sort ổn định, khoá phụ trước:
            # cùng cách backend memory làm, để None đứng cùng một chỗ.
            for order in reversed(level_orders.get(i, [])):
                objs.sort(
                    key=lambda o, c=order.column.field: (
                        (v := bind_value(getattr(o, c, None))) is not None,
                        v if v is not None else 0),
                    reverse=order.descending,
                )
            level_rows[i] = [
                ({c: getattr(o, c, None) for c in needed[i]},
                 {out_name: getattr(o, column_name, None) for out_name, column_name in levels[i].fields})
                for o in objs
            ]

        for i in range(root_idx + 1, len(levels)):           # đi vào TRONG
            link = links[i - 1]
            link_keys = _keys_in_order(level_rows[i - 1], link.outer_field)
            await fetch_level(i, link.inner_field, link_keys)
        for i in range(root_idx - 1, -1, -1):                # đi ra NGOÀI
            link = links[i]
            link_keys = _keys_in_order(level_rows[i + 1], link.inner_field)
            await fetch_level(i, link.outer_field, link_keys)
            if i in level_orders:
                continue        # người dùng đã sắp lớp này tường minh — tôn trọng
            # Lớp ngoài xếp theo thứ tự xuất hiện của lớp trong, không theo
            # thứ tự database trả về — nhờ vậy `order_by_*` vẫn còn tác dụng.
            position = {k: n for n, k in enumerate(link_keys)}
            level_rows[i].sort(key=lambda x, f=link.outer_field: position.get(x[0].get(f), 0))

        for i in range(len(levels) - 1, 0, -1):             # ghép từ TRONG ra
            link = links[i - 1]
            grouped: dict[Any, list[dict[str, Any]]] = {}
            for link_keys, out in level_rows[i]:
                grouped.setdefault(link_keys.get(link.inner_field), []).append(out)
            for link_keys, out in level_rows[i - 1]:
                children = grouped.get(link_keys.get(link.outer_field), [])
                out[levels[i].name] = children if link.inner_is_list else (children[0] if children else None)

        return [out for _, out in level_rows[0]]

    async def _fetch_in(self, entity: type, field: str, keys: list[Any]) -> list[Any]:
        """`SELECT ... WHERE field IN (keys)`, chia mẻ để không vượt giới hạn tham số."""
        found: list[Any] = []
        keys = sorted(keys, key=str)
        for i in range(0, len(keys), IN_CHUNK):
            child: Query[Any] = Query(entity, self._db)
            child._spec.conditions = [
                Compare(Column(entity, field), "in", keys[i:i + IN_CHUNK])
            ]
            found.extend(await child._backend().run_query(child._spec))
        return found

    async def _attach(self, inc: Include, rows: list[dict[str, Any]]) -> None:
        """Một câu lệnh cho cả mẻ: `WHERE khoá IN (...)`, rồi ghép bằng dict."""
        keys = {row.get(inc.root_field) for row in rows} - {None}
        found: list[Any] = []
        keys_list = sorted(keys, key=str)
        for i in range(0, len(keys_list), IN_CHUNK):
            child: Query[Any] = Query(inc.entity, self._db)
            child._spec.conditions = [
                Compare(Column(inc.entity, inc.other_field), "in", keys_list[i:i + IN_CHUNK]),
                *inc.conditions,
            ]
            child._spec.orders = list(inc.orders)
            found.extend(await child._backend().run_query(child._spec))

        by_key: dict[Any, Any] = {}
        for obj in found:
            key = getattr(obj, inc.other_field, None)
            shaped = {out_name: getattr(obj, column_name, None) for out_name, column_name in inc.fields}
            if inc.to_list:
                by_key.setdefault(key, []).append(shaped)
            else:
                by_key.setdefault(key, shaped)

        field_name = inc.name or _default_field_name(inc.entity, inc.to_list)
        for row in rows:
            key = row.get(inc.root_field)
            row[field_name] = by_key.get(key, [] if inc.to_list else None)

    async def first(self) -> Any | None:
        """Dòng đầu tiên, hoặc None. Tự đặt `LIMIT 1`."""
        keep = self._spec.limit
        self._spec.limit = 1
        try:
            rows = await self.all()
        finally:
            self._spec.limit = keep
        return rows[0] if rows else None

    async def one(self) -> Any:
        """Đúng một dòng, không có thì ném 404."""
        from fastapi_modular.core.exceptions import NotFoundError

        row = await self.first()
        if row is None:
            raise NotFoundError(f"Không tìm thấy {self._spec.entity.__name__}")
        return row

    async def count(self) -> int:
        """`SELECT count(*)` — không kéo dòng nào về."""
        return await self._backend().count_query(self._spec)

    async def exists(self) -> bool:
        return await self.first() is not None

    def sql(self) -> str:
        """Câu SQL sẽ chạy, kèm giá trị đã nhúng — để NHÌN, không phải để đoán.

        In nó ra khi truy vấn cho kết quả lạ; nhanh hơn đọc lại builder. Chỉ có
        ở backend SQL, vì `memory` không sinh SQL nào cả.
        """
        backend = self._backend()
        maker = getattr(backend, "query_sql", None)
        if maker is None:
            raise BadRequestError(
                f"Backend {backend.name!r} không sinh SQL nên không có gì để in. "
                "`.sql()` chỉ dùng được với sqlite/postgres."
            )
        return maker(self._spec)

    def _backend(self) -> Any:
        backend = self._db.backend
        if not hasattr(backend, "run_query"):
            raise BadRequestError(
                f"Backend {backend.name!r} chưa hỗ trợ query builder. "
                "Dùng được với: postgres, sqlite, memory."
            )
        return backend

    def __repr__(self) -> str:
        spec = self._spec
        return (
            f"<Query[{spec.entity.__name__}] joins={len(spec.joins)} "
            f"where={len(spec.conditions)} limit={spec.limit}>"
        )


def _default_field_name(entity: type, is_list: bool) -> str:
    """Tên trường mặc định: `events` cho danh sách, `camera` cho một object."""
    out_name = entity.__name__.lower()
    return f"{out_name}s" if is_list else out_name


def _link_between(outer: type, inner: type) -> NestLink:
    """Mắt xích giữa hai bảng cạnh nhau, đọc từ khoá ngoại đã khai."""
    if outer is inner:
        raise BadRequestError(f"`nest_under(...)` có {outer.__name__} hai lần.")

    outer_refs = [(f, r) for f, r in mapping_for(outer).references if r.target is inner]
    inner_refs = [(f, r) for f, r in mapping_for(inner).references if r.target is outer]

    if len(inner_refs) == 1 and not outer_refs:
        field, ref = inner_refs[0]
        return NestLink(outer, inner, ref.column, field, True)
    if len(outer_refs) == 1 and not inner_refs:
        field, ref = outer_refs[0]
        return NestLink(outer, inner, field, ref.column, False)
    if not outer_refs and not inner_refs:
        raise BadRequestError(
            f"`nest_under(..., {outer.__name__}, {inner.__name__}, ...)`: hai bảng này "
            f"không có khoá ngoại TRỰC TIẾP với nhau. Kể tên đủ các bảng trên đường "
            f"đi ({_suggest_chain(outer, inner)}), hoặc đổi thứ tự. Khung không tự "
            f"sắp lại giúp: sắp sai thì dữ liệu sai mà không ai thấy."
        )
    raise BadRequestError(
        f"`nest_under(..., {outer.__name__}, {inner.__name__}, ...)`: có nhiều khoá "
        f"ngoại giữa hai bảng nên không biết ghép theo cột nào."
    )


def _suggest_chain(a: type, b: type) -> str:
    """Tìm đường đi ngắn nhất giữa hai bảng theo khoá ngoại, để mách trong lỗi."""
    from collections import deque

    edges: dict[type, set[type]] = {}
    for entity in list(_registered_entities().values()):
        try:
            refs = mapping_for(entity).references
        except TypeError:
            continue
        for _, ref in refs:
            edges.setdefault(entity, set()).add(ref.target)
            edges.setdefault(ref.target, set()).add(entity)

    queue, visited = deque([[a]]), {a}
    while queue:
        path = queue.popleft()
        if path[-1] is b:
            return " -> ".join(x.__name__ for x in path)
        for neighbor in edges.get(path[-1], ()):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append([*path, neighbor])
    return f"không có đường nào nối {a.__name__} với {b.__name__}"


def _registered_entities() -> dict[str, type]:
    from fastapi_modular.core.container import _ENTITIES

    return _ENTITIES


def _link_columns(levels: list[NestLevel], links: list[NestLink]) -> list[set[str]]:
    """Mỗi lớp cần giữ lại những cột nào để ghép được với hai lớp cạnh nó."""
    needed: list[set[str]] = [set() for _ in levels]
    for i, link in enumerate(links):
        needed[i].add(link.outer_field)
        needed[i + 1].add(link.inner_field)
    return needed


def _keys_in_order(level_pairs: list[tuple[dict[str, Any], dict[str, Any]]], field: str) -> list[Any]:
    """Giá trị khoá theo thứ tự xuất hiện, bỏ trùng và bỏ None."""
    out: dict[Any, None] = {}
    for link_keys, _ in level_pairs:
        gia_tri = link_keys.get(field)
        if gia_tri is not None:
            out[gia_tri] = None
    return list(out)


def _as_dict(entity: type, row: Any) -> dict[str, Any]:
    """Dòng kết quả -> dict. `.select()` đã cho dict rồi thì giữ nguyên."""
    if isinstance(row, dict):
        return row
    return {name: getattr(row, name, None) for name in mapping_for(entity).fields}


def _orders_of(entity: type, fields: Any, descending: bool) -> list[Order]:
    """Tên cột / cột thật / hàm gộp -> `Order`. Chiều do người gọi quyết định.

    Không có quy ước dấu trừ nào ở đây: chiều nằm trong TÊN HÀM
    (`order_by_desc`), không nằm trong dữ liệu.
    """
    if fields is None:
        return []
    items = fields if isinstance(fields, (list, tuple)) else [fields]
    out: list[Order] = []
    for item in items:
        if isinstance(item, str):
            out.append(Order(Column(entity, item), descending=descending))
        elif isinstance(item, Aggregate):
            out.append(Order(item, descending=descending))
        else:
            out.append(Order(column_of(item), descending=descending))
    return out


def _compose(branches: list[list[Condition]]) -> list[Condition]:
    """Các nhánh -> danh sách điều kiện mà backend hiểu (nối ngầm bằng AND).

    Một nhánh thì trả nguyên danh sách chứ không gói thêm một tầng `OR` một
    phần tử. Chỉ để `spec` đọc cho dễ khi soi lỗi — câu SQL thì giống hệt, đã
    thử: SQLAlchemy dẹp luôn `or_()` một vế.

    Nhiều nhánh thì gói thành đúng một `OR` của các `AND`.
    """
    kept = [b for b in branches if b]
    if not kept:
        return []
    if len(kept) == 1:
        return list(kept[0])
    return [Group("or", tuple(b[0] if len(b) == 1 else Group("and", tuple(b)) for b in kept))]


# ------------------------------------------------- tính bằng Python (memory)
# Khoá giữ các dòng thành viên của một nhóm trong dòng đã gộp. Không đụng tên
# bảng nào được vì alias luôn là định danh Python hợp lệ.
BUCKET_KEY = "*"


def evaluate(condition: Condition, rows: dict[str, Any]) -> bool:
    """Chạy một điều kiện trên một dòng đã ghép. `rows` là {alias: entity}."""
    if isinstance(condition, Group):
        results = (evaluate(part, rows) for part in condition.parts)
        return all(results) if condition.op == "and" else any(results)
    if isinstance(condition, Not):
        return not evaluate(condition.part, rows)
    if not isinstance(condition, Compare):
        raise BadRequestError(f"Điều kiện lạ: {condition!r}")

    left = bind_value(_value_of(condition.column, rows))
    right = condition.value
    if isinstance(right, Column):
        right = _value_of(right, rows)
    right = bind_value(right)

    op = condition.op
    if op == "isnull":
        return (left is None) is bool(right)
    if left is None:
        # SQL: mọi so sánh với NULL đều không đúng. Giữ y hệt để hai backend
        # cho cùng kết quả — đây là chỗ dễ lệch nhất giữa memory và SQL.
        return False
    if op == "eq":
        return bool(left == right)
    if op == "ne":
        return bool(left != right)
    if op == "gt":
        return bool(left > right)
    if op == "gte":
        return bool(left >= right)
    if op == "lt":
        return bool(left < right)
    if op == "lte":
        return bool(left <= right)
    if op == "in":
        return left in right
    if op == "nin":
        return left not in right
    if op == "between":
        return bool(right[0] <= left <= right[1])
    if op in {"like", "ilike"}:
        return _like(str(left), str(right), fold=op == "ilike")
    raise BadRequestError(f"Toán tử {op!r} chưa cài cho backend memory")


def _value_of(column: Any, rows: dict[str, Any]) -> Any:
    if isinstance(column, Aggregate):
        return aggregate_of(column, rows.get(BUCKET_KEY) or [])
    obj = rows.get(table_of(column))
    return None if obj is None else getattr(obj, column.field, None)


def aggregate_of(item: Aggregate, bucket: list[dict[str, Any]]) -> Any:
    """Tính một hàm gộp trên một nhóm, cho backend memory.

    Bám đúng luật của SQL để hai backend không lệch: `count()` đếm dòng,
    `count(cot)` BỎ QUA NULL, `sum`/`avg`/`min`/`max` cũng bỏ qua NULL và trả
    về NULL khi nhóm rỗng — chứ không phải 0.
    """
    if item.func == "count" and item.column is None:
        return len(bucket)

    values = [_value_of(item.column, row) for row in bucket]
    values = [v for v in values if v is not None]
    if item.distinct:
        values = list(dict.fromkeys(values))
    if item.func == "count":
        return len(values)
    if not values:
        return None
    if item.func == "sum":
        return sum(values)
    if item.func == "avg":
        return sum(values) / len(values)
    if item.func == "min":
        return min(values)
    return max(values)


def _like(text: str, pattern: str, *, fold: bool) -> bool:
    """`%` và `_` của SQL LIKE, dịch sang regex."""
    import re

    if fold:
        text, pattern = text.lower(), pattern.lower()
    regex = "".join(
        ".*" if ch == "%" else "." if ch == "_" else re.escape(ch) for ch in pattern
    )
    return re.fullmatch(regex, text) is not None


def sort_key(orders: Sequence[Order]) -> Any:
    """Khoá sắp xếp cho backend memory, chịu được None."""

    def key(rows: dict[str, Any]) -> tuple:
        parts = []
        for order in orders:
            value = bind_value(_value_of(order.column, rows))
            # None xếp trước, giống NULLS FIRST mặc định của SQLite/Postgres ASC.
            parts.append((value is not None, value if value is not None else 0))
        return tuple(parts)

    return key


__all__ = [
    "JOIN_KINDS",
    "Aggregate",
    "Column",
    "Condition",
    "F",
    "Include",
    "Join",
    "NestLevel",
    "NestLink",
    "Order",
    "Query",
    "QuerySpec",
    "and_",
    "avg",
    "between",
    "count",
    "evaluate",
    "ilike",
    "in_",
    "is_not_null",
    "is_null",
    "like",
    "max_",
    "min_",
    "not_",
    "not_in",
    "or_",
    "sort_key",
    "sum_",
]
