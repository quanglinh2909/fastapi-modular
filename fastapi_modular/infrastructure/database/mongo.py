"""Backend MongoDB dùng motor (driver async chính thức).

File này CHỈ được import khi settings chọn driver mongodb — xem `factory.py`.

Ánh xạ: mỗi entity một collection, trường `id` của entity lưu vào `_id` của
document nên không tốn thêm index.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, TypeVar

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection

from fastapi_modular.core.container import _ENTITIES
from fastapi_modular.core.exceptions import (
    BadRequestError,
    CapabilityNotSupportedError,
    ConflictError,
)
from fastapi_modular.core.logging import get_logger
from fastapi_modular.infrastructure.database.base import (
    DatabaseBackend,
    Filters,
    Match,
    active_filters,
    default_of,
    from_document,
    mapping_for,
    to_document,
)

log = get_logger(__name__)

E = TypeVar("E")


def _to_mongo(doc: dict[str, Any]) -> dict[str, Any]:
    out = dict(doc)
    out["_id"] = out.pop("id")
    return out


def _from_mongo(doc: dict[str, Any]) -> dict[str, Any]:
    out = dict(doc)
    out["id"] = out.pop("_id")
    return out


class MongoBackend(DatabaseBackend):
    name = "mongodb"

    def __init__(
        self,
        dsn: str,
        *,
        database: str,
        connect_timeout_seconds: float = 10.0,
        query_timeout_seconds: float = 15.0,
    ) -> None:
        self._dsn = dsn
        self._database_name = database
        self._timeout_ms = int(connect_timeout_seconds * 1000)
        self._query_timeout_ms = int(query_timeout_seconds * 1000)
        self._client: AsyncIOMotorClient | None = None

    async def startup(self) -> None:
        # motor/pymongo tự dò lại server và kết nối lại; chỉ cần siết thời gian
        # chờ, vì mặc định 30 giây sẽ khiến request treo rất lâu khi Mongo chết.
        self._client = AsyncIOMotorClient(
            self._dsn,
            uuidRepresentation="standard",
            serverSelectionTimeoutMS=self._timeout_ms,
            connectTimeoutMS=self._timeout_ms,
            # Chặn câu lệnh đã gửi: server treo thì socket đọc mãi không xong.
            socketTimeoutMS=self._query_timeout_ms,
        )
        log.info("db.connected", backend=self.name, database=self._database_name)

    async def shutdown(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    async def ping(self) -> bool:
        await self._client[self._database_name].command("ping")  # type: ignore[index]
        return True

    async def create_schema(self, *entities: type) -> None:
        """Mongo không cần tạo collection, nhưng index thì có.

        Không có index, mọi truy vấn lọc đều quét toàn bộ collection; và không
        có unique index thì hai request đồng thời cùng tạo được bản trùng.
        """
        for entity in entities:
            mapping = mapping_for(entity)
            collection = self._collection(entity)
            for name, columns, is_unique in mapping.index_specs():
                try:
                    await collection.create_index(
                        [(column, 1) for column in columns], unique=is_unique, name=name
                    )
                except Exception as exc:  # noqa: BLE001 - index hỏng không được làm chết app
                    log.error(
                        "db.index_failed",
                        collection=mapping.storage,
                        index=name,
                        columns=list(columns),
                        unique=is_unique,
                        error=str(exc).splitlines()[0],
                        hint="dọn document trùng rồi khởi động lại",
                    )
        log.info("db.indexes_ready", collections=[mapping_for(e).storage for e in entities])

    def _collection(self, entity: type) -> AsyncIOMotorCollection:
        assert self._client is not None, "backend chưa startup()"
        return self._client[self._database_name][mapping_for(entity).storage]

    def _query(self, filters: Filters) -> dict[str, Any]:
        query = active_filters(filters)
        if "id" in query:
            query["_id"] = query.pop("id")
        return query

    async def get(self, entity: type[E], id_: str) -> E | None:
        doc = await self._collection(entity).find_one({"_id": id_})
        return from_document(entity, _from_mongo(doc)) if doc else None

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
        cursor = self._collection(entity).find(self._query(filters))
        if order_by:
            cursor = cursor.sort(order_by, 1)

        # `match=` là predicate Python, không dịch sang query Mongo được:
        # phải lấy về rồi lọc. Chỉ phân trang ở DB khi không có match.
        if match is None:
            if offset:
                cursor = cursor.skip(offset)
            if limit is not None:
                cursor = cursor.limit(limit)

        items = [from_document(entity, _from_mongo(d)) async for d in cursor]
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
        return await self._collection(entity).count_documents(self._query(filters))

    # -------------------------------------------------------------- builder
    async def run_query(self, spec: Any) -> list[Any]:
        raise BadRequestError(
            "Query builder chưa hỗ trợ MongoDB. MongoDB có `$lookup` nhưng ngữ "
            "nghĩa join lệch đủ nhiều để một bản giả lập sẽ đúng ở demo và sai ở "
            "production, nên thà nói không. Dùng `repo.find(...)` cho truy vấn một "
            "collection, hoặc đổi APP_DB__DRIVER sang postgres/sqlite."
        )

    async def count_query(self, spec: Any) -> int:
        return await self.run_query(spec)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """MongoDB một node KHÔNG có transaction đa-document — nói thẳng, không giả vờ.

        Giả vờ (chạy tiếp rồi không rollback được) là cách chắc chắn nhất để
        một lỗi giữa chừng để lại dữ liệu nửa vời mà không ai biết.
        """
        raise CapabilityNotSupportedError(
            "MongoDB chỉ có transaction đa-document khi chạy replica set, và "
            "template không bật. Cách khác: gộp dữ liệu cần ghi cùng lúc vào MỘT "
            "document (Mongo bảo đảm nguyên tử ở mức một document), hoặc đổi "
            "APP_DB__DRIVER sang postgres/sqlite."
        )
        yield       # pragma: no cover - để hàm này vẫn là async generator

    async def save(self, entity: type[E], obj: E) -> E:
        if not getattr(obj, "id", None):
            obj.id = uuid.uuid4().hex  # type: ignore[attr-defined]
        doc = _to_mongo(to_document(obj))
        await self._collection(entity).replace_one({"_id": doc["_id"]}, doc, upsert=True)
        return obj

    # ------------------------------------------------------------ khoá ngoại
    async def _cascade(self, parent: type, ids: list[str]) -> None:
        """Áp `on_delete` bằng tay, vì MongoDB KHÔNG có khoá ngoại.

        Đây là khác biệt phải biết trước, không phải chi tiết vụn: với SQL thì
        chính database áp ràng buộc, trong cùng một transaction, nên hoặc xong
        hết hoặc không gì cả. Ở đây là khung chạy nhiều lệnh nối nhau — tiến
        trình chết giữa chừng thì bạn còn lại bản ghi cha đã mất mà con chưa
        dọn, hoặc ngược lại.

        Cần bảo đảm thật thì dùng postgres, hoặc tự bọc trong transaction của
        MongoDB (cần replica set).
        """
        if not ids:
            return
        for child in list(_ENTITIES.values()):
            for column, ref in mapping_for(child).references:
                if ref.target is not parent:
                    continue
                collection = self._collection(child)
                loc = {column: {"$in": ids}}
                if ref.on_delete == "CASCADE":
                    con = [doc["_id"] async for doc in collection.find(loc, {"_id": 1})]
                    if con:
                        await self._cascade(child, con)
                        await collection.delete_many(loc)
                elif ref.on_delete == "SET NULL":
                    await collection.update_many(loc, {"$set": {column: None}})
                elif ref.on_delete == "SET DEFAULT":
                    await collection.update_many(
                        loc, {"$set": {column: default_of(child, column)}}
                    )
                else:                                # RESTRICT / NO ACTION
                    con = await collection.count_documents(loc)
                    if con:
                        raise ConflictError(
                            f"Không xoá được {parent.__name__} vì còn {con} "
                            f"{child.__name__} trỏ tới nó ({column}). Xoá chúng "
                            'trước, hoặc khai on_delete="CASCADE"/"SET NULL".'
                        )

    async def delete(self, entity: type[E], id_: str) -> bool:
        await self._cascade(entity, [id_])
        result = await self._collection(entity).delete_one({"_id": id_})
        return result.deleted_count > 0

    async def delete_where(
        self, entity: type[E], *, filters: Filters, match: Match = None
    ) -> int:
        if match is not None:
            victims = await self.find(entity, filters=filters, match=match)
            removed = 0
            for obj in victims:
                removed += int(await self.delete(entity, obj.id))  # type: ignore[attr-defined]
            return removed

        query = self._query(filters)
        if mapping_for(entity).references or _co_con(entity):
            ids = [doc["_id"] async for doc in self._collection(entity).find(query, {"_id": 1})]
            await self._cascade(entity, ids)
        result = await self._collection(entity).delete_many(query)
        return int(result.deleted_count)


def _co_con(parent: type) -> bool:
    """Có entity nào trỏ tới `parent` không — để khỏi truy vấn id thừa."""
    return any(
        ref.target is parent
        for child in list(_ENTITIES.values())
        for _, ref in mapping_for(child).references
    )
