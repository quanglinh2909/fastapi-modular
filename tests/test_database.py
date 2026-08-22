"""Test tầng database: cấu hình, factory, và Repository trên backend memory."""

from __future__ import annotations

import pytest

from fastapi_modular.core.config import DatabaseSettings, Settings
from fastapi_modular.infrastructure.database.factory import create_backend
from fastapi_modular.infrastructure.database.repository import Database, Repository


@pytest.mark.parametrize(
    ("driver", "expected"),
    [
        ("sqlite", "sqlite+aiosqlite:///./data/app.db"),
        ("postgres", "postgresql+asyncpg://postgres:postgres@localhost:5432/app"),
        ("mongodb", "mongodb://localhost:27017"),
    ],
)
def test_dsn_mac_dinh_theo_driver(driver, expected):
    assert DatabaseSettings(driver=driver).resolved_dsn == expected


def test_dsn_tu_dat_duoc_uu_tien():
    settings = DatabaseSettings(driver="postgres", dsn="postgresql+asyncpg://x/y")
    assert settings.resolved_dsn == "postgresql+asyncpg://x/y"


def test_driver_khong_ho_tro():
    with pytest.raises(RuntimeError, match="không hỗ trợ"):
        create_backend(DatabaseSettings.model_construct(driver="oracle"))


def test_canh_bao_cau_hinh_prod():
    problems = Settings(
        APP_ENV="prod",
        APP_DEBUG=True,
        APP_DB={"driver": "memory", "schema_mode": "sync", "drop_columns": True},
    ).check_production_safety()
    joined = " ".join(problems)
    assert "cors.allow_origins" in joined
    assert "debug=True" in joined
    assert "memory" in joined
    assert "schema_mode" in joined
    assert "drop_columns" in joined
    assert "ws.adapter" in joined


def test_prod_cau_hinh_dung_thi_khong_canh_bao():
    settings = Settings(
        APP_ENV="prod",
        APP_DEBUG=False,
        APP_CORS={"allow_origins": ["https://app.example.com"]},
        APP_DB={"driver": "postgres", "schema_mode": "off"},
        APP_WS={"adapter": "redis"},
    )
    assert settings.check_production_safety() == []


@pytest.fixture
async def repo(settings) -> Repository:
    database = Database(settings)
    await database.startup()
    from src.api.users.entities.user_model import User

    yield Repository(User, database)
    await database.shutdown()


@pytest.mark.asyncio
async def test_repository_crud(repo):
    from src.api.users.entities.user_model import User

    saved = await repo.save(User(id="", email="a@b.co", full_name="A"))
    assert saved.id, "save() phải tự sinh id"

    assert (await repo.get(saved.id)).email == "a@b.co"
    assert await repo.count() == 1
    assert await repo.exists(email="a@b.co")
    assert not await repo.exists(email="khong@co.gi")

    await repo.save(User(id="", email="c@d.co", full_name="C"))
    assert len(await repo.find(limit=1)) == 1
    assert (await repo.find_one(email="c@d.co")).full_name == "C"

    # match= cho điều kiện vượt ngoài so sánh bằng nhau
    found = await repo.find(match=lambda u: u.email.endswith("d.co"))
    assert len(found) == 1

    # None nghĩa là không lọc
    assert await repo.count(email=None) == 2

    assert await repo.delete(saved.id) is True
    assert await repo.delete(saved.id) is False
    assert await repo.delete_where(email="c@d.co") == 1
    assert await repo.count() == 0
