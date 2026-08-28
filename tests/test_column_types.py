"""Test kiểu cột chữ: `column(length=...)` cho VARCHAR(n) và `column(text=True)` cho TEXT.

Hai lớp phép kiểm, và phải có CẢ HAI:

- **DDL** (SQLite/Postgres thật): cột sinh ra đúng kiểu, đổi độ dài thì bị kêu.
- **Chặn lúc ghi** (memory/SQLite/MongoDB): chuỗi quá dài bị chặn ở tầng khung.
  Chỉ Postgres tự báo lỗi — đo được: ghi 60 ký tự vào `VARCHAR(50)` thì SQLite
  nhận còn Postgres ném `StringDataRightTruncation`. Không chặn ở khung thì
  `fam test` xanh mà production đổ.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import pytest

from fastapi_modular.core.clock import utcnow
from fastapi_modular.core.config import DatabaseSettings
from fastapi_modular.core.container import entity
from fastapi_modular.core.exceptions import BadRequestError
from fastapi_modular.infrastructure.database.base import (
    column,
    mapping_for,
    reference,
)
from fastapi_modular.infrastructure.database.factory import create_backend
from fastapi_modular.infrastructure.database.repository import Repository

HAS_SQLITE = bool(os.getenv("TEST_SQLITE")) and importlib.util.find_spec("aiosqlite") is not None
MONGO_DSN = os.getenv("TEST_MONGO_DSN", "")
HAS_MONGO = bool(MONGO_DSN) and importlib.util.find_spec("motor") is not None
POSTGRES_DSN = os.getenv("TEST_POSTGRES_DSN", "")
HAS_POSTGRES = bool(POSTGRES_DSN) and importlib.util.find_spec("asyncpg") is not None


class Kind(Enum):
    SHORT = "ab"
    LONG = "abcdefghij"


@entity()
@dataclass(slots=True)
class ColZone:
    id: str
    name: str = ""
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@entity()
@dataclass(slots=True)
class ColBadge:
    id: str
    code: str = field(default="", metadata=column(length=8))
    note: str = field(default="", metadata=column(text=True))
    free: str = ""                                              # không khai gì
    kind: Kind = field(default=Kind.SHORT, metadata=column(length=4))
    # Khoá ngoại và độ dài trên cùng một cột: gộp hai dict bằng `|`.
    zone_id: str = field(default="", metadata=reference(ColZone) | column(length=36))
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@entity()
@dataclass(slots=True)
class ColOptional:
    """Mọi trường đều `| None` — chỗ khung từng sinh nhầm hết thành VARCHAR."""

    id: str
    port: int | None = None
    score: float | None = None
    live: bool | None = None
    seen_at: datetime | None = None
    kind: Kind | None = None
    note: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


class _Db:
    def __init__(self, backend) -> None:
        self.backend = backend
        self.driver = backend.name


@pytest.fixture(params=[
    "memory",
    pytest.param("sqlite", marks=pytest.mark.skipif(
        not HAS_SQLITE, reason="đặt TEST_SQLITE=1 và cài aiosqlite")),
    pytest.param("mongodb", marks=pytest.mark.skipif(
        not HAS_MONGO, reason="đặt TEST_MONGO_DSN và cài motor")),
    pytest.param("postgres", marks=pytest.mark.skipif(
        not HAS_POSTGRES, reason="đặt TEST_POSTGRES_DSN và cài asyncpg")),
])
async def badges(request, tmp_path):
    if request.param == "memory":
        settings = DatabaseSettings(driver="memory")
    elif request.param == "sqlite":
        settings = DatabaseSettings(
            driver="sqlite", dsn=f"sqlite+aiosqlite:///{tmp_path}/{uuid.uuid4().hex}.db"
        )
    elif request.param == "postgres":
        settings = DatabaseSettings(driver="postgres", dsn=POSTGRES_DSN)
    else:
        settings = DatabaseSettings(
            driver="mongodb", dsn=MONGO_DSN, name=f"fam_col_{uuid.uuid4().hex[:8]}"
        )
    backend = create_backend(settings)
    await backend.startup()
    if hasattr(backend, "create_schema"):
        await backend.create_schema(ColZone, ColBadge, ColOptional)

    db = _Db(backend)
    await Repository(ColZone, db).save(ColZone(id="z1"))
    yield Repository(ColBadge, db)
    if request.param == "mongodb":
        await backend._client.drop_database(backend._database_name)
    if request.param == "postgres":
        # Postgres dùng chung một database cho cả bộ test, nên phải dọn bảng —
        # để lại dữ liệu là test sau đếm nhầm. Bảng con trước, bảng cha sau.
        from sqlalchemy import text as sql_text

        async with backend._engine.begin() as conn:
            for name in ("colbadges", "coloptionals", "colzones"):
                await conn.execute(sql_text(f'DROP TABLE IF EXISTS "{name}" CASCADE'))
    await backend.shutdown()


@pytest.fixture
async def optionals(badges):
    """Repository của `ColOptional`, dùng chung backend với fixture `badges`."""
    return Repository(ColOptional, badges._db)


# ------------------------------------------------- trường `X | None`
async def test_optional_fields_keep_their_type_through_the_database(optionals):
    """`int | None` phải đọc về `int`, không phải `'8080'`.

    Đo được trước khi sửa: cột sinh ra là VARCHAR nên SQLite trả chuỗi, Postgres
    ném `DataError` ngay lúc ghi, còn memory trả đúng số — cùng một entity, ba
    kết quả khác nhau.
    """
    when = utcnow()
    await optionals.save(ColOptional(
        id="o1", port=8080, score=0.75, live=True, seen_at=when, kind=Kind.SHORT, note="x",
    ))
    found = await optionals.get("o1")

    assert found.port == 8080 and isinstance(found.port, int)
    assert found.score == 0.75 and isinstance(found.score, float)
    assert found.live is True
    assert found.kind is Kind.SHORT, "Enum trong Optional cũng phải ép lại"
    assert found.seen_at.tzinfo is not None, "datetime đọc ra luôn kèm múi giờ"
    assert abs((found.seen_at - when).total_seconds()) < 1


async def test_optional_fields_still_accept_none(optionals):
    await optionals.save(ColOptional(id="o2"))
    found = await optionals.get("o2")
    assert (found.port, found.score, found.live, found.seen_at, found.kind) == (
        None, None, None, None, None,
    )


@pytest.mark.skipif(not (HAS_SQLITE or HAS_POSTGRES), reason="cần SQLite hoặc Postgres")
def test_optional_columns_render_the_right_sql_type():
    from sqlalchemy.dialects import postgresql

    from fastapi_modular.infrastructure.database.sql import build_metadata

    table = build_metadata(ColOptional).tables[mapping_for(ColOptional).storage]
    rendered = {
        c.name: c.type.compile(dialect=postgresql.dialect()) for c in table.columns
    }
    assert rendered["port"] == "INTEGER"
    assert rendered["score"] == "FLOAT"
    assert rendered["live"] == "BOOLEAN"
    assert rendered["seen_at"] == "TIMESTAMP WITH TIME ZONE"
    assert rendered["kind"] == "VARCHAR(64)", "Enum trong Optional vẫn là cột Enum"
    assert rendered["note"] == "VARCHAR"


# ------------------------------------------------------------------ khai báo
def test_declaration_readable_from_entity():
    specs = dict(mapping_for(ColBadge).column_specs)
    assert specs["code"].length == 8 and specs["code"].text is False
    assert specs["note"].text is True and specs["note"].length is None
    assert "free" not in specs, "không khai `column(...)` thì không có spec"
    assert specs["zone_id"].length == 36, "gộp với `reference()` không được mất spec"
    assert dict(mapping_for(ColBadge).references)["zone_id"].target is ColZone


@pytest.mark.parametrize("kwargs", [
    {},                              # không khai gì
    {"length": 50, "text": True},    # mâu thuẫn
    {"length": 0},
    {"length": -1},
    {"length": 1.5},
    {"length": True},                # bool là int, nhưng không phải độ dài
])
def test_bad_declaration_refused_at_declaration_time(kwargs):
    """Gõ sai phải chết ngay lúc khai báo, không đợi tới lúc có người ghi."""
    with pytest.raises(BadRequestError):
        column(**kwargs)


def test_length_on_non_text_field_refused():
    @dataclass(slots=True)
    class Broken:
        id: str
        port: int = field(default=0, metadata=column(length=5))

    with pytest.raises(BadRequestError, match="cột chữ"):
        mapping_for(Broken)


# ------------------------------------------------------------------ DDL thật
@pytest.mark.skipif(not (HAS_SQLITE or HAS_POSTGRES), reason="cần SQLite hoặc Postgres")
async def test_ddl_renders_varchar_and_text(tmp_path):
    """Cột sinh ra dưới database đúng kiểu đã khai — đọc lại bằng inspector."""
    from sqlalchemy import inspect as sa_inspect

    table = f"colddl_{uuid.uuid4().hex[:8]}"

    @entity(name=table)
    @dataclass(slots=True)
    class Ddl:
        id: str
        code: str = field(default="", metadata=column(length=8))
        note: str = field(default="", metadata=column(text=True))
        free: str = ""

    if HAS_POSTGRES:
        settings = DatabaseSettings(driver="postgres", dsn=POSTGRES_DSN)
    else:
        settings = DatabaseSettings(
            driver="sqlite", dsn=f"sqlite+aiosqlite:///{tmp_path}/{table}.db"
        )
    backend = create_backend(settings)
    await backend.startup()
    await backend.create_schema(Ddl)
    try:
        async with backend._engine.connect() as conn:
            found = await conn.run_sync(
                lambda c: {x["name"]: x["type"] for x in sa_inspect(c).get_columns(table)}
            )
            dialect = conn.engine.dialect
            rendered = {name: type_.compile(dialect=dialect) for name, type_ in found.items()}
    finally:
        if HAS_POSTGRES:
            from sqlalchemy import text as sql_text

            async with backend._engine.begin() as conn:
                await conn.execute(sql_text(f'DROP TABLE IF EXISTS "{table}"'))
        await backend.shutdown()

    assert rendered["code"] == "VARCHAR(8)"
    assert rendered["note"] == "TEXT"
    assert rendered["free"] == "VARCHAR", "không khai gì thì vẫn là VARCHAR trơn"


@pytest.mark.skipif(not (HAS_SQLITE or HAS_POSTGRES), reason="cần SQLite hoặc Postgres")
async def test_length_change_is_warned_not_applied(tmp_path, monkeypatch):
    """Đổi độ dài trong entity: khung KHÔNG tự đổi cột, nhưng phải kêu to."""
    from fastapi_modular.infrastructure.database import sql as sql_module

    records: list[tuple[str, dict]] = []

    class _CatchLog:
        def __getattr__(self, level):
            def write(event: str, **kw) -> None:
                records.append((event, kw))
            return write

    monkeypatch.setattr(sql_module, "log", _CatchLog())

    table = f"colevo_{uuid.uuid4().hex[:8]}"

    @dataclass(slots=True)
    class Narrow:
        id: str
        code: str = field(default="", metadata=column(length=8))
        free: str = ""

    @dataclass(slots=True)
    class Wide:
        id: str
        code: str = field(default="", metadata=column(length=64))
        free: str = ""

    Narrow.__storage_name__ = Wide.__storage_name__ = table

    def _settings():
        if HAS_POSTGRES:
            return DatabaseSettings(driver="postgres", dsn=POSTGRES_DSN, schema_mode="sync")
        return DatabaseSettings(
            driver="sqlite", dsn=f"sqlite+aiosqlite:///{tmp_path}/{table}.db",
            schema_mode="sync",
        )

    first = create_backend(_settings())
    await first.startup()
    await first.create_schema(Narrow)
    await first.shutdown()

    records.clear()
    second = create_backend(_settings())
    await second.startup()
    try:
        await second.create_schema(Wide)
    finally:
        if HAS_POSTGRES:
            from sqlalchemy import text as sql_text

            async with second._engine.begin() as conn:
                await conn.execute(sql_text(f'DROP TABLE IF EXISTS "{table}"'))
        await second.shutdown()

    warned = [kw["column"] for name, kw in records if name == "db.column_type_mismatch"]
    assert any("VARCHAR(8) -> VARCHAR(64)" in item for item in warned), warned
    assert not any(".free" in item for item in warned), (
        "cột không khai độ dài không được kêu oan"
    )



@pytest.mark.skipif(not (HAS_SQLITE or HAS_POSTGRES), reason="cần SQLite hoặc Postgres")
async def test_existing_length_is_not_warned_when_entity_declares_none(tmp_path, monkeypatch):
    """Bảng cũ có VARCHAR(50), entity để `str` trơn: KHÔNG được kêu.

    Đây là bảng của mọi người dùng đang có sẵn. Kêu ở đây thì mỗi lần khởi động
    lại có một dòng cảnh báo không ai sửa được, và cảnh báo thật sẽ chìm theo.
    """
    from sqlalchemy import text as sql_text

    from fastapi_modular.infrastructure.database import sql as sql_module

    records: list[tuple[str, dict]] = []

    class _CatchLog:
        def __getattr__(self, level):
            def write(event: str, **kw) -> None:
                records.append((event, kw))
            return write

    table = f"colold_{uuid.uuid4().hex[:8]}"

    @dataclass(slots=True)
    class Plain:
        id: str
        code: str = ""

    Plain.__storage_name__ = table

    if HAS_POSTGRES:
        settings = DatabaseSettings(driver="postgres", dsn=POSTGRES_DSN, schema_mode="sync")
    else:
        settings = DatabaseSettings(
            driver="sqlite", dsn=f"sqlite+aiosqlite:///{tmp_path}/{table}.db",
            schema_mode="sync",
        )
    backend = create_backend(settings)
    await backend.startup()
    async with backend._engine.begin() as conn:
        await conn.execute(sql_text(
            f'CREATE TABLE "{table}" (id VARCHAR NOT NULL PRIMARY KEY, code VARCHAR(50))'
        ))
    monkeypatch.setattr(sql_module, "log", _CatchLog())
    try:
        await backend.create_schema(Plain)
    finally:
        monkeypatch.undo()
        async with backend._engine.begin() as conn:
            await conn.execute(sql_text(f'DROP TABLE IF EXISTS "{table}"'))
        await backend.shutdown()

    warned = [kw["column"] for name, kw in records if name == "db.column_type_mismatch"]
    assert warned == [], warned


# ------------------------------------------------------------- chặn lúc ghi
async def test_too_long_refused_before_write(badges):
    with pytest.raises(BadRequestError, match="quá 8 ký tự"):
        await badges.save(ColBadge(id="b1", code="123456789", zone_id="z1"))
    assert await badges.count() == 0, "bị chặn thì không được ghi gì cả"


async def test_exactly_at_limit_is_allowed(badges):
    await badges.save(ColBadge(id="b1", code="12345678", zone_id="z1"))
    found = await badges.get("b1")
    assert found is not None and found.code == "12345678"


async def test_text_field_takes_a_very_long_string(badges):
    await badges.save(ColBadge(id="b1", note="x" * 20_000, zone_id="z1"))
    found = await badges.get("b1")
    assert found is not None and len(found.note) == 20_000


async def test_field_without_length_is_unbounded(badges):
    await badges.save(ColBadge(id="b1", free="x" * 20_000, zone_id="z1"))
    found = await badges.get("b1")
    assert found is not None and len(found.free) == 20_000


async def test_enum_counted_by_its_value(badges):
    """Enum lưu bằng `.value`, nên độ dài phải đo trên `.value` chứ không phải tên."""
    with pytest.raises(BadRequestError, match="quá 4 ký tự"):
        await badges.save(ColBadge(id="b1", kind=Kind.LONG, zone_id="z1"))
    await badges.save(ColBadge(id="b2", kind=Kind.SHORT, zone_id="z1"))


async def test_update_refuses_too_long(badges):
    await badges.save(ColBadge(id="b1", code="ok", zone_id="z1"))
    with pytest.raises(BadRequestError, match="quá 8 ký tự"):
        await badges.update("b1", code="123456789")
    assert (await badges.get("b1")).code == "ok", "bị chặn thì dữ liệu cũ còn nguyên"


async def test_update_where_refuses_too_long(badges):
    await badges.save(ColBadge(id="b1", code="ok", zone_id="z1"))
    with pytest.raises(BadRequestError, match="quá 8 ký tự"):
        await badges.update_where({"zone_id": "z1"}, code="123456789")
    assert (await badges.get("b1")).code == "ok"


@pytest.mark.skipif(not HAS_POSTGRES, reason="đặt TEST_POSTGRES_DSN")
async def test_postgres_agrees_with_the_framework_check():
    """Postgres tự chặn quá dài — khung chỉ chặn SỚM HƠN, cùng một kết luận."""
    from sqlalchemy import insert

    table = f"colpg_{uuid.uuid4().hex[:8]}"

    @entity(name=table)
    @dataclass(slots=True)
    class PgBadge:
        id: str
        code: str = field(default="", metadata=column(length=8))

    backend = create_backend(DatabaseSettings(driver="postgres", dsn=POSTGRES_DSN))
    await backend.startup()
    await backend.create_schema(PgBadge)
    try:
        repo = Repository(PgBadge, _Db(backend))
        with pytest.raises(BadRequestError):
            await repo.save(PgBadge(id="p1", code="123456789"))

        # Đi thẳng xuống driver, không qua khung: chính Postgres phải nổ.
        with pytest.raises(Exception) as bad:
            async with backend._engine.begin() as conn:
                await conn.execute(
                    insert(backend._table(PgBadge)).values(id="p2", code="123456789")
                )
        assert "too long" in str(bad.value) or "StringDataRightT" in str(bad.value)
    finally:
        from sqlalchemy import text as sql_text

        async with backend._engine.begin() as conn:
            await conn.execute(sql_text(f'DROP TABLE IF EXISTS "{table}"'))
        await backend.shutdown()
