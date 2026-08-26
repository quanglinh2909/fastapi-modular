"""Backend lưu trong RAM — mặc định của template, không cần cài gì thêm.

Chỉ hợp cho phát triển và test: dữ liệu mất khi restart, và mỗi worker giữ
một bản riêng nên KHÔNG được chạy nhiều worker với backend này.
"""

from __future__ import annotations

import uuid
from typing import Any, TypeVar

from fastapi_modular.core.container import _ENTITIES
from fastapi_modular.core.exceptions import ConflictError
from fastapi_modular.infrastructure.database.base import (
    DatabaseBackend,
    DuplicateKeyViolation,
    Filters,
    Match,
    active_filters,
    default_of,
    mapping_for,
    matches,
)
from fastapi_modular.infrastructure.database.query import BUCKET_KEY as _BUCKET

E = TypeVar("E")


class MemoryBackend(DatabaseBackend):
    name = "memory"

    def __init__(self) -> None:
        self._tables: dict[str, dict[str, Any]] = {}

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        self._tables.clear()

    async def ping(self) -> bool:
        return True

    def _table(self, entity: type) -> dict[str, Any]:
        return self._tables.setdefault(mapping_for(entity).storage, {})

    def _select(self, entity: type, filters: Filters, match: Match) -> list[Any]:
        active = active_filters(filters)
        return [o for o in self._table(entity).values() if matches(o, active, match)]

    async def get(self, entity: type[E], id_: str) -> E | None:
        return self._table(entity).get(id_)

    async def find(
        self,
        entity: type[E],
        *,
        filters: Filters,
        match: Match = None,
        order_by: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[E]:
        rows = self._select(entity, filters, match)
        if order_by:
            rows.sort(key=lambda o: getattr(o, order_by, 0))
        rows = rows[offset:]
        return rows[:limit] if limit is not None else rows

    async def find_one(
        self, entity: type[E], *, filters: Filters, match: Match = None
    ) -> E | None:
        return next(iter(self._select(entity, filters, match)), None)

    async def count(
        self, entity: type[E], *, filters: Filters, match: Match = None
    ) -> int:
        return len(self._select(entity, filters, match))

    # -------------------------------------------------------------- builder
    def _rows_for(self, spec: Any) -> list[dict[str, Any]]:
        """Ghép bảng bằng Python, ra danh sách {tên bảng: entity}.

        Vì sao backend memory cũng phải làm được: `fam test` mặc định chạy trên
        đây. Không có nó thì mọi code dùng query builder chỉ test được khi có
        database thật, và đó là cách nhanh nhất để người ta thôi viết test.

        Đây là vòng lặp lồng nhau, cỡ O(n×m) — đúng cho vài nghìn dòng trong
        test, không phải cho production. Production dùng SQL thật.
        """
        from fastapi_modular.infrastructure.database.query import evaluate, sort_key

        root_alias = spec.entity.__name__.lower()
        aliases = [root_alias]
        rows: list[dict[str, Any]] = [
            {root_alias: obj} for obj in self._table(spec.entity).values()
        ]

        for join in spec.joins:
            others = list(self._table(join.entity).values())
            da_khop: set[int] = set()
            ghep: list[dict[str, Any]] = []
            for row in rows:
                khop = [
                    (i, o) for i, o in enumerate(others)
                    if evaluate(join.on, {**row, join.alias: o})
                ]
                if khop:
                    da_khop.update(i for i, _ in khop)
                    ghep.extend({**row, join.alias: o} for _, o in khop)
                elif join.outer:
                    ghep.append({**row, join.alias: None})
            if join.full:
                # FULL JOIN: thêm nốt những dòng bên PHẢI không khớp ai, với
                # toàn bộ bên trái để trống.
                trong = dict.fromkeys(aliases)
                ghep.extend(
                    {**trong, join.alias: o}
                    for i, o in enumerate(others) if i not in da_khop
                )
            aliases.append(join.alias)
            rows = ghep

        for condition in spec.conditions:
            rows = [row for row in rows if evaluate(condition, row)]

        if spec.groups:
            rows = _group(spec, rows)
        elif _co_ham_gop(spec):
            # `select(so=count())` không kèm `group_by` = gộp CẢ BẢNG thành một
            # nhóm, y như SQL. Bảng rỗng vẫn ra một dòng (`count` = 0).
            rows = [{**(rows[0] if rows else {}), _BUCKET: rows}]
        for condition in spec.havings:
            rows = [row for row in rows if evaluate(condition, row)]

        if spec.orders:
            rows.sort(key=sort_key(spec.orders))
            # Sắp nhiều cột với chiều khác nhau: sắp từ cột PHỤ tới cột CHÍNH,
            # dựa vào tính ổn định của sorted() trong Python.
            for order in reversed(spec.orders):
                rows.sort(key=sort_key([order]), reverse=order.descending)
        return rows

    async def run_query(self, spec: Any) -> list[Any]:
        rows = self._rows_for(spec)
        root_alias = spec.entity.__name__.lower()

        if spec.distinct:
            seen, unique = set(), []
            for row in rows:
                key = id(row[root_alias]) if not spec.selects else tuple(
                    _read(row, column) for column in spec.selects.values()
                )
                if key not in seen:
                    seen.add(key)
                    unique.append(row)
            rows = unique

        rows = rows[spec.offset:]
        if spec.limit is not None:
            rows = rows[: spec.limit]

        if spec.selects:
            return [
                {name: _read(row, column) for name, column in spec.selects.items()}
                for row in rows
            ]
        return [row[root_alias] for row in rows]

    async def count_query(self, spec: Any) -> int:
        # Có `group_by` thì đếm SỐ NHÓM, giống `SELECT count(*) FROM (...)`.
        return len(self._rows_for(spec))

    async def save(self, entity: type[E], obj: E) -> E:
        self._check_unique(entity, obj)
        self._check_references(entity, obj)
        if not getattr(obj, "id", None):
            obj.id = uuid.uuid4().hex  # type: ignore[attr-defined]
        self._table(entity)[obj.id] = obj  # type: ignore[attr-defined]
        return obj

    def _check_unique(self, entity: type, obj: Any) -> None:
        """Bắt chước ràng buộc unique của database thật.

        Không có phần này thì backend memory sẽ cho ghi trùng, và test chạy
        trên memory sẽ không bắt được lỗi mà production gặp phải.
        """
        mapping = mapping_for(entity)
        if not mapping.unique:
            return

        own_id = getattr(obj, "id", None)
        for columns in mapping.unique:
            values = tuple(getattr(obj, column, None) for column in columns)
            for other in self._table(entity).values():
                if getattr(other, "id", None) == own_id:
                    continue
                if tuple(getattr(other, c, None) for c in columns) == values:
                    raise DuplicateKeyViolation(mapping.storage, columns, values)

    def _check_references(self, entity: type, obj: Any) -> None:
        """Bắt chước ràng buộc khoá ngoại LÚC GHI: cha phải có thật.

        SQL thật ném `FOREIGN KEY constraint failed` khi ghi một `camera_id`
        không trỏ tới camera nào. Không bắt chước thì backend memory cho ghi
        thoải mái, `fam test` xanh, và production đổ đúng chỗ đó.
        """
        for column, ref in mapping_for(entity).references:
            value = getattr(obj, column, None)
            if value is None:                      # NULL nghĩa là "chưa gắn", hợp lệ
                continue
            if value not in self._table(ref.target):
                raise ConflictError(
                    f"{entity.__name__}.{column} = {value!r} nhưng không có "
                    f"{ref.target.__name__} nào mang id đó."
                )

    # ------------------------------------------------------------ khoá ngoại
    def _children_of(self, parent: type) -> list[tuple[type, str, Any]]:
        """Mọi entity ĐANG BIẾT có cột trỏ tới `parent`.

        "Đang biết" = đã có bản ghi trong backend này. Đủ cho test; SQL thật
        thì chính database giữ danh sách này nên không có giới hạn đó.
        """
        found = []
        for child in list(_ENTITIES.values()):
            for column, ref in mapping_for(child).references:
                if ref.target is parent:
                    found.append((child, column, ref))
        return found

    async def _cascade(self, parent: type, ids: list[str]) -> None:
        """Áp `on_delete` cho mọi bản ghi con, trước khi xoá cha.

        SQL thật thì database làm việc này. Ở đây khung làm, để `fam test`
        (chạy trên memory) cho cùng kết quả với production.
        """
        if not ids:
            return
        for child, column, ref in self._children_of(parent):
            table = self._table(child)
            con = [o for o in table.values() if getattr(o, column, None) in ids]
            if not con:
                continue

            if ref.on_delete == "CASCADE":
                await self.delete_where_ids(child, [o.id for o in con])
            elif ref.on_delete == "SET NULL":
                for obj in con:
                    setattr(obj, column, None)
            elif ref.on_delete == "SET DEFAULT":
                value = default_of(child, column)
                for obj in con:
                    setattr(obj, column, value)
            else:                                   # RESTRICT / NO ACTION
                raise ConflictError(
                    f"Không xoá được {parent.__name__} vì còn {len(con)} "
                    f"{child.__name__} trỏ tới nó ({column}). Xoá chúng trước, "
                    f'hoặc khai on_delete="CASCADE"/"SET NULL".'
                )

    async def delete_where_ids(self, entity: type, ids: list[str]) -> int:
        """Xoá theo danh sách id, có áp khoá ngoại. Dùng nội bộ cho cascade."""
        await self._cascade(entity, ids)
        table = self._table(entity)
        removed = 0
        for id_ in ids:
            removed += int(table.pop(id_, None) is not None)
        return removed

    async def delete(self, entity: type[E], id_: str) -> bool:
        if id_ not in self._table(entity):
            return False
        return await self.delete_where_ids(entity, [id_]) > 0

    async def delete_where(
        self, entity: type[E], *, filters: Filters, match: Match = None
    ) -> int:
        ids = [o.id for o in self._select(entity, filters, match)]
        return await self.delete_where_ids(entity, ids)


def _read(row: dict[str, Any], column: Any) -> Any:
    from fastapi_modular.infrastructure.database.query import _value_of

    return _value_of(column, row)


def _co_ham_gop(spec: Any) -> bool:
    from fastapi_modular.infrastructure.database.query import Aggregate

    return any(isinstance(x, Aggregate) for x in spec.selects.values()) or bool(spec.havings)


def _group(spec: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gộp dòng theo `spec.groups`, giữ nguyên thứ tự nhóm xuất hiện lần đầu.

    Dòng đại diện lấy từ bản ghi ĐẦU TIÊN của nhóm — cột không nằm trong
    `group_by` vì vậy có giá trị của bản ghi đó. PostgreSQL từ chối hẳn kiểu
    truy vấn này; SQLite thì cho, và cho đúng như vậy.
    """
    from fastapi_modular.infrastructure.database.query import BUCKET_KEY, _value_of

    nhom: dict[tuple, list[dict[str, Any]]] = {}
    for row in rows:
        key = tuple(_value_of(column, row) for column in spec.groups)
        nhom.setdefault(key, []).append(row)
    return [{**bucket[0], BUCKET_KEY: bucket} for bucket in nhom.values()]
