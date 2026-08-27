"""Controller viết bằng `def` thường — FastAPI đẩy sang thread pool, khung cũng vậy.

Vì sao có file này: khung bọc mọi method thành một endpoint `async` rồi `await`
thẳng kết quả, nên `def` thường nổ `TypeError: object dict can't be used in
'await' expression`. Đó là mất một tính năng của FastAPI, không phải chuyện nhỏ:
người ta khai `def` khi bên trong có thứ CHẶN (`requests`, `cv2`, driver đồng
bộ), và nếu khung gọi thẳng trên vòng lặp thì một request chặn đứng cả tiến
trình.

Ba điều phải giữ, và đó là ba nhóm test dưới đây: chạy được, chạy NGOÀI vòng
lặp, và nhiều request chạy SONG SONG.
"""

from __future__ import annotations

import asyncio
import functools
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from fastapi_modular.core.config import DatabaseSettings, Settings
from fastapi_modular.core.container import Scope, container, injectable
from fastapi_modular.core.controller import build_router, controller, get, post
from fastapi_modular.core.error_handlers import register_error_handlers
from fastapi_modular.core.exceptions import ForbiddenError


def tren_vong_lap() -> bool:
    """Đang chạy TRÊN vòng lặp sự kiện hay ở một thread khác?

    Không so `threading.get_ident()` với luồng import: `TestClient` chạy vòng
    lặp ở một luồng RIÊNG, nên phép so đó cho `False` với cả `async def` lẫn
    `def` thường — test xanh vì lý do sai. `get_running_loop()` chỉ thành công
    trên đúng luồng đang chạy vòng lặp.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


@injectable
class DbCong:
    def cong(self, a: int, b: int) -> int:
        return a + b


@injectable(scope=Scope.REQUEST)
class DauRequest:
    def __init__(self) -> None:
        self.value = id(self)


@injectable
class GuardDongBo:
    """Guard `def` thường — cũng từng nổ y hệt handler."""

    def check(self, request: Request) -> None:
        return None


@injectable
class GuardDongBoChan:
    def check(self, request: Request) -> None:
        raise ForbiddenError("không cho vào")


def boc_dong_bo(fn):
    """Decorator `def` bọc `async def` — `iscoroutinefunction` nhìn thành đồng bộ."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)          # trả về coroutine, chưa chạy

    return wrapper


@controller(prefix="/db")
class DongBoController:
    def __init__(self, cong: DbCong) -> None:
        self._cong = cong

    @get("/tinh")
    def tinh(self, a: int = 1) -> dict:
        return {"tong": self._cong.cong(a, 2), "tren_loop": tren_vong_lap()}

    @get("/bat-dong-bo")
    async def bat_dong_bo(self) -> dict:
        return {"tren_loop": tren_vong_lap()}

    @get("/cham")
    def cham(self) -> dict:
        time.sleep(0.3)                      # CHẶN thật, không phải asyncio.sleep
        return {"xong": True}

    @get("/scope")
    def scope(self) -> dict:
        # Provider request-scoped phải resolve được TỪ TRONG thread — context
        # được sao sang thread, nếu không thì mọi thứ dựa trên ContextVar
        # (request-id, transaction của request) sẽ đứt đúng ở đây.
        return {"resolve_duoc": container.resolve(DauRequest).value is not None}

    @get("/khong-tra-ve", status_code=204)
    def khong_tra_ve(self) -> None:
        return None

    @get("/tra-list")
    def tra_list(self) -> list[int]:
        return [1, 2, 3]

    @get("/bi-boc")
    @boc_dong_bo
    async def bi_boc(self) -> dict:
        return {"ok": True}

    @post("/no")
    def no(self) -> dict:
        raise ValueError("nổ trong thread")


@controller(prefix="/dbg", guards=[GuardDongBo])
class GuardDongBoController:
    @get("/qua")
    def qua(self) -> dict:
        return {"qua_guard": True}

    @get("/bi-chan", guards=[GuardDongBoChan])
    def bi_chan(self) -> dict:
        return {"khong": "toi day"}


def _app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app, debug=False)
    app.include_router(build_router(DongBoController), prefix="/api")
    app.include_router(build_router(GuardDongBoController), prefix="/api")
    container.override(Settings, Settings(APP_DB=DatabaseSettings(driver="memory")))
    return app


@pytest.fixture
def client():
    with TestClient(_app()) as client:
        yield client


# ------------------------------------------------------------------ chạy được
def test_def_thuong_chay_duoc(client):
    """Ca chính: trước đây nổ TypeError ngay ở dòng `await fn(...)`."""
    assert client.get("/api/db/tinh", params={"a": 5}).json()["tong"] == 7


def test_async_def_khong_bi_day_vao_thread_pool(monkeypatch):
    """Đếm thẳng số lần dùng thread pool, vì nhìn từ handler thì không phân biệt được.

    Đẩy `async def` vào pool vẫn "chạy được": gọi nó trong thread chỉ TẠO ra
    coroutine, rồi coroutine ấy được await trên vòng lặp như thường — nên thân
    hàm vẫn thấy mình ở trên loop. Cái mất là một chỗ trong pool 40 thread cho
    mỗi request, và khi tải cao thì chính pool đó thành nút cổ chai. Chỉ có
    cách đếm mới thấy.
    """
    from fastapi_modular.core import controller as controller_module

    calls: list[str] = []
    that = controller_module.run_in_threadpool

    async def dem(fn, *args, **kwargs):
        calls.append(getattr(fn, "__name__", repr(fn)))
        return await that(fn, *args, **kwargs)

    monkeypatch.setattr(controller_module, "run_in_threadpool", dem)

    with TestClient(_app()) as client:
        client.get("/api/db/bat-dong-bo")
        assert calls == [], "async def không được đụng tới thread pool"

        client.get("/api/db/tinh")
        assert calls == ["tinh"], "def thường thì PHẢI đi qua thread pool"


def test_async_def_van_chay_TREN_vong_lap(client):
    """`async def` phải ở nguyên trên vòng lặp — đẩy nó vào thread cũng "chạy
    được" nhưng đốt một chỗ trong pool 40 thread cho mỗi request, và khi tải
    cao thì chính pool đó thành nút cổ chai."""
    assert client.get("/api/db/bat-dong-bo").json() == {"tren_loop": True}


def test_def_thuong_tra_ve_204(client):
    """`-> None` + status_code=204: đường đồng bộ không được tự nhét body vào."""
    response = client.get("/api/db/khong-tra-ve")
    assert response.status_code == 204
    assert response.text == ""


def test_def_thuong_tra_ve_list(client):
    assert client.get("/api/db/tra-list").json() == [1, 2, 3]


def test_loi_trong_thread_noi_len_dung():
    """Lỗi ném trong thread phải nổi lên nguyên vẹn, không bị nuốt thành treo.

    Hai cách nhìn, kiểm cả hai: để nguyên thì exception thật lọt lên tận đây,
    còn khi app tự bắt (`raise_server_exceptions=False`) thì thành 500 đúng
    khuôn lỗi chung.
    """
    with TestClient(_app()) as client, pytest.raises(ValueError, match="nổ trong thread"):
        client.post("/api/db/no")

    with TestClient(_app(), raise_server_exceptions=False) as client:
        response = client.post("/api/db/no")
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"


def test_decorator_def_boc_async_van_chay(client):
    """`functools.wraps` không đổi cờ coroutine, nên hàm bọc bị nhìn thành đồng bộ.

    Chạy ở thread rồi nhận lại coroutine thì await nốt — còn hơn để người dùng
    nhận "coroutine was never awaited" và một response rỗng khó hiểu.
    """
    assert client.get("/api/db/bi-boc").json() == {"ok": True}


# --------------------------------------------------- chạy NGOÀI vòng lặp
def test_def_thuong_khong_chay_tren_vong_lap(client):
    """Cả điểm của tính năng này: `def` thường phải rời khỏi vòng lặp sự kiện."""
    assert client.get("/api/db/tinh").json()["tren_loop"] is False


def test_request_scope_van_toi_duoc_trong_thread(client):
    assert client.get("/api/db/scope").json() == {"resolve_duoc": True}


# ----------------------------------------------------------- guard đồng bộ
def test_guard_def_thuong_chay_duoc(client):
    assert client.get("/api/dbg/qua").json() == {"qua_guard": True}


def test_guard_def_thuong_van_chan_duoc(client):
    response = client.get("/api/dbg/bi-chan")
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


# --------------------------------------------------------------- song song
async def test_nhieu_request_dong_bo_chay_song_song():
    """Bốn request chặn 0,3s: song song thì ~0,3s, chạy trên loop thì ~1,2s.

    Đây là phép đo phân biệt "hết lỗi" với "đúng như FastAPI" — gọi thẳng hàm
    đồng bộ trong endpoint async cũng hết lỗi, nhưng bốn request sẽ nối đuôi.
    """
    import httpx

    app = _app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        moc = time.perf_counter()
        await asyncio.gather(*(client.get("/api/db/cham") for _ in range(4)))
        giay = time.perf_counter() - moc

    assert giay < 0.9, f"4 x 0,3s mất {giay:.2f}s — vẫn đang chặn vòng lặp"
