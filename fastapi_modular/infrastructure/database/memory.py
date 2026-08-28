"""Backend lưu trong RAM — mặc định của template, không cần cài gì thêm.

Chỉ hợp cho phát triển và test: dữ liệu mất khi restart, và mỗi worker giữ
một bản riêng nên KHÔNG được chạy nhiều worker với backend này.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, TypeVar

from fastapi_modular.core.container import _ENTITIES, Scope, injectable
from fastapi_modular.core.exceptions import ConflictError
from fastapi_modular.infrastructure.database.base import (
    DatabaseBackend,
    DuplicateKeyViolation,
    Filters,
    Match,
    RollbackRequested,
    Transaction,
    active_filters,
    coerce_value,
    default_of,
    mapping_for,
    matches,
)
from fastapi_modular.infrastructure.database.query import BUCKET_KEY as _BUCKET

E = TypeVar("E")


@injectable(scope=Scope.REQUEST)
class MemoryUnitOfWork:
    """Chụp ảnh dữ liệu ở lần GHI đầu tiên của request, trả lại nếu request hỏng.

    Có nó để backend memory không nói dối: với sqlite/postgres, handler ném
    exception là cả request ROLLBACK. Không làm gì tương tự ở đây thì một test
    kiểu "request hỏng thì không được ghi gì" sẽ đỏ trên `fam test` (memory)
    trong khi production (postgres) chạy đúng — và người ta sẽ học được rằng
    lời hứa rollback là không đáng tin.
    """

    def __init__(self) -> None:
        self._backend: MemoryBackend | None = None
        self._snapshot: dict[str, dict[str, Any]] | None = None

    def join(self, backend: MemoryBackend) -> None:
        if self._snapshot is None:
            self._backend = backend
            self._snapshot = backend._copy_tables()

    async def on_request_end(self, error: BaseException | None) -> None:
        if error is not None and self._backend is not None and self._snapshot is not None:
            self._backend._restore_tables(self._snapshot)
        self._backend, self._snapshot = None, None


class MemoryBackend(DatabaseBackend):
    name = "memory"

    def __init__(self) -> None:
        self._tables: dict[str, dict[str, Any]] = {}
        self._tx_lock = asyncio.Lock()
        # Độ sâu transaction CỦA TASK NÀY — để khối lồng nhau không tự khoá mình.
        self._tx_depth: ContextVar[int] = ContextVar(f"memory_tx_depth_{id(self)}", default=0)

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        self._tables.clear()

    async def ping(self) -> bool:
        return True

    def _table(self, entity: type) -> dict[str, Any]:
        return self._tables.setdefault(mapping_for(entity).storage, {})

    def _copy_tables(self) -> dict[str, dict[str, Any]]:
        """Bản sao NÔNG của từng bản ghi là đủ: trường entity đều là scalar,
        datetime hoặc Enum — không sửa tại chỗ được."""
        import copy

        return {
            storage_name: {record_id: copy.copy(obj) for record_id, obj in records.items()}
            for storage_name, records in self._tables.items()
        }

    def _restore_tables(self, snapshot: dict[str, dict[str, Any]]) -> None:
        self._tables.clear()
        self._tables.update(snapshot)

    def _join_request(self) -> None:
        """Nối vào transaction của request đang chạy, nếu đang ở trong request."""
        from fastapi_modular.core.container import container

        try:
            uow = container.resolve(MemoryUnitOfWork)
        except RuntimeError:
            return          # ngoài request: mỗi thao tác tự đứng một mình
        uow.join(self)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Transaction]:
        """Chụp ảnh dữ liệu lúc vào, có lỗi thì trả lại y như cũ.

        Backend memory không có transaction thật, nhưng nếu để nó KHÔNG rollback
        thì một test kiểu "hỏng giữa chừng thì không được ghi gì" sẽ đỏ ở memory
        trong khi production chạy đúng — `fam test` nói dối theo hướng tệ nhất.

        Chụp cả bản sao NÔNG của từng bản ghi, vì `save()` một object đã sửa
        thẳng tại chỗ thì khôi phục dict thôi không đủ. Nông là đủ: trường của
        entity đều là scalar/datetime/Enum, không sửa tại chỗ được.

        Lồng nhau thì khối trong tự chụp ảnh riêng, nên nó huỷ được phần của
        mình mà không đụng phần ngoài — giống SAVEPOINT của SQL.

        Hai task ĐỒNG THỜI thì xếp hàng qua một khoá. Không có khoá thì ảnh
        chụp là của CẢ kho: task A rollback sẽ trả kho về trước lúc A vào — xoá
        luôn thứ task B đã commit trong lúc đó. Đo được: hai task ghi song
        song, một task rollback, bản ghi ĐÃ COMMIT của task kia biến mất trên
        memory trong khi sqlite giữ nguyên. Giá phải trả là transaction memory
        chạy tuần tự — chấp nhận được cho backend dùng để test. Lưu ý một kẽ
        hở nhỏ: task SINH RA bên trong một transaction thừa hưởng độ sâu qua
        ContextVar nên không xếp hàng — đừng spawn task ghi dữ liệu từ trong
        transaction.
        """
        depth = self._tx_depth.get()
        if depth == 0:
            await self._tx_lock.acquire()
        token = self._tx_depth.set(depth + 1)
        snapshot = self._copy_tables()
        tx = Transaction()
        try:
            yield tx
        except RollbackRequested:
            self._restore_tables(snapshot)        # tx.rollback(): huỷ, không ném tiếp
        except BaseException:
            self._restore_tables(snapshot)
            raise
        finally:
            self._tx_depth.reset(token)
            if depth == 0:
                self._tx_lock.release()

    def _select(self, entity: type, filters: Filters, match: Match) -> list[Any]:
        active = active_filters(filters, entity)
        return [obj for obj in self._table(entity).values() if matches(obj, active, match)]

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
            rows.sort(key=lambda obj: getattr(obj, order_by, 0))
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
            was_matched: set[int] = set()
            merged: list[dict[str, Any]] = []
            for row in rows:
                matched = [
                    (i, obj) for i, obj in enumerate(others)
                    if evaluate(join.on, {**row, join.alias: obj})
                ]
                if matched:
                    was_matched.update(i for i, _ in matched)
                    merged.extend({**row, join.alias: obj} for _, obj in matched)
                elif join.keeps_left:
                    merged.append({**row, join.alias: None})
            if join.keeps_right:
                # RIGHT/FULL: thêm nốt những dòng bên PHẢI không khớp ai, với
                # toàn bộ bên trái để trống.
                empty_row = dict.fromkeys(aliases)
                merged.extend(
                    {**empty_row, join.alias: obj}
                    for i, obj in enumerate(others) if i not in was_matched
                )
            aliases.append(join.alias)
            rows = merged

        for condition in spec.conditions:
            rows = [row for row in rows if evaluate(condition, row)]

        if spec.groups:
            rows = _group(spec, rows)
        elif _has_aggregate(spec):
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
        self._join_request()
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
            children = [obj for obj in table.values() if getattr(obj, column, None) in ids]
            if not children:
                continue

            if ref.on_delete == "CASCADE":
                await self.delete_where_ids(child, [obj.id for obj in children])
            elif ref.on_delete == "SET NULL":
                for obj in children:
                    setattr(obj, column, None)
            elif ref.on_delete == "SET DEFAULT":
                value = default_of(child, column)
                for obj in children:
                    setattr(obj, column, value)
            else:                                   # RESTRICT / NO ACTION
                raise ConflictError(
                    f"Không xoá được {parent.__name__} vì còn {len(children)} "
                    f"{child.__name__} trỏ tới nó ({column}). Xoá chúng trước, "
                    f'hoặc khai on_delete="CASCADE"/"SET NULL".'
                )

    async def delete_where_ids(self, entity: type, ids: list[str]) -> int:
        """Xoá theo danh sách id, có áp khoá ngoại. Dùng nội bộ cho cascade.

        Mọi đường xoá đều đi qua đây, nên chỉ cần nối vào transaction ở đây.
        """
        self._join_request()
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
        ids = [obj.id for obj in self._select(entity, filters, match)]
        return await self.delete_where_ids(entity, ids)

    async def update_one(
        self, entity: type[E], *, id_: str, changes: Filters
    ) -> E | None:
        obj = self._table(entity).get(id_)
        if obj is None:
            return None
        self._apply(entity, obj, changes)
        return obj

    async def update_where(
        self, entity: type[E], *, filters: Filters, changes: Filters, match: Match = None
    ) -> list[E]:
        """Sửa tại chỗ, nhưng vẫn phải qua đúng những phép kiểm của `save`.

        SQL thật áp ràng buộc duy nhất và khoá ngoại cho cả câu UPDATE, không
        riêng INSERT. Bỏ qua ở đây thì `fam test` xanh trong khi production ném
        lỗi 409 — đúng kiểu lệch tệ nhất.
        """
        found = self._select(entity, filters, match)
        for obj in found:
            self._apply(entity, obj, changes)
        return found

    def _apply(self, entity: type, obj: Any, changes: Filters) -> None:
        """Ghi giá trị vào một bản ghi, qua đúng những phép kiểm của `save`.

        SQL thật áp ràng buộc duy nhất và khoá ngoại cho cả câu UPDATE, không
        riêng INSERT. Bỏ qua ở đây thì `fam test` xanh trong khi production ném
        lỗi 409 — đúng kiểu lệch tệ nhất.
        """
        self._join_request()
        fields = mapping_for(entity).fields
        for name, value in changes.items():
            # Ép về đúng kiểu đã khai. Không có bước này thì cột Enum giữ
            # nguyên chuỗi thô, trong khi SQL/Mongo đọc lên vẫn ra Enum —
            # `r.status.value` chạy ở hai chỗ kia và nổ ở đây.
            setattr(obj, name, coerce_value(fields[name], value))
        self._check_unique(entity, obj)
        self._check_references(entity, obj)


def _read(row: dict[str, Any], column: Any) -> Any:
    from fastapi_modular.infrastructure.database.query import _value_of

    return _value_of(column, row)


def _has_aggregate(spec: Any) -> bool:
    from fastapi_modular.infrastructure.database.query import Aggregate

    return any(isinstance(x, Aggregate) for x in spec.selects.values()) or bool(spec.havings)


def _group(spec: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gộp dòng theo `spec.groups`, giữ nguyên thứ tự nhóm xuất hiện lần đầu.

    Dòng đại diện lấy từ bản ghi ĐẦU TIÊN của nhóm. Builder chỉ cho trả về cột
    đã gộp hoặc hàm gộp (xem `Query._check_grouped`), nên phần "bản ghi đầu
    tiên" chỉ còn ảnh hưởng tới cột ghép của `include` — mà cột đó cũng bắt
    buộc nằm trong `group_by`.
    """
    from fastapi_modular.infrastructure.database.query import BUCKET_KEY, _value_of

    buckets: dict[tuple, list[dict[str, Any]]] = {}
    for row in rows:
        key = tuple(_value_of(column, row) for column in spec.groups)
        buckets.setdefault(key, []).append(row)
    return [{**bucket[0], BUCKET_KEY: bucket} for bucket in buckets.values()]
