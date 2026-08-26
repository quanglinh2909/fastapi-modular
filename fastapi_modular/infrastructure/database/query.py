"""Query builder — JOIN, so sánh, NULL, và câu SQL THẬT chạy dưới database.

`repo.find()` chỉ so bằng (`=`). Khi cần hơn thế thì dùng cái này:

    events = await (
        repo.query()
        .join(Camera, on="camera_id")
        .where(score__gte=0.8, label="person")
        .where(deleted_at__isnull=True)
        .where(camera__name__like="Cổng%")
        .order_by("-created_at")
        .limit(20)
        .all()
    )

Kết quả là `list[Event]` như `find()` — JOIN ở đây để **lọc**, không đổi kiểu
trả về. Cần cột của bảng kia thì nói rõ bằng `.select(...)`.

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

    __slots__ = ("entity", "field")
    __hash__ = None                      # type: ignore[assignment]

    def __init__(self, entity: type, field: str) -> None:
        fields = mapping_for(entity).fields
        if field not in fields:
            raise BadRequestError(
                f"{entity.__name__} không có trường {field!r}. "
                f"Có: {', '.join(sorted(fields))}"
            )
        self.entity = entity
        self.field = field

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
        return f"{self.entity.__name__}.{self.field}"


class _Fields:
    """Cái `F(Event)` trả về. `__getattr__` dựng `Column` và kiểm tên ngay."""

    __slots__ = ("_entity",)

    def __init__(self, entity: type) -> None:
        self._entity = entity

    def __getattr__(self, name: str) -> Column:
        return Column(self._entity, name)

    def __repr__(self) -> str:
        return f"F({self._entity.__name__})"


def F(entity: type) -> Any:
    """Cổng vào các cột của một entity: `F(Event).score >= 0.8`.

    Trả về `Any` để type checker không cản `F(Event).score` — tên trường được
    kiểm lúc chạy, ngay khi dựng, và lỗi liệt kê đủ tên hợp lệ.
    """
    return _Fields(entity)


@dataclasses.dataclass(frozen=True, slots=True)
class Order:
    column: Column
    descending: bool = False


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
    column: Column
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


def and_(*parts: Condition) -> Condition:
    return Group("and", tuple(parts))


def or_(*parts: Condition) -> Condition:
    """`WHERE a OR b` — thứ mà kwargs không viết được.

        .where(or_(F(Event).score >= 0.8, F(Event).label == "fire"))
    """
    if not parts:
        raise BadRequestError("`or_()` cần ít nhất một điều kiện")
    return Group("or", tuple(parts))


def not_(part: Condition) -> Condition:
    return Not(part)


# ------------------------------------------------------------------- join
@dataclasses.dataclass(frozen=True, slots=True)
class Join:
    entity: type
    alias: str
    on: Condition
    outer: bool = False


def _alias_of(entity: type) -> str:
    """`Camera` -> `camera`. Đây là tiền tố dùng trong kwargs: `camera__name`."""
    return entity.__name__.lower()


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
    selects: dict[str, Column] = dataclasses.field(default_factory=dict)
    distinct: bool = False

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
        on: str | tuple[str, str] | Condition,
        outer: bool = False,
        alias: str = "",
    ) -> Query[E]:
        """Nối thêm một bảng để LỌC theo cột của nó.

            .join(Camera, on="camera_id")          # Event.camera_id = Camera.id
            .join(Camera, on=("camera_id", "id"))  # nói rõ cả hai vế
            .join(Camera, on=F(Event).camera_id == F(Camera).id)

        `outer=True` cho `LEFT JOIN` — giữ cả những dòng không có bên phải.
        Cần khi bạn muốn lọc kiểu "camera CHƯA có sự kiện nào".
        """
        name = alias or _alias_of(entity)
        if name in {_alias_of(self._spec.entity), *(j.alias for j in self._spec.joins)}:
            raise BadRequestError(
                f"Đã có bảng tên {name!r} trong truy vấn. Truyền `alias=` để đặt tên khác."
            )

        if isinstance(on, Condition):
            condition = on
        else:
            left, right = (on, "id") if isinstance(on, str) else on
            condition = Column(self._spec.entity, left) == Column(entity, right)

        self._spec.joins.append(Join(entity=entity, alias=name, on=condition, outer=outer))
        return self

    def where(self, *conditions: Condition, **lookups: Any) -> Query[E]:
        """Thêm điều kiện. Nhiều lời gọi `where` nối với nhau bằng AND.

            .where(score__gte=0.8, label="person")      # kiểu ngắn
            .where(or_(E.score >= 0.8, E.label == "fire"))   # kiểu đối tượng

        Đuôi `__<toán tử>`: `gt gte lt lte ne in nin like ilike startswith
        endswith contains isnull between`. Không có đuôi thì là so bằng.

        Tiền tố là tên bảng đã `join`: `camera__name__like="Cổng%"`.
        """
        self._spec.conditions.extend(conditions)
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
        else:
            entity, field = self._spec.entity, parts[0]

        if op in {"startswith", "endswith", "contains"}:
            pattern = {"startswith": f"{value}%", "endswith": f"%{value}",
                       "contains": f"%{value}%"}[op]
            return Compare(Column(entity, field), "like", pattern)
        return Compare(Column(entity, field), op, value)

    def order_by(self, *fields: str | Column | Order) -> Query[E]:
        """`order_by("-created_at")` — dấu trừ là giảm dần.

        Nhận cả `F(Event).created_at.desc()` khi cần sắp theo cột bảng đã join.
        """
        for item in fields:
            if isinstance(item, Order):
                self._spec.orders.append(item)
            elif isinstance(item, Column):
                self._spec.orders.append(Order(item))
            else:
                descending = item.startswith("-")
                name = item.lstrip("-+")
                self._spec.orders.append(
                    Order(Column(self._spec.entity, name), descending=descending)
                )
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

    def select(self, *fields: str | Column, **renamed: str | Column) -> Query[E]:
        """Đổi kiểu trả về sang `list[dict]` để lấy được cột của bảng đã join.

            rows = await (repo.query()
                .join(Camera, on="camera_id")
                .select("id", "score", camera_name=F(Camera).name)
                .all())
            # [{"id": ..., "score": ..., "camera_name": "Cổng chính"}]

        Không gọi `select` thì trả về `list[Event]` như `find()`.
        """
        for item in fields:
            column = item if isinstance(item, Column) else Column(self._spec.entity, item)
            self._spec.selects[column.field] = column
        for name, item in renamed.items():
            column = item if isinstance(item, Column) else Column(self._spec.entity, item)
            self._spec.selects[name] = column
        return self

    # ------------------------------------------------------------- chạy
    async def all(self) -> list[Any]:
        """Chạy và trả về mọi dòng khớp."""
        return await self._backend().run_query(self._spec)

    async def first(self) -> Any | None:
        """Dòng đầu tiên, hoặc None. Tự đặt `LIMIT 1`."""
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


def _value_of(column: Column, rows: dict[str, Any]) -> Any:
    obj = rows.get(_alias_of(column.entity))
    return None if obj is None else getattr(obj, column.field, None)


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
    "Column",
    "Condition",
    "F",
    "Join",
    "Order",
    "Query",
    "QuerySpec",
    "and_",
    "evaluate",
    "not_",
    "or_",
    "sort_key",
]
