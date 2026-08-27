"""Backend MongoDB dùng motor (driver async chính thức).

File này CHỈ được import khi settings chọn driver mongodb — xem `factory.py`.

Ánh xạ: mỗi entity một collection, trường `id` của entity lưu vào `_id` của
document nên không tốn thêm index.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import Enum
from typing import Any, TypeVar

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection

from fastapi_modular.core.container import _ENTITIES
from fastapi_modular.core.exceptions import BadRequestError, ConflictError
from fastapi_modular.core.logging import get_logger
from fastapi_modular.core.providers import CapabilityNotSupportedError
from fastapi_modular.infrastructure.database.base import (
    DatabaseBackend,
    Filters,
    Match,
    active_filters,
    coerce_value,
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

    def _query(self, entity: type, filters: Filters) -> dict[str, Any]:
        query = active_filters(filters, entity)
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
        cursor = self._collection(entity).find(self._query(entity, filters))
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
        return await self._collection(entity).count_documents(self._query(entity, filters))

    # -------------------------------------------------------------- builder
    def _check_supported(self, spec: Any) -> None:
        """Chặn phần builder KHÔNG dịch được sang Mongo, nói luôn cách thay.

        Mongo có `$lookup`, nhưng nó trả về MẢNG LỒNG chứ không phải dòng phẳng
        như JOIN. Giả lập cho giống sẽ đúng ở demo và sai ở production, nên thà
        nói không — và ở Mongo thì `include`/`nest_under` mới là cách đúng: cả
        hai chạy bằng câu lệnh riêng rồi ghép trong Python, không cần `$lookup`.
        """
        from fastapi_modular.infrastructure.database.query import Aggregate

        if spec.joins:
            ten = spec.joins[0].entity.__name__
            raise BadRequestError(
                f"MongoDB không có JOIN. Cần dữ liệu của {ten} thì dùng "
                f"`.include({ten})` (gắn vào kết quả) hoặc `.nest_under({ten})` "
                f"(đảo chiều) — cả hai chạy được trên Mongo. Cần LỌC theo cột của "
                f"{ten} thì phải đổi APP_DB__DRIVER sang postgres/sqlite."
            )
        if spec.groups or spec.havings or any(
            isinstance(x, Aggregate) for x in spec.selects.values()
        ):
            raise BadRequestError(
                "MongoDB chưa hỗ trợ `group_by`/`having`/hàm gộp trong builder. "
                "Dùng aggregation pipeline của Mongo qua motor, hoặc đổi "
                "APP_DB__DRIVER sang postgres/sqlite."
            )
        if spec.distinct:
            raise BadRequestError(
                "MongoDB chưa hỗ trợ `.distinct()` trong builder. Không có JOIN "
                "thì dòng trùng cũng hiếm khi sinh ra."
            )

    def _find_args(self, spec: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """(điều kiện, projection) cho `find()`."""
        loc = _and([_condition_to_filter(spec.entity, c) for c in spec.conditions])
        if not spec.selects:
            return loc, None

        # Projection: xin đúng cột cần. `_id` thì Mongo luôn trả về nên tắt tay
        # nếu không ai xin — không đổi kết quả, chỉ bớt dữ liệu truyền về.
        chieu: dict[str, Any] = {}
        for column in spec.selects.values():
            chieu[_field(column.field)] = 1
        if "_id" not in chieu:
            chieu["_id"] = 0
        return loc, chieu

    async def run_query(self, spec: Any) -> list[Any]:
        self._check_supported(spec)
        loc, chieu = self._find_args(spec)

        cursor = self._collection(spec.entity).find(loc, chieu)
        if spec.orders:
            cursor = cursor.sort([
                (_field(o.column.field), -1 if o.descending else 1) for o in spec.orders
            ])
        if spec.offset:
            cursor = cursor.skip(spec.offset)
        if spec.limit is not None:
            cursor = cursor.limit(spec.limit)

        docs = [doc async for doc in cursor]
        if not spec.selects:
            return [from_document(spec.entity, _from_mongo(d)) for d in docs]

        fields = mapping_for(spec.entity).fields
        return [
            {
                ten: coerce_value(fields[c.field], d.get(_field(c.field)))
                for ten, c in spec.selects.items()
            }
            for d in docs
        ]

    async def count_query(self, spec: Any) -> int:
        self._check_supported(spec)
        loc, _ = self._find_args(spec)
        return int(await self._collection(spec.entity).count_documents(loc))

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
        await self._check_references(entity, obj)
        if not getattr(obj, "id", None):
            obj.id = uuid.uuid4().hex  # type: ignore[attr-defined]
        doc = _to_mongo(to_document(obj))
        await self._collection(entity).replace_one({"_id": doc["_id"]}, doc, upsert=True)
        return obj

    async def _check_references(self, entity: type, obj: Any) -> None:
        """Cha phải có thật — MongoDB không tự kiểm, nên khung kiểm.

        Cùng lý do với `_cascade`: nếu ở đây cho ghi thoải mái thì `fam test`
        (chạy trên memory, có kiểm) xanh, còn Mongo lặng lẽ nhận một
        `camera_id` không trỏ tới đâu — và chỗ dữ liệu rác đó chỉ lộ ra khi có
        người join.

        Giá phải trả: mỗi khoá ngoại KHÁC NULL tốn thêm một lượt tìm theo
        `_id`. Đo ba lần trên container local, 400 lượt ghi mỗi lần: không khoá
        ngoại 0,21-0,46 ms/ghi, một khoá ngoại 0,61-0,75 ms/ghi — tức là xấp xỉ
        gấp đôi. Cần tốc độ hơn ràng buộc thì bỏ `reference(...)` khỏi cột đó,
        khi ấy khung cũng thôi cascade luôn.

        Đây KHÔNG phải ràng buộc thật: giữa lúc kiểm và lúc ghi vẫn có kẽ hở,
        và cha có thể bị xoá ngay sau đó. Cần bảo đảm thật thì dùng SQL.
        """
        for column, ref in mapping_for(entity).references:
            value = getattr(obj, column, None)
            if value is None:                      # NULL nghĩa là "chưa gắn", hợp lệ
                continue
            cha = await self._collection(ref.target).find_one({"_id": value}, {"_id": 1})
            if cha is None:
                raise ConflictError(
                    f"{entity.__name__}.{column} = {value!r} nhưng không có "
                    f"{ref.target.__name__} nào mang id đó."
                )

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

        query = self._query(entity, filters)
        if mapping_for(entity).references or _co_con(entity):
            ids = [doc["_id"] async for doc in self._collection(entity).find(query, {"_id": 1})]
            await self._cascade(entity, ids)
        result = await self._collection(entity).delete_many(query)
        return int(result.deleted_count)


# ------------------------------------------------------- dịch điều kiện
def _field(name: str) -> str:
    """Tên trường trong document. `id` của entity nằm ở `_id`."""
    return "_id" if name == "id" else name


def _value(value: Any) -> Any:
    """Giá trị đem đi so sánh — Enum lưu bằng `.value` nên phải so bằng `.value`."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [_value(v) for v in value]
    return value


def _like_to_regex(pattern: str) -> str:
    """`LIKE` của SQL -> regex của Mongo.

    Phải escape từng ký tự thường: `like(name, "a.b")` mà để nguyên thì `.`
    thành ký tự đại diện của regex và khớp luôn "axb".
    """
    import re

    out = ["^"]
    for ch in pattern:
        out.append(".*" if ch == "%" else "." if ch == "_" else re.escape(ch))
    out.append("$")
    return "".join(out)


def _and(parts: list[dict[str, Any]]) -> dict[str, Any]:
    if not parts:
        return {}
    return parts[0] if len(parts) == 1 else {"$and": parts}


def _condition_to_filter(entity: type, condition: Any) -> dict[str, Any]:
    """Cây điều kiện của builder -> filter document của Mongo.

    Chỗ dễ sai nhất là NULL. Mongo coi "thiếu trường" và "null" là một giá trị
    bình thường, nên `{n: {"$ne": 1}}` TRẢ VỀ cả document không có `n` — đo
    được. SQL thì `NULL != 1` là không-đúng nên loại. Ở đây phải chèn thêm
    `$ne: None` cho `ne`/`nin`/`NOT`, nếu không cùng một câu lệnh cho hai kết
    quả khác nhau giữa postgres và mongo.
    """
    from fastapi_modular.infrastructure.database.query import Column, Compare, Group, Not

    if isinstance(condition, Group):
        parts = [_condition_to_filter(entity, p) for p in condition.parts]
        return _and(parts) if condition.op == "and" else {"$or": parts}
    if isinstance(condition, Not):
        return {"$nor": [_condition_to_filter(entity, condition.part)]}
    if not isinstance(condition, Compare):
        raise BadRequestError(f"Điều kiện lạ: {condition!r}")

    column = condition.column
    if not isinstance(column, Column):
        raise BadRequestError(f"MongoDB chưa hỗ trợ điều kiện trên {column!r}")
    if column.entity is not entity:
        raise BadRequestError(
            f"MongoDB không có JOIN nên không lọc được theo {column!r}."
        )

    name, op, value = _field(column.field), condition.op, condition.value
    if isinstance(value, Column):
        # So cột với cột trong CÙNG một document: phải dùng $expr.
        toan_tu = {"eq": "$eq", "ne": "$ne", "gt": "$gt", "gte": "$gte",
                   "lt": "$lt", "lte": "$lte"}.get(op)
        if toan_tu is None:
            raise BadRequestError(f"MongoDB chưa hỗ trợ `{op}` giữa hai cột.")
        return {"$expr": {toan_tu: [f"${name}", f"${_field(value.field)}"]}}

    value = _value(value)
    if op == "isnull":
        return {name: None} if value else {name: {"$ne": None}}
    if op == "eq":
        return {name: value}
    if op == "ne":
        return {"$and": [{name: {"$ne": value}}, {name: {"$ne": None}}]}
    if op in ("gt", "gte", "lt", "lte"):
        return {name: {f"${op}": value}}
    if op == "in":
        return {name: {"$in": value}}
    if op == "nin":
        return {"$and": [{name: {"$nin": value}}, {name: {"$ne": None}}]}
    if op == "between":
        return {name: {"$gte": value[0], "$lte": value[1]}}
    if op in ("like", "ilike"):
        loc = {"$regex": _like_to_regex(value)}
        if op == "ilike":
            loc["$options"] = "i"
        return {name: loc}
    raise BadRequestError(f"Toán tử {op!r} chưa cài cho MongoDB")


def _co_con(parent: type) -> bool:
    """Có entity nào trỏ tới `parent` không — để khỏi truy vấn id thừa."""
    return any(
        ref.target is parent
        for child in list(_ENTITIES.values())
        for _, ref in mapping_for(child).references
    )
