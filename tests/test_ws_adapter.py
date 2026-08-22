"""Test adapter phát tin xuyên worker.

Bài test quan trọng nhất của lớp WebSocket khi lên production: sổ kết nối nằm
trong RAM từng tiến trình, nên hai worker chỉ "thấy" nhau qua adapter.

Mặc định bỏ qua. Chạy thật bằng:

    docker run -d --name redis-test -p 6380:6379 redis:7-alpine
    make install-redis
    TEST_REDIS_URL=redis://localhost:6380/0 make test
"""

from __future__ import annotations

import asyncio
import importlib.util
import os

import pytest
from starlette.websockets import WebSocketState

from fastapi_modular.core.config import DatabaseSettings, Settings, WebSocketSettings
from fastapi_modular.core.exceptions import ComponentNotEnabledError
from fastapi_modular.core.websocket import WebSocketServer
from fastapi_modular.core.websocket.adapter import RedisAdapter
from fastapi_modular.core.websocket.socket import Socket

CO_REDIS = importlib.util.find_spec("redis") is not None
REDIS_URL = os.getenv("TEST_REDIS_URL")


@pytest.mark.skipif(CO_REDIS, reason="chỉ kiểm tra được khi CHƯA cài thư viện redis")
async def test_chua_cai_redis_thi_bao_ro_cach_khac_phuc():
    adapter = RedisAdapter("redis://localhost:6379/0", "ws:test")
    with pytest.raises(ComponentNotEnabledError) as loi:
        await adapter.start(lambda _: None)
    assert "make install-redis" in str(loi.value)


class _WsGia:
    """WebSocket giả — chỉ cần đủ để Socket xếp tin vào hàng đợi.

    Cố ý khai lại ở đây thay vì import từ tests/test_websocket.py: import chéo
    giữa hai file test khiến module kia chạy lần hai, và decorator @gateway
    trong đó sẽ đăng ký trùng tên provider.
    """

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.client_state = WebSocketState.CONNECTED

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.client_state = WebSocketState.DISCONNECTED


def _settings(channel: str) -> Settings:
    return Settings(
        APP_DB=DatabaseSettings(driver="memory"),
        APP_WS=WebSocketSettings(adapter="redis", redis_url=REDIS_URL or "", channel=channel),
    )


def _gan_socket(server: WebSocketServer, room: str) -> Socket:
    namespace = server.namespace("/ws/chat")
    socket = Socket(_WsGia(), namespace)   # type: ignore[arg-type]
    namespace.add(socket)
    socket.join(room)
    return socket


async def _cho(socket: Socket, expected: int, timeout: float = 3.0) -> int:
    """Pub/sub là bất đồng bộ — chờ tới khi tin sang tới worker kia."""
    han = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < han:
        if socket.pending >= expected:
            break
        await asyncio.sleep(0.02)
    return socket.pending


@pytest.mark.skipif(not (CO_REDIS and REDIS_URL), reason="cần redis + TEST_REDIS_URL")
async def test_tin_di_duoc_sang_worker_khac():
    worker_a = WebSocketServer(_settings("ws:test:1"))
    worker_b = WebSocketServer(_settings("ws:test:1"))
    await worker_a.startup()
    await worker_b.startup()

    socket_a = _gan_socket(worker_a, "r")
    socket_b = _gan_socket(worker_b, "r")

    try:
        assert await worker_a.emit("e", {"x": 1}, namespace="/ws/chat", room="r") == 1
        assert await _cho(socket_b, 1) == 1, "worker B phải nhận được tin của worker A"
        # Không được nhận lại tin của chính mình: đã gửi tại chỗ rồi.
        assert await _cho(socket_a, 2, timeout=0.5) == 1
    finally:
        await worker_a.shutdown()
        await worker_b.shutdown()


@pytest.mark.skipif(not (CO_REDIS and REDIS_URL), reason="cần redis + TEST_REDIS_URL")
async def test_kenh_khac_nhau_khong_nghe_nham_cua_nhau():
    worker_a = WebSocketServer(_settings("ws:test:2"))
    worker_b = WebSocketServer(_settings("ws:test:khac"))
    await worker_a.startup()
    await worker_b.startup()

    _gan_socket(worker_a, "r")
    socket_b = _gan_socket(worker_b, "r")

    try:
        await worker_a.emit("e", 1, namespace="/ws/chat", room="r")
        assert await _cho(socket_b, 1, timeout=0.7) == 0
    finally:
        await worker_a.shutdown()
        await worker_b.shutdown()


@pytest.mark.skipif(not (CO_REDIS and REDIS_URL), reason="cần redis + TEST_REDIS_URL")
async def test_gui_thang_cho_mot_nguoi_cung_di_xuyen_worker():
    worker_a = WebSocketServer(_settings("ws:test:3"))
    worker_b = WebSocketServer(_settings("ws:test:3"))
    await worker_a.startup()
    await worker_b.startup()

    namespace = worker_b.namespace("/ws/chat")
    socket = Socket(_WsGia(), namespace, user_id="an")   # type: ignore[arg-type]
    namespace.add(socket)
    worker_a.namespace("/ws/chat")   # worker A không có kết nối nào của "an"

    try:
        assert await worker_a.to_user("an", "e", {"x": 1}, namespace="/ws/chat") == 0
        assert await _cho(socket, 1) == 1
    finally:
        await worker_a.shutdown()
        await worker_b.shutdown()
