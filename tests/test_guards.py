"""Test khung guard: chặn request, cộng dồn guard, và Principal theo request."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from fastapi_modular.core.config import DatabaseSettings, Settings
from fastapi_modular.core.container import Scope, container, injectable, request_scope
from fastapi_modular.core.controller import build_router, controller, get
from fastapi_modular.core.exceptions import ForbiddenError, UnauthorizedError
from fastapi_modular.core.guards import Principal, RequireHeader, current_principal

CHAY: list[str] = []


@injectable
class GuardMotc:
    async def check(self, request: Request) -> None:
        CHAY.append("controller")


@injectable
class GuardHai:
    async def check(self, request: Request) -> None:
        CHAY.append("route")


@injectable
class GuardChan:
    async def check(self, request: Request) -> None:
        raise ForbiddenError("không cho vào")


@controller(prefix="/thu", tags=["thu"], guards=[GuardMotc])
class ThuController:
    @get("/thu-tu", guards=[GuardHai])
    async def thu_tu(self) -> dict[str, list[str]]:
        return {"da_chay": list(CHAY)}

    @get("/bi-chan", guards=[GuardChan])
    async def bi_chan(self) -> dict[str, str]:
        return {"khong": "toi day"}

    @get("/danh-tinh", guards=[RequireHeader])
    async def danh_tinh(self) -> dict[str, object]:
        principal = current_principal()
        return {"id": principal.id, "roles": sorted(principal.roles)}

    @get("/an-danh")
    async def an_danh(self) -> dict[str, object]:
        return {"is_authenticated": current_principal().is_authenticated}


@pytest.fixture
def guard_client():
    from fastapi import FastAPI

    from fastapi_modular.core.error_handlers import register_error_handlers

    app = FastAPI()
    register_error_handlers(app, debug=False)
    app.include_router(build_router(ThuController), prefix="/api")
    container.override(Settings, Settings(APP_DB=DatabaseSettings(driver="memory")))
    CHAY.clear()
    with TestClient(app) as client:
        yield client


def test_guard_controller_chay_truoc_guard_route(guard_client):
    body = guard_client.get("/api/thu/thu-tu").json()
    assert body["da_chay"] == ["controller", "route"]


def test_guard_nem_loi_thi_chan_request(guard_client):
    response = guard_client.get("/api/thu/bi-chan")
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_guard_dien_principal_cho_request(guard_client):
    response = guard_client.get("/api/thu/danh-tinh", headers={"X-Client-Id": "abc"})
    assert response.status_code == 200
    assert response.json() == {"id": "abc", "roles": ["client"]}


def test_thieu_header_thi_401(guard_client):
    response = guard_client.get("/api/thu/danh-tinh")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


def test_khong_co_guard_thi_principal_an_danh(guard_client):
    assert guard_client.get("/api/thu/an-danh").json() == {"is_authenticated": False}


def test_principal_khong_ro_ri_giua_hai_request(guard_client):
    first = guard_client.get("/api/thu/danh-tinh", headers={"X-Client-Id": "nguoi-1"})
    assert first.json()["id"] == "nguoi-1"
    # request sau không mang header -> phải là ẩn danh, không thấy "nguoi-1"
    assert guard_client.get("/api/thu/an-danh").json()["is_authenticated"] is False


@pytest.mark.asyncio
async def test_principal_la_request_scoped():
    async with request_scope():
        a = current_principal()
        a.assume(id="x", roles={"admin"})
        assert current_principal() is a
    async with request_scope():
        assert current_principal().id is None


@pytest.mark.asyncio
async def test_require_role():
    async with request_scope():
        principal = current_principal()
        with pytest.raises(UnauthorizedError):
            principal.require_role("admin")

        principal.assume(id="u1", roles={"user"})
        with pytest.raises(ForbiddenError):
            principal.require_role("admin")

        principal.assume(id="u1", roles={"admin"})
        principal.require_role("admin")          # không ném


def test_container_chan_singleton_om_principal():
    """Principal theo request; service singleton giữ nó là rò dữ liệu."""
    from fastapi_modular.core.container import Container

    @injectable
    class ServiceRoRi:
        def __init__(self, principal: Principal) -> None: ...

    with pytest.raises(RuntimeError, match="request-scoped"):
        Container().resolve(ServiceRoRi)


def test_scope_cua_principal():
    assert container.scope_of(Principal) is Scope.REQUEST
