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
from fastapi_modular.infrastructure.database.base import mapping_for

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


@dataclasses.dataclass(frozen=True, slots=True)
class Join:
    entity: type
    alias: str
    on: Condition
    outer: bool = False
    full: bool = False


def _alias_of(entity: type) -> str:
    """`Camera` -> `camera`. Đây là tiền tố dùng trong kwargs: `camera__name`."""
    return entity.__name__.lower()


def table_of(column: Column) -> str:
    """Tên bảng mà một cột trỏ tới trong truy vấn — alias nếu có, không thì mặc định.

    Backend tra bảng bằng CHUỖI này chứ không bằng `column.entity`: nối bảng với
    chính nó thì hai cột cùng entity mà là hai bảng khác nhau.
    """
    return column.alias or _alias_of(column.entity)


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

    __slots__ = ("_db", "_spec")

    def __init__(self, entity: type[E], database: Database) -> None:
        self._db = database
        self._spec = QuerySpec(entity=entity)

    # ------------------------------------------------------------- dựng
    def join(
        self,
        entity: type,
        *,
        on: Any = _INFER,
        outer: bool = False,
        full: bool = False,
        alias: str = "",
    ) -> Query[E]:
        """Nối thêm một bảng để LỌC theo cột của nó.

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

        Bốn kiểu nối:

            .join(Camera)                       # INNER: chỉ dòng khớp cả hai bên
            .join(Camera, outer=True)           # LEFT : giữ cả dòng không có bên phải
            .join(Camera, full=True)            # FULL : giữ cả hai bên, cần `.select(...)`
            .join(Camera, alias="cha", on=...)  # nối bảng với CHÍNH NÓ

        `RIGHT JOIN` không có, và không thiếu: builder trả về entity của bảng
        GỐC, nên "RIGHT JOIN" chỉ là đổi bảng nào làm gốc rồi dùng `outer=True`
        — `cameras.query().join(Event, outer=True)` thay cho
        `events.query().join(Camera, right=True)`. Bảng gốc cũng chính là thứ
        bạn muốn nhận về, nên chọn nó là bước bạn phải làm dù thế nào.
        """
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
            Join(
                entity=entity,
                alias=name,
                on=self._on_condition(entity, on, rieng),
                outer=outer or full,
                full=full,
            )
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
        self._spec.conditions.extend(as_condition(c) for c in conditions)
        for key, value in lookups.items():
            self._spec.conditions.append(self._parse_lookup(key, value))
        return self

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

    def order_by(self, *fields: Any) -> Query[E]:
        """`order_by("-created_at")` — dấu trừ là giảm dần.

        Nhận cả `Event.created_at`, `F(Event).created_at.desc()` khi cần sắp
        theo cột của bảng đã join.
        """
        for item in fields:
            if isinstance(item, Order):
                self._spec.orders.append(item)
            elif isinstance(item, str):
                descending = item.startswith("-")
                name = item.lstrip("-+")
                self._spec.orders.append(
                    Order(Column(self._spec.entity, name), descending=descending)
                )
            elif isinstance(item, Aggregate):
                self._spec.orders.append(Order(item))
            else:
                self._spec.orders.append(Order(column_of(item)))
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
        self._spec.havings.extend(as_condition(c) for c in conditions)
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

    def select(self, *fields: Any, **renamed: Any) -> Query[E]:
        """Đổi kiểu trả về sang `list[dict]` để lấy được cột của bảng đã join.

            query = repo.query().join(Camera).select("id", "score", camera_name=Camera.name)
            rows = await query.all()
            # [{"id": ..., "score": ..., "camera_name": "Cổng chính"}]

        Không gọi `select` thì trả về `list[Event]` như `find()`.
        """
        for item in fields:
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
        if any(j.full for j in self._spec.joins):
            raise BadRequestError(
                "`full=True` phải đi với `.select(...)`: FULL JOIN sinh cả những "
                f"dòng KHÔNG có bản ghi {self._spec.entity.__name__} nào. Ví dụ: "
                ".select(ma=..., ten=...)"
            )

    async def all(self) -> list[Any]:
        """Chạy và trả về mọi dòng khớp."""
        self._need_select()
        return await self._backend().run_query(self._spec)

    async def first(self) -> Any | None:
        """Dòng đầu tiên, hoặc None. Tự đặt `LIMIT 1`."""
        self._need_select()
        keep = self._spec.limit
        self._spec.limit = 1
        try:
            rows = await self._backend().run_query(self._spec)
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

    left = _value_of(condition.column, rows)
    right = condition.value
    if isinstance(right, Column):
        right = _value_of(right, rows)

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
            value = _value_of(order.column, rows)
            # None xếp trước, giống NULLS FIRST mặc định của SQLite/Postgres ASC.
            parts.append((value is not None, value if value is not None else 0))
        return tuple(parts)

    return key


__all__ = [
    "Aggregate",
    "Column",
    "Condition",
    "F",
    "Join",
    "Order",
    "Query",
    "QuerySpec",
    "and_",
    "avg",
    "count",
    "evaluate",
    "max_",
    "min_",
    "not_",
    "or_",
    "sort_key",
    "sum_",
]
