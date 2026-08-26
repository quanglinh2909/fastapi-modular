"""Backend lưu trong RAM — mặc định của template, không cần cài gì thêm.

Chỉ hợp cho phát triển và test: dữ liệu mất khi restart, và mỗi worker giữ
một bản riêng nên KHÔNG được chạy nhiều worker với backend này.
"""

from __future__ import annotations

import uuid
from typing import Any, TypeVar

from fastapi_modular.infrastructure.database.base import (
    DatabaseBackend,
    DuplicateKeyViolation,
    Filters,
    Match,
    active_filters,
    mapping_for,
    matches,
)

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
        rows: list[dict[str, Any]] = [
            {root_alias: obj} for obj in self._table(spec.entity).values()
        ]

        for join in spec.joins:
            others = list(self._table(join.entity).values())
            ghep: list[dict[str, Any]] = []
            for row in rows:
                khop = [o for o in others if evaluate(join.on, {**row, join.alias: o})]
                if khop:
                    ghep.extend({**row, join.alias: o} for o in khop)
                elif join.outer:
                    ghep.append({**row, join.alias: None})
            rows = ghep

        for condition in spec.conditions:
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
        return len(self._rows_for(spec))

    async def save(self, entity: type[E], obj: E) -> E:
        self._check_unique(entity, obj)
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

    async def delete(self, entity: type[E], id_: str) -> bool:
        return self._table(entity).pop(id_, None) is not None

    async def delete_where(
        self, entity: type[E], *, filters: Filters, match: Match = None
    ) -> int:
        table = self._table(entity)
        ids = [o.id for o in self._select(entity, filters, match)]
        for id_ in ids:
            del table[id_]
        return len(ids)


def _read(row: dict[str, Any], column: Any) -> Any:
    obj = row.get(column.entity.__name__.lower())
    return None if obj is None else getattr(obj, column.field, None)
