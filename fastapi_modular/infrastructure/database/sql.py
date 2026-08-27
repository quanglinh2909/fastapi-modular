"""Backend SQL cho SQLite và PostgreSQL, dùng SQLAlchemy Core (không ORM).

File này CHỈ được import khi settings chọn driver sqlite/postgres — xem
`factory.py`. Nhờ vậy máy không cài SQLAlchemy vẫn chạy được template.

Bảng được suy ra từ dataclass entity, nên entity không phải kế thừa Base hay
khai báo Column. Đổi lại, `create_all()` chỉ tạo bảng còn thiếu và không biết
migrate — production nên dùng Alembic thay cho `auto_create`.

Transaction: mỗi request dùng chung một connection do `SqlUnitOfWork` giữ
(provider request-scoped), commit khi handler chạy xong, rollback nếu có lỗi.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime
from enum import Enum
from typing import Any, TypeVar

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    func,
    select,
)
from sqlalchemy import (
    delete as sql_delete,
)
from sqlalchemy import (
    insert as sql_insert,
)
from sqlalchemy import (
    update as sql_update,
)
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from fastapi_modular.core.container import Scope, injectable
from fastapi_modular.core.exceptions import BadRequestError
from fastapi_modular.core.logging import get_logger
from fastapi_modular.infrastructure.database.base import (
    DatabaseBackend,
    Filters,
    Match,
    RollbackRequested,
    Transaction,
    active_filters,
    coerce_value,
    from_document,
    mapping_for,
    to_document,
)

log = get_logger(__name__)

E = TypeVar("E")

_COLUMN_TYPES: dict[type, Any] = {
    str: String,
    bool: Boolean,
    int: Integer,
    float: Float,
    datetime: DateTime(timezone=True),
}


def _type_name(type_: Any, dialect: Any) -> str:
    """Tên kiểu đã chuẩn hoá, bỏ phần độ dài, để so hai bên cho công bằng."""
    try:
        rendered = type_.compile(dialect=dialect)
    except Exception:  # noqa: BLE001 - kiểu lạ không compile được thì lấy repr
        rendered = str(type_)
    return rendered.split("(")[0].strip().upper()


def _column_type(declared: type) -> Any:
    if isinstance(declared, type) and issubclass(declared, Enum):
        return String(64)  # Enum lưu bằng .value cho dễ đọc và dễ migrate
    return _COLUMN_TYPES.get(declared, String)


# Connection của một `async with db.transaction():` đang mở. ContextVar chứ
# không phải thuộc tính của backend: hai request (hoặc hai task asyncio) chạy
# song song phải thấy transaction của riêng mình.
_open_transaction: ContextVar[AsyncConnection | None] = ContextVar(
    "sql_open_transaction", default=None
)


def _on_delete_group(action: str | None) -> str:
    """Gom `on_delete` theo HÀNH VI, để so sánh không báo động giả.

    Database ghi "NO ACTION" khi câu CREATE TABLE không nói gì, còn khung mặc
    định khai "RESTRICT". Hai cái này chỉ khác nhau ở ràng buộc DEFERRABLE —
    thứ khung không dùng — nên coi là một, nếu không mọi bảng cũ đều bị kêu.
    """
    name = (action or "NO ACTION").upper()
    return "NO ACTION" if name in ("NO ACTION", "RESTRICT") else name


@injectable(scope=Scope.REQUEST)
class SqlUnitOfWork:
    """Một connection + một transaction cho mỗi request.

    Container tạo nó khi backend cần lần đầu trong request, và gọi
    `on_request_end` lúc đóng request scope — commit nếu handler thành công,
    rollback nếu có exception. Vì việc này chạy TRƯỚC khi response được gửi
    (xem controller.py), client đọc lại ngay sau khi ghi sẽ thấy dữ liệu mới.
    """

    def __init__(self) -> None:
        self._connection: AsyncConnection | None = None

    async def connection(self, engine: AsyncEngine) -> AsyncConnection:
        if self._connection is None:
            self._connection = await engine.connect()
            await self._connection.begin()
        return self._connection

    async def on_request_end(self, error: BaseException | None) -> None:
        if self._connection is None:
            return
        try:
            if error is None:
                await self._connection.commit()
            else:
                await self._connection.rollback()
        finally:
            await self._connection.close()
            self._connection = None


def build_metadata(*entities: type) -> MetaData:
    """Dựng MetaData từ các entity — dùng cho Alembic autogenerate.

    Không cần engine, không cần kết nối: chỉ đọc dataclass và suy ra bảng.
    """
    metadata = MetaData()
    for entity in entities:
        mapping = mapping_for(entity)
        Table(
            mapping.storage,
            metadata,
            *(
                Column(
                    name,
                    _column_type(declared),
                    primary_key=(name == "id"),
                    nullable=(name != "id"),
                )
                for name, declared in mapping.fields.items()
            ),
            *(
                Index(name, *columns, unique=is_unique)
                for name, columns, is_unique in mapping.index_specs()
            ),
        )
    return metadata


class SqlBackend(DatabaseBackend):
    def __init__(
        self,
        dsn: str,
        *,
        echo: bool = False,
        schema_mode: str = "create",
        drop_columns: bool = False,
        pool_pre_ping: bool = True,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_recycle_seconds: int = 1800,
        connect_timeout_seconds: float = 10.0,
        query_timeout_seconds: float = 15.0,
        sqlite_journal_mode: str = "WAL",
        sqlite_synchronous: str = "NORMAL",
        sqlite_busy_timeout_seconds: float = 5.0,
    ) -> None:
        self.name = "postgres" if dsn.startswith("postgresql") else "sqlite"
        self._dsn = dsn
        self._echo = echo
        self._schema_mode = schema_mode
        self._drop_columns = drop_columns
        self._pool_pre_ping = pool_pre_ping
        self._pool_size = pool_size
        self._max_overflow = max_overflow
        self._pool_recycle = pool_recycle_seconds
        self._connect_timeout = connect_timeout_seconds
        self._query_timeout = query_timeout_seconds
        self._sqlite_journal_mode = sqlite_journal_mode
        self._sqlite_synchronous = sqlite_synchronous
        self._sqlite_busy_timeout = sqlite_busy_timeout_seconds
        self._engine: AsyncEngine | None = None
        self._metadata = MetaData()
        self._tables: dict[str, Table] = {}

    # ------------------------------------------------------------------ vòng đời
    def _engine_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "echo": self._echo,
            "future": True,
            # Kiểm tra connection còn sống trước khi giao cho request. Đây là
            # thứ khiến database restart trở nên vô hình với client.
            "pool_pre_ping": self._pool_pre_ping,
        }
        if self.name == "postgres":
            # SQLite dùng NullPool nên không nhận các tham số pool này.
            kwargs.update(
                pool_size=self._pool_size,
                max_overflow=self._max_overflow,
                pool_recycle=self._pool_recycle,
                connect_args={
                    "timeout": self._connect_timeout,
                    # Chặn cả câu lệnh đã gửi đi, không chỉ lúc mở kết nối.
                    "command_timeout": self._query_timeout,
                },
            )
        return kwargs

    def _apply_sqlite_pragmas(self, engine: AsyncEngine) -> None:
        """Đặt PRAGMA cho MỌI connection SQLite, ngay lúc nó vừa mở.

        Phải làm ở mức connection chứ không phải chạy một câu lệnh lúc khởi
        động: `journal_mode` thì dính vào file nên chỉ cần đặt một lần, nhưng
        `synchronous` và `busy_timeout` là **thiết lập của từng connection** —
        connection thứ hai trong pool không thừa hưởng gì từ connection đầu.

        Mặc định gốc của SQLite chậm tới mức khó tin: 68 ghi/s, vì mỗi commit
        là một fsync trọn vẹn. WAL + synchronous=NORMAL đưa nó lên 1.269 ghi/s
        mà vẫn không hỏng file khi mất điện. Xem `DatabaseSettings`.
        """
        from sqlalchemy import event

        pragmas = {
            "journal_mode": self._sqlite_journal_mode,
            "synchronous": self._sqlite_synchronous,
            "busy_timeout": int(self._sqlite_busy_timeout * 1000),
            # SQLite TẮT khoá ngoại mặc định, và tắt nghĩa là `ON DELETE CASCADE`
            # trong schema chỉ nằm đó làm cảnh: xoá cha thì con ở lại thành mồ
            # côi, không lỗi, không cảnh báo. Đây là thiết lập của TỪNG
            # connection nên phải đặt ở đây chứ không phải chạy một câu lệnh.
            "foreign_keys": "ON",
            # `LIKE` của SQLite mặc định KHÔNG phân biệt hoa thường (với ký tự
            # ASCII), trong khi Postgres và backend memory thì có. Đo được:
            # `LIKE 'kho%'` ra "Kho hàng" ở sqlite nhưng không ra gì ở hai chỗ
            # kia. Bật pragma này để ba backend cho cùng kết quả — cần chữ hoa
            # thường bỏ qua thì dùng `ilike`, chỗ nào cũng chạy. Nó còn cho
            # SQLite dùng index với `LIKE 'tiền tố%'`.
            "case_sensitive_like": "ON",
        }

        @event.listens_for(engine.sync_engine, "connect")
        def _set_pragmas(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            try:
                for key, value in pragmas.items():
                    cursor.execute(f"PRAGMA {key}={value}")
            finally:
                cursor.close()

        log.info("db.sqlite_pragmas", **pragmas)

    async def startup(self) -> None:
        self._engine = create_async_engine(self._dsn, **self._engine_kwargs())
        if self.name == "sqlite":
            self._apply_sqlite_pragmas(self._engine)
        log.info("db.connected", backend=self.name, pre_ping=self._pool_pre_ping)

    async def shutdown(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    async def ping(self) -> bool:
        from sqlalchemy import text

        async with self._engine.connect() as conn:  # type: ignore[union-attr]
            await conn.execute(text("SELECT 1"))
        return True

    # ------------------------------------------------------------------ ánh xạ
    def _table(self, entity: type) -> Table:
        mapping = mapping_for(entity)
        table = self._tables.get(mapping.storage)
        if table is not None:
            return table

        references = dict(mapping.references)
        columns = []
        for name, declared in mapping.fields.items():
            args: list[Any] = [name, _column_type(declared)]
            ref = references.get(name)
            if ref is not None:
                target = mapping_for(ref.target)
                # Bảng cha phải được dựng TRƯỚC trong cùng MetaData, nếu không
                # SQLAlchemy không phân giải nổi "cameras.id".
                self._table(ref.target)
                args.append(
                    ForeignKey(f"{target.storage}.{ref.column}", ondelete=ref.on_delete)
                )
            columns.append(
                Column(*args, primary_key=(name == "id"), nullable=(name != "id"))
            )
        table = Table(mapping.storage, self._metadata, *columns)
        self._tables[mapping.storage] = table
        return table

    async def create_schema(self, *entities: type) -> None:
        """Đưa schema về khớp entity, theo mức đã cấu hình.

        - "off"    : không làm gì.
        - "create" : chỉ CREATE TABLE cho bảng còn thiếu.
        - "sync"   : thêm cột mới bằng ALTER TABLE ADD COLUMN; cột thừa thì xoá
                     (nếu drop_columns=True) hoặc chỉ cảnh báo; cột lệch kiểu
                     chỉ cảnh báo, không tự đổi.

        Vì sao không tự đổi kiểu cột: mỗi database một cú pháp, và phép đổi có
        thể mất dữ liệu (VARCHAR -> INTEGER) hoặc khoá bảng rất lâu. Đó là việc
        của một migration có review, không phải của lúc khởi động.
        """
        if self._engine is None:
            return

        for entity in entities:
            self._table(entity)

        if self._schema_mode == "off":
            # Không đụng schema, nhưng vẫn phải soi: thiếu unique index thì
            # ràng buộc duy nhất không tồn tại, và hai request đồng thời sẽ
            # cùng ghi được bản trùng.
            await self._audit_indexes(*entities)
            await self._audit_foreign_keys(*entities)
            return

        async with self._engine.begin() as conn:
            await conn.run_sync(self._metadata.create_all)

        await self._ensure_indexes(*entities)
        await self._audit_foreign_keys(*entities)

        if self._schema_mode != "sync":
            log.info("db.schema_ready", mode=self._schema_mode, tables=sorted(self._tables))
            return

        added, dropped, mismatched, kept = [], [], [], []
        async with self._engine.begin() as conn:
            for table in self._tables.values():
                a, d, m, k = await self._sync_table(conn, table)
                added += a
                dropped += d
                mismatched += m
                kept += k

        log.info(
            "db.schema_ready",
            mode="sync",
            tables=sorted(self._tables),
            added=added or None,
            dropped=dropped or None,
        )
        for column in mismatched:
            log.warning("db.column_type_mismatch", column=column,
                        hint="đổi kiểu cột phải làm bằng migration, không tự động")
        for column in kept:
            log.warning("db.extra_column_kept", column=column,
                        hint="đặt APP_DB__DROP_COLUMNS=true nếu muốn xoá (mất dữ liệu)")

    def _actual_foreign_keys(self, conn: Any, table: str) -> dict[str, str]:
        """Khoá ngoại ĐANG CÓ TRONG DATABASE của một bảng: {cột: hành vi on_delete}.

        Phải tách theo dialect vì inspector của SQLAlchemy KHÔNG trả `ondelete`
        cho SQLite — đo được: `options` rỗng kể cả với `ON DELETE CASCADE`.
        Với SQLite phải đọc `PRAGMA foreign_key_list`, chỗ đó mới có.
        """
        from sqlalchemy import inspect as sa_inspect

        if conn.dialect.name == "sqlite":
            rows = conn.exec_driver_sql(f'PRAGMA foreign_key_list("{table}")').mappings()
            return {row["from"]: _on_delete_group(row["on_delete"]) for row in rows}

        found = {}
        for fk in sa_inspect(conn).get_foreign_keys(table):
            columns = fk.get("constrained_columns") or []
            if len(columns) == 1:                       # khung chỉ sinh khoá một cột
                found[columns[0]] = _on_delete_group(fk.get("options", {}).get("ondelete"))
        return found

    async def _audit_foreign_keys(self, *entities: type) -> None:
        """So khoá ngoại khai trong entity với khoá ngoại THẬT trong database.

        Vì sao cần: `create_all` chỉ tạo bảng CÒN THIẾU, và mode "sync" chỉ
        thêm/bớt CỘT — không cái nào đụng tới ràng buộc của bảng đã tồn tại.
        Nên thêm `reference(...)` hoặc đổi `on_delete` vào một entity đã chạy
        rồi thì khai báo đó nằm lại trong Python, database không hề biết.

        Hậu quả đo được: xoá cha thì tầng con thứ nhất bị xoá (khoá ngoại của
        nó có thật), tầng thứ hai ở lại thành mồ côi — không lỗi, không cảnh
        báo, chỉ là dữ liệu rác lặng lẽ đọng lại. Đây là chỗ để kêu lên.
        """
        assert self._engine is not None
        problems: list[str] = []

        async with self._engine.connect() as conn:
            for entity in entities:
                mapping = mapping_for(entity)
                if not mapping.references:
                    continue
                try:
                    actual = await conn.run_sync(
                        lambda c, name=mapping.storage: self._actual_foreign_keys(c, name)
                    )
                except Exception:  # noqa: BLE001 - bảng chưa tồn tại
                    continue
                for column, ref in mapping.references:
                    want = _on_delete_group(ref.on_delete)
                    have = actual.get(column)
                    where = f"{mapping.storage}.{column}"
                    if have is None:
                        problems.append(
                            f"{where} -> {mapping_for(ref.target).storage}: "
                            f"database KHÔNG có khoá ngoại này"
                        )
                    elif have != want:
                        problems.append(
                            f"{where}: khai ON DELETE {ref.on_delete}, database đang {have}"
                        )

        if problems:
            log.warning(
                "db.foreign_keys_stale",
                problems=problems,
                hint="bảng đã tạo từ trước nên khoá ngoại giữ nguyên hình cũ — "
                     "khung KHÔNG tự sửa ràng buộc. Hậu quả hay gặp nhất: xoá "
                     "cha mà cháu ở lại thành mồ côi. Sửa bằng migration "
                     "(ALTER TABLE ... ADD CONSTRAINT ... ON DELETE ...), hoặc "
                     "ở môi trường dev thì xoá bảng cho khung tạo lại",
            )

    async def _audit_indexes(self, *entities: type) -> None:
        """Chỉ kiểm tra, không tạo — dùng khi schema_mode="off"."""
        from sqlalchemy import inspect as sa_inspect

        assert self._engine is not None
        missing: list[str] = []

        async with self._engine.connect() as conn:
            for entity in entities:
                mapping = mapping_for(entity)
                if not (mapping.unique or mapping.indexes):
                    continue
                try:
                    existing = await conn.run_sync(
                        lambda c, name=mapping.storage: {
                            i["name"] for i in sa_inspect(c).get_indexes(name)
                        }
                    )
                except Exception:  # noqa: BLE001 - bảng chưa tồn tại
                    continue
                for name, columns, is_unique in mapping.index_specs():
                    if name in existing:
                        continue
                    label = f"{mapping.storage}({', '.join(columns)})"
                    missing.append(f"{label} (UNIQUE)" if is_unique else label)

        if missing:
            log.warning(
                "db.indexes_missing",
                indexes=missing,
                hint="schema_mode='off' nên index không được tạo tự động — "
                     "hãy thêm chúng vào migration, nếu không ràng buộc duy nhất "
                     "sẽ không có hiệu lực",
            )

    async def _ensure_indexes(self, *entities: type) -> None:
        """Tạo index và unique index đã khai báo ở @entity.

        `CREATE [UNIQUE] INDEX IF NOT EXISTS` chạy được trên cả SQLite lẫn
        PostgreSQL và lặp lại vô hại, nên gọi mỗi lần khởi động cũng không sao.
        """
        from sqlalchemy import text

        assert self._engine is not None
        for entity in entities:
            mapping = mapping_for(entity)
            for name, columns, is_unique in mapping.index_specs():
                kind = "UNIQUE INDEX" if is_unique else "INDEX"
                statement = (
                    f"CREATE {kind} IF NOT EXISTS {name} "
                    f"ON {mapping.storage} ({', '.join(columns)})"
                )
                try:
                    async with self._engine.begin() as conn:
                        await conn.execute(text(statement))
                except Exception as exc:  # noqa: BLE001 - index hỏng không được làm chết app
                    # Hay gặp nhất: dữ liệu cũ đã có bản trùng nên không tạo
                    # được unique index. Không làm app chết, nhưng phải kêu to
                    # vì lúc này ràng buộc KHÔNG có hiệu lực.
                    log.error(
                        "db.index_failed",
                        index=name,
                        columns=list(columns),
                        unique=is_unique,
                        error=str(exc).splitlines()[0],
                        hint="dọn bản ghi trùng rồi khởi động lại",
                    )

    async def _sync_table(self, conn: AsyncConnection, table: Table):
        from sqlalchemy import inspect as sa_inspect
        from sqlalchemy import text

        existing = await conn.run_sync(
            lambda sync_conn: {c["name"]: c for c in sa_inspect(sync_conn).get_columns(table.name)}
        )
        dialect = conn.engine.dialect
        added, dropped, mismatched, kept = [], [], [], []

        # cột có trong entity nhưng chưa có dưới database -> thêm
        for column in table.columns:
            if column.name in existing:
                # Phải compile CẢ HAI bằng cùng dialect. Dùng str() cho kiểu do
                # inspector trả về sẽ mất thông tin (TIMESTAMP WITH TIME ZONE
                # in ra thành "TIMESTAMP") và sinh cảnh báo sai.
                want = _type_name(column.type, dialect)
                have = _type_name(existing[column.name]["type"], dialect)
                if want != have:
                    mismatched.append(f"{table.name}.{column.name}: {have} -> {want}")
                continue

            ddl = f"{column.name} {column.type.compile(dialect=dialect)}"
            await conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))
            added.append(f"{table.name}.{column.name}")

        # cột còn dưới database nhưng entity đã bỏ
        for name in existing:
            if name in table.c:
                continue
            if not self._drop_columns:
                kept.append(f"{table.name}.{name}")
                continue
            try:
                await conn.execute(text(f"ALTER TABLE {table.name} DROP COLUMN {name}"))
                dropped.append(f"{table.name}.{name}")
            except Exception as exc:  # noqa: BLE001 - SQLite cũ không DROP COLUMN được
                kept.append(f"{table.name}.{name}")
                log.warning("db.drop_column_failed", column=f"{table.name}.{name}", error=str(exc))

        return added, dropped, mismatched, kept

    # ------------------------------------------------------------------ kết nối
    @asynccontextmanager
    async def _conn(self) -> AsyncIterator[AsyncConnection]:
        """Connection nào cũng theo thứ tự này: transaction đang mở -> request -> tạm.

        Thứ tự quan trọng: đang trong `async with db.transaction()` thì MỌI thao
        tác phải đi qua đúng connection đó, nếu không hai câu lệnh nằm ở hai
        transaction khác nhau và "cùng thành công hoặc cùng không" mất nghĩa.
        """
        from fastapi_modular.core.container import container

        assert self._engine is not None, "backend chưa startup()"
        mo = _open_transaction.get()
        if mo is not None:
            yield mo
            return
        try:
            uow = container.resolve(SqlUnitOfWork)
        except RuntimeError:
            async with self._engine.begin() as conn:  # ngoài request: tự commit
                yield conn
            return
        yield await uow.connection(self._engine)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Transaction]:
        """Gộp nhiều thao tác thành một: cùng thành công, hoặc cùng không.

        Ba ca, ba cách nối vào:

        - Đã ở trong `async with db.transaction()` khác -> mở SAVEPOINT, để
          khối trong hỏng thì chỉ khối trong bị huỷ.
        - Đang trong một HTTP request -> dùng chung connection của request và
          mở SAVEPOINT trên đó, vì request đã mở transaction từ trước.
        - Ngoài request (worker, job, script) -> mở hẳn một connection mới,
          COMMIT khi thoát êm, ROLLBACK khi có exception.
        """
        from fastapi_modular.core.container import container

        assert self._engine is not None, "backend chưa startup()"

        dang_mo = _open_transaction.get()
        if dang_mo is None:
            try:
                dang_mo = await container.resolve(SqlUnitOfWork).connection(self._engine)
            except RuntimeError:
                dang_mo = None

        if dang_mo is not None:
            # Đã có transaction bao ngoài: SAVEPOINT là cách duy nhất để khối
            # này huỷ được phần của mình mà không đụng phần bên ngoài.
            token = _open_transaction.set(dang_mo)
            nested = await dang_mo.begin_nested()
            tx = Transaction()
            try:
                yield tx
            except RollbackRequested:
                await nested.rollback()      # tx.rollback(): huỷ, không ném tiếp
            except BaseException:
                await nested.rollback()
                raise
            else:
                await nested.commit()
            finally:
                _open_transaction.reset(token)
            return

        conn = await self._engine.connect()
        token = _open_transaction.set(conn)
        tx = Transaction()
        try:
            await conn.begin()
            try:
                yield tx
            except RollbackRequested:
                await conn.rollback()
            except BaseException:
                await conn.rollback()
                raise
            else:
                await conn.commit()
        finally:
            _open_transaction.reset(token)
            await conn.close()

    def _where(self, entity: type, table: Table, filters: Filters) -> list[Any]:
        return [table.c[k] == v for k, v in active_filters(filters, entity).items()]

    def _row_to_entity(self, entity: type[E], row: Any) -> E:
        return from_document(entity, dict(row._mapping))

    # ------------------------------------------------------------------ truy vấn
    async def get(self, entity: type[E], id_: str) -> E | None:
        table = self._table(entity)
        async with self._conn() as conn:
            row = (await conn.execute(select(table).where(table.c.id == id_))).first()
        return self._row_to_entity(entity, row) if row is not None else None

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
        table = self._table(entity)
        stmt = select(table).where(*self._where(entity, table, filters))
        if order_by and order_by in table.c:
            stmt = stmt.order_by(table.c[order_by])

        # `match=` là predicate Python nên KHÔNG đẩy xuống SQL được: phải lấy
        # về rồi lọc. Chỉ phân trang ở SQL khi không có match.
        if match is None:
            if offset:
                stmt = stmt.offset(offset)
            if limit is not None:
                stmt = stmt.limit(limit)

        async with self._conn() as conn:
            rows = (await conn.execute(stmt)).fetchall()

        items = [self._row_to_entity(entity, r) for r in rows]
        if match is not None:
            items = [o for o in items if match(o)][offset:]
            if limit is not None:
                items = items[:limit]
        return items

    async def find_one(
        self, entity: type[E], *, filters: Filters, match: Match = None
    ) -> E | None:
        items = await self.find(entity, filters=filters, match=match, limit=1)
        return items[0] if items else None

    async def count(
        self, entity: type[E], *, filters: Filters, match: Match = None
    ) -> int:
        if match is not None:
            return len(await self.find(entity, filters=filters, match=match))
        table = self._table(entity)
        stmt = select(func.count()).select_from(table).where(*self._where(entity, table, filters))
        async with self._conn() as conn:
            return int((await conn.execute(stmt)).scalar_one())

    # -------------------------------------------------------------- builder
    def _compile(self, spec: Any) -> Any:
        """QuerySpec -> câu SELECT của SQLAlchemy Core.

        Mọi thứ đi xuống database: JOIN, WHERE, ORDER BY, LIMIT. Khác hẳn
        `find(match=...)` vốn phải kéo cả bảng về rồi lọc bằng Python.
        """
        from sqlalchemy import and_ as sql_and
        from sqlalchemy import func
        from sqlalchemy import not_ as sql_not
        from sqlalchemy import or_ as sql_or

        from fastapi_modular.infrastructure.database.query import (
            Aggregate,
            Compare,
            Group,
            Not,
            table_of,
        )
        from fastapi_modular.infrastructure.database.query import (
            Column as QColumn,
        )
        from fastapi_modular.infrastructure.database.query import (
            _alias_of as alias_of_entity,
        )

        root = self._table(spec.entity)

        # Bảng tra theo TÊN chứ không theo entity: nối bảng với chính nó thì
        # hai cột cùng entity lại là hai bảng khác nhau trong câu lệnh.
        tables: dict[str, Any] = {alias_of_entity(spec.entity): root}
        for join in spec.joins:
            table = self._table(join.entity)
            if join.alias != alias_of_entity(join.entity):
                table = table.alias(join.alias)
            tables[join.alias] = table

        def col(column: QColumn) -> Any:
            name = table_of(column)
            table = tables.get(name)
            if table is None:
                raise BadRequestError(
                    f"Cột {column!r} trỏ tới bảng {name!r} chưa có trong truy vấn. "
                    f"Đang có: {', '.join(sorted(tables))}."
                )
            return table.c[column.field]

        def gop(item: Aggregate) -> Any:
            if item.column is None:
                return func.count()
            inner = col(item.column)
            if item.distinct:
                inner = inner.distinct()
            return getattr(func, item.func)(inner)

        def value_of(item: Any) -> Any:
            return gop(item) if isinstance(item, Aggregate) else col(item)

        def build(condition: Any) -> Any:
            if isinstance(condition, Group):
                parts = [build(p) for p in condition.parts]
                return sql_and(*parts) if condition.op == "and" else sql_or(*parts)
            if isinstance(condition, Not):
                return sql_not(build(condition.part))
            if not isinstance(condition, Compare):
                raise BadRequestError(f"Điều kiện lạ: {condition!r}")

            left = value_of(condition.column)
            value = condition.value
            if isinstance(value, (QColumn, Aggregate)):
                value = value_of(value)
            op = condition.op
            if op == "isnull":
                return left.is_(None) if value else left.isnot(None)
            if op == "eq":
                return left == value
            if op == "ne":
                return left != value
            if op == "gt":
                return left > value
            if op == "gte":
                return left >= value
            if op == "lt":
                return left < value
            if op == "lte":
                return left <= value
            if op == "in":
                return left.in_(value)
            if op == "nin":
                return left.notin_(value)
            if op == "like":
                return left.like(value)
            if op == "ilike":
                return left.ilike(value)
            if op == "between":
                return left.between(value[0], value[1])
            raise BadRequestError(f"Toán tử {op!r} chưa cài cho SQL")

        if spec.selects:
            stmt = select(*(value_of(c).label(name) for name, c in spec.selects.items()))
        else:
            stmt = select(root)

        target: Any = root
        for join in spec.joins:
            other = tables[join.alias]
            on = build(join.on)
            if join.kind == "right":
                # `A RIGHT JOIN B` == `B LEFT JOIN A`. Sinh ra vế trái cho lành:
                # SQLite chỉ có RIGHT JOIN từ 3.39, còn LEFT JOIN thì ở đâu cũng có.
                target = other.join(target, on, isouter=True)
            else:
                target = target.join(
                    other, on, isouter=join.kind != "inner", full=join.kind == "outer"
                )
        if spec.joins:
            stmt = stmt.select_from(target)

        for condition in spec.conditions:
            stmt = stmt.where(build(condition))
        for column in spec.groups:
            stmt = stmt.group_by(col(column))
        for condition in spec.havings:
            stmt = stmt.having(build(condition))
        if spec.distinct:
            stmt = stmt.distinct()
        for order in spec.orders:
            column = value_of(order.column)
            stmt = stmt.order_by(column.desc() if order.descending else column.asc())
        if spec.offset:
            stmt = stmt.offset(spec.offset)
        if spec.limit is not None:
            stmt = stmt.limit(spec.limit)
        return stmt

    async def run_query(self, spec: Any) -> list[Any]:
        stmt = self._compile(spec)
        async with self._conn() as conn:
            rows = (await conn.execute(stmt)).fetchall()
        if spec.selects:
            return [self._select_row(spec, dict(row._mapping)) for row in rows]
        return [self._row_to_entity(spec.entity, row) for row in rows]

    @staticmethod
    def _select_row(spec: Any, raw: dict[str, Any]) -> dict[str, Any]:
        """Ép kiểu từng cột đã chọn, để dict của `.select()` giống hệt memory."""
        from fastapi_modular.infrastructure.database.query import Aggregate

        out: dict[str, Any] = {}
        for name, item in spec.selects.items():
            value = raw.get(name)
            if isinstance(item, Aggregate):
                out[name] = value          # count/avg/sum: kiểu do database quyết
            else:
                declared = mapping_for(item.entity).fields[item.field]
                out[name] = coerce_value(declared, value)
        return out

    async def count_query(self, spec: Any) -> int:
        # Đếm ở database, không kéo dòng nào về. Bỏ order/limit vì chúng vô
        # nghĩa với count và Postgres còn từ chối ORDER BY trong subquery đếm.
        import dataclasses as _dc

        from sqlalchemy import func

        # Có `group_by` thì SELECT phải là chính các cột gộp: Postgres từ chối
        # `SELECT * ... GROUP BY x`. Đếm xong là đếm SỐ NHÓM, đúng như SQL.
        selects = {f"g{i}": c for i, c in enumerate(spec.groups)} if spec.groups else {}
        plain = _dc.replace(spec, orders=[], limit=None, offset=0, selects=selects)
        inner = self._compile(plain).subquery()
        async with self._conn() as conn:
            return int((await conn.execute(select(func.count()).select_from(inner))).scalar() or 0)

    def query_sql(self, spec: Any) -> str:
        """Câu SQL sẽ chạy, giá trị nhúng thẳng — để đọc, không phải để chạy."""
        stmt = self._compile(spec)
        return str(stmt.compile(self._engine, compile_kwargs={"literal_binds": True}))

    # ------------------------------------------------------------------ ghi
    async def save(self, entity: type[E], obj: E) -> E:
        import uuid

        table = self._table(entity)
        is_new = not getattr(obj, "id", None)
        if is_new:
            obj.id = uuid.uuid4().hex  # type: ignore[attr-defined]

        values = to_document(obj)
        async with self._conn() as conn:
            if is_new:
                await conn.execute(sql_insert(table).values(**values))
            else:
                result = await conn.execute(
                    sql_update(table).where(table.c.id == obj.id).values(**values)  # type: ignore[attr-defined]
                )
                if result.rowcount == 0:
                    await conn.execute(sql_insert(table).values(**values))
        return obj

    async def delete(self, entity: type[E], id_: str) -> bool:
        table = self._table(entity)
        async with self._conn() as conn:
            result = await conn.execute(sql_delete(table).where(table.c.id == id_))
        return result.rowcount > 0

    async def delete_where(
        self, entity: type[E], *, filters: Filters, match: Match = None
    ) -> int:
        if match is not None:
            victims = await self.find(entity, filters=filters, match=match)
            removed = 0
            for obj in victims:
                removed += int(await self.delete(entity, obj.id))  # type: ignore[attr-defined]
            return removed

        table = self._table(entity)
        async with self._conn() as conn:
            result = await conn.execute(sql_delete(table).where(*self._where(entity, table, filters)))
        return int(result.rowcount)
