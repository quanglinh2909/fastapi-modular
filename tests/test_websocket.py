"""Test lớp WebSocket: bắt tay, phòng, gửi thẳng, ack, lỗi và dọn dẹp."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect, WebSocketState

from pymodular.core.config import DatabaseSettings, Settings, WebSocketSettings
from pymodular.core.container import container
from pymodular.core.websocket import Socket, WebSocketServer, build_ws_router, gateway, subscribe
from pymodular.core.websocket.namespace import Namespace
from pymodular.core.websocket.protocol import CloseCode, parse_frame
from pymodular.factory import create_app

CHAT = "/ws/chat"


def connect(client: TestClient, user: str = "an", path: str = CHAT):
    return client.websocket_connect(f"{path}?client_id={user}")


def recv(ws, event: str, *, limit: int = 10) -> dict:
    """Đọc tới khi gặp đúng sự kiện cần, bỏ qua ping/presence xen giữa."""
    for _ in range(limit):
        frame = ws.receive_json()
        if frame["event"] == event:
            return frame
    raise AssertionError(f"Không nhận được sự kiện '{event}'")


def join(ws, room: str, *, frame_id: str = "j1") -> dict:
    ws.send_json({"event": "room.join", "data": {"room": room}, "id": frame_id})
    return recv(ws, "room.join")


# ------------------------------------------------------------------ bắt tay
def test_thieu_danh_tinh_bi_dong_voi_ma_4401(client: TestClient):
    with client.websocket_connect(CHAT) as ws:
        error = ws.receive_json()
        assert error["event"] == "error"
        assert error["data"]["code"] == "unauthorized"
        with pytest.raises(WebSocketDisconnect) as thoat:
            ws.receive_json()
    assert thoat.value.code == CloseCode.UNAUTHORIZED


def test_khung_connected_mang_danh_tinh(client: TestClient):
    with connect(client, "an") as ws:
        frame = recv(ws, "connected")
        assert frame["data"]["user_id"] == "an"
        assert frame["data"]["namespace"] == CHAT
        assert len(frame["data"]["socket_id"]) == 16


def test_nhan_dang_qua_header(client: TestClient):
    with client.websocket_connect(CHAT, headers={"x-client-id": "binh"}) as ws:
        assert recv(ws, "connected")["data"]["user_id"] == "binh"


def test_ping_tra_ve_pong_kem_ack(client: TestClient):
    with connect(client) as ws:
        recv(ws, "connected")
        ws.send_json({"event": "ping", "data": {"n": 1}, "id": "p1"})
        pong = recv(ws, "pong")
        assert pong["ack"] == "p1"
        assert pong["data"] == {"n": 1}


def test_client_tra_loi_pong_thi_server_im_lang(client: TestClient):
    """Client trả lời ping của server bằng `pong` — không được coi là sự kiện lạ."""
    with connect(client) as ws:
        recv(ws, "connected")
        ws.send_json({"event": "pong", "data": {"t": 1}})
        ws.send_json({"event": "whoami", "id": "w1"})
        assert recv(ws, "whoami")["ack"] == "w1"     # không có khung error xen giữa


# -------------------------------------------------------------------- phòng
def test_vao_phong_va_nhan_tin_cua_phong(client: TestClient):
    with connect(client, "an") as an, connect(client, "binh") as binh:
        recv(an, "connected")
        recv(binh, "connected")
        assert join(an, "room:1")["data"]["room"] == "room:1"
        assert join(binh, "room:1")["data"]["size"] == 2

        an.send_json({"event": "message.send", "data": {"room": "room:1", "text": "chào"}, "id": "m1"})

        ack = recv(an, "message.send")
        assert ack["ack"] == "m1"
        assert ack["data"]["delivered"] == 2      # cả người gửi cũng ở trong phòng

        tin = recv(binh, "message.new")
        assert tin["data"] == {**tin["data"], "from": "an", "text": "chào", "room": "room:1"}


def test_khong_o_trong_phong_thi_khong_gui_duoc(client: TestClient):
    with connect(client) as ws:
        recv(ws, "connected")
        ws.send_json({"event": "message.send", "data": {"room": "room:9", "text": "x"}, "id": "m1"})
        error = recv(ws, "error")
        assert error["data"]["code"] == "not_found"
        assert error["ack"] == "m1"


def test_can_join_chan_phong_khong_duoc_phep(client: TestClient):
    with connect(client) as ws:
        recv(ws, "connected")
        ws.send_json({"event": "room.join", "data": {"room": "bi-mat"}, "id": "j1"})
        error = recv(ws, "error")
        assert error["data"]["code"] == "forbidden"


def test_roi_phong_thi_thoi_nhan_tin(client: TestClient):
    with connect(client, "an") as an, connect(client, "binh") as binh:
        recv(an, "connected")
        recv(binh, "connected")
        join(an, "room:2")
        join(binh, "room:2")

        binh.send_json({"event": "room.leave", "data": {"room": "room:2"}, "id": "l1"})
        assert recv(binh, "room.leave")["data"]["rooms"] == ["lobby"]   # vẫn còn lobby

        an.send_json({"event": "message.send", "data": {"room": "room:2", "text": "hi"}, "id": "m1"})
        assert recv(an, "message.send")["data"]["delivered"] == 1


def test_presence_bao_khi_co_nguoi_vao(client: TestClient):
    with connect(client, "an") as an:
        recv(an, "connected")
        with connect(client, "binh") as binh:
            recv(binh, "connected")
            assert recv(an, "presence.join")["data"]["user_id"] == "binh"
        assert recv(an, "presence.leave")["data"]["user_id"] == "binh"


# --------------------------------------------------------------- gửi thẳng
def test_gui_thang_toi_moi_ket_noi_cua_mot_nguoi(client: TestClient):
    """Một người mở hai tab thì cả hai tab đều nhận."""
    with connect(client, "an") as an, connect(client, "binh") as tab1, connect(client, "binh") as tab2:
        for ws in (an, tab1, tab2):
            recv(ws, "connected")

        an.send_json({"event": "message.direct", "data": {"to_user": "binh", "text": "riêng"}, "id": "d1"})
        assert recv(an, "message.direct")["data"] == {"delivered": 2, "online": True}
        assert recv(tab1, "message.direct")["data"]["text"] == "riêng"
        assert recv(tab2, "message.direct")["data"]["from"] == "an"


def test_gui_cho_nguoi_offline_tra_ve_khong(client: TestClient):
    with connect(client, "an") as ws:
        recv(ws, "connected")
        ws.send_json({"event": "message.direct", "data": {"to_user": "vang", "text": "?"}, "id": "d1"})
        assert recv(ws, "message.direct")["data"] == {"delivered": 0, "online": False}


def test_whoami_khong_can_payload(client: TestClient):
    with connect(client, "an") as ws:
        recv(ws, "connected")
        join(ws, "room:3")
        ws.send_json({"event": "whoami", "id": "w1"})
        data = recv(ws, "whoami")["data"]
        assert data["user_id"] == "an"
        assert sorted(data["rooms"]) == ["lobby", "room:3"]


# ---------------------------------------------------------------------- lỗi
def test_su_kien_la_bi_tu_choi_nhung_khong_dut_ket_noi(client: TestClient):
    with connect(client) as ws:
        recv(ws, "connected")
        ws.send_json({"event": "khong-co-that", "id": "x1"})
        error = recv(ws, "error")
        assert error["data"]["code"] == "unknown_event"
        assert "whoami" in error["data"]["details"]["known"]

        ws.send_json({"event": "ping", "id": "p1"})       # kết nối vẫn sống
        assert recv(ws, "pong")["ack"] == "p1"


def test_payload_sai_tra_loi_validate_giong_http(client: TestClient):
    with connect(client) as ws:
        recv(ws, "connected")
        ws.send_json({"event": "message.send", "data": {"room": "room:1"}, "id": "m1"})
        error = recv(ws, "error")
        assert error["data"]["code"] == "validation_error"
        assert error["data"]["details"][0]["field"] == "text"
        assert error["data"]["details"][0]["type"] == "missing"


def test_khung_khong_phai_json(client: TestClient):
    with connect(client) as ws:
        recv(ws, "connected")
        ws.send_text("không phải json")
        assert recv(ws, "error")["data"]["code"] == "ws_protocol_error"


def test_khung_thieu_event(client: TestClient):
    with connect(client) as ws:
        recv(ws, "connected")
        ws.send_json({"data": {}})
        assert "event" in recv(ws, "error")["data"]["message"]


# ------------------------------------------------------- đẩy tin từ phía HTTP
def test_http_day_tin_xuong_websocket(client: TestClient):
    with connect(client, "an") as ws:
        recv(ws, "connected")
        join(ws, "room:7")

        response = client.post(
            "/api/chat/broadcast",
            json={"room": "room:7", "event": "alert.created", "data": {"id": "A1"}},
        )
        assert response.status_code == 200
        assert response.json() == {"delivered": 1}
        assert recv(ws, "alert.created")["data"] == {"id": "A1"}


def test_stats_va_don_dep_sau_khi_ngat(client: TestClient):
    with connect(client, "an") as ws:
        recv(ws, "connected")
        join(ws, "room:8")
        stats = client.get("/api/chat/stats").json()
        assert stats["connections"] == 1
        assert stats["adapter"] == "local"
        assert stats["namespaces"][0]["rooms"] == 2      # lobby + room:8

    # Ra khỏi khối with = client đóng kết nối; sổ phòng phải sạch.
    stats = client.get("/api/chat/stats").json()
    assert stats["connections"] == 0
    assert stats["namespaces"][0]["rooms"] == 0


# ------------------------------------------------------------- hạn mức
@gateway(path="/ws/kin", client_rooms=False)
class GatewayKin:
    @subscribe("echo")
    async def echo(self, socket: Socket, payload: dict) -> dict:
        return payload


def _app_kin(settings: Settings) -> FastAPI:
    app = create_app(settings)
    app.include_router(build_ws_router(GatewayKin))
    return app


def test_client_rooms_tat_thi_tu_choi_room_join(settings: Settings):
    with TestClient(_app_kin(settings)) as client, client.websocket_connect("/ws/kin") as ws:
        recv(ws, "connected")
        ws.send_json({"event": "room.join", "data": {"room": "x"}, "id": "j1"})
        error = recv(ws, "error")
        assert error["data"]["code"] == "forbidden"
        assert "client_rooms=True" in error["data"]["message"]


def test_gui_qua_nhanh_bi_siet():
    settings = Settings(
        APP_ENV="local",
        APP_DB=DatabaseSettings(driver="memory"),
        APP_WS=WebSocketSettings(max_messages_per_second=1, burst_messages=2),
    )
    with TestClient(_app_kin(settings)) as client, client.websocket_connect("/ws/kin") as ws:
        recv(ws, "connected")
        for i in range(5):
            ws.send_json({"event": "echo", "data": {"i": i}, "id": str(i)})
        events = [ws.receive_json()["event"] for _ in range(5)]
        assert "error" in events, events


def test_im_lang_qua_lau_bi_dong_4408():
    """Mất mạng đột ngột thì TCP không báo; hạn im lặng là thứ duy nhất dọn được."""
    settings = Settings(
        APP_ENV="local",
        APP_DB=DatabaseSettings(driver="memory"),
        APP_WS=WebSocketSettings(idle_timeout_seconds=0.2, heartbeat_seconds=0),
    )
    with TestClient(_app_kin(settings)) as client, client.websocket_connect("/ws/kin") as ws:
        recv(ws, "connected")
        with pytest.raises(WebSocketDisconnect) as thoat:
            ws.receive_json()
    assert thoat.value.code == CloseCode.IDLE_TIMEOUT


def test_khung_qua_dai_bi_tu_choi():
    settings = Settings(
        APP_ENV="local",
        APP_DB=DatabaseSettings(driver="memory"),
        APP_WS=WebSocketSettings(max_message_bytes=200),
    )
    with TestClient(_app_kin(settings)) as client, client.websocket_connect("/ws/kin") as ws:
        recv(ws, "connected")
        ws.send_json({"event": "echo", "data": {"text": "x" * 500}})
        assert recv(ws, "error")["data"]["code"] == "ws_protocol_error"


# ------------------------------------------------------------ handler kế thừa
class MixinChao:
    """Bộ sự kiện dùng chung, đóng gói ngoài lõi rồi trộn vào gateway."""

    @subscribe("chao")
    async def chao(self, socket: Socket) -> dict[str, str]:
        return {"tu": "mixin"}

    @subscribe("tam-biet")
    async def tam_biet(self, socket: Socket) -> dict[str, str]:
        return {"tu": "mixin"}


@gateway(path="/ws/tron")
class GatewayTron(MixinChao):
    @subscribe("chao")
    async def chao_de(self, socket: Socket) -> dict[str, str]:
        return {"tu": "lop con"}


def test_handler_cua_mixin_duoc_ke_thua_va_ghi_de_duoc(settings: Settings):
    app = create_app(settings)
    app.include_router(build_ws_router(GatewayTron))
    with TestClient(app) as client, client.websocket_connect("/ws/tron") as ws:
        recv(ws, "connected")

        ws.send_json({"event": "tam-biet", "id": "1"})
        assert recv(ws, "tam-biet")["data"] == {"tu": "mixin"}      # kế thừa

        ws.send_json({"event": "chao", "id": "2"})
        assert recv(ws, "chao")["data"] == {"tu": "lop con"}        # lớp con thắng


def test_trung_su_kien_trong_cung_mot_class_van_bi_bao():
    class TrungTen:
        @subscribe("x")
        async def mot(self, socket: Socket) -> None: ...

        @subscribe("x")
        async def hai(self, socket: Socket) -> None: ...

    with pytest.raises(RuntimeError, match="trùng sự kiện"):
        build_ws_router(gateway(path="/ws/trung")(TrungTen))


# ------------------------------------------------- kiểm tra lúc dựng gateway
def test_khong_dang_ky_de_su_kien_cua_framework():
    with pytest.raises(RuntimeError, match="framework"):
        subscribe("room.join")


def test_handler_sai_chu_ky_bi_bao_ngay():
    class Sai:
        @subscribe("a")
        async def thieu_socket(self) -> None: ...

    with pytest.raises(RuntimeError, match="thiếu tham số socket"):
        build_ws_router(gateway(path="/ws/sai")(Sai))

    class SaiDongBo:
        @subscribe("b")
        def khong_async(self, socket: Socket) -> None: ...

    with pytest.raises(RuntimeError, match="async def"):
        build_ws_router(gateway(path="/ws/sai2")(SaiDongBo))


def test_path_phai_bat_dau_bang_gach_cheo():
    with pytest.raises(RuntimeError, match="bắt đầu bằng"):

        @gateway(path="ws/thieu")
        class Thieu: ...


# --------------------------------------------------------- đơn vị: namespace
class _WsGia:
    """WebSocket giả, đủ để thử Socket mà không cần mạng."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed: tuple[int, str] | None = None
        self.client_state = WebSocketState.CONNECTED

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)
        self.client_state = WebSocketState.DISCONNECTED


def _socket(ns: Namespace, **kwargs) -> Socket:
    socket = Socket(_WsGia(), ns, **kwargs)   # type: ignore[arg-type]
    ns.add(socket)
    return socket


async def test_namespace_don_sach_khi_socket_roi_di():
    ns = Namespace("/ws/t")
    a = _socket(ns, user_id="an")
    b = _socket(ns, user_id="an")
    a.join("r1")
    b.join("r1")

    assert ns.room_size("r1") == 2
    assert len(ns.sockets_of("an")) == 2

    ns.remove(a)
    assert ns.room_size("r1") == 1
    assert len(ns.sockets_of("an")) == 1

    ns.remove(b)
    assert ns.rooms == {}          # phòng rỗng bị xoá hẳn, không rò bộ nhớ
    assert ns.sockets_of("an") == []
    assert len(ns) == 0


async def test_gui_theo_phong_bo_qua_nguoi_bi_loai():
    ns = Namespace("/ws/t")
    a = _socket(ns, user_id="an")
    b = _socket(ns, user_id="binh")
    a.join("r")
    b.join("r")

    assert ns.deliver("e", {"x": 1}, room="r", exclude=[a.id]) == 1
    assert a.pending == 0
    assert b.pending == 1


async def test_hang_doi_day_thi_ngat_ket_noi_cham():
    ns = Namespace("/ws/t")
    socket = _socket(ns, queue_size=2)          # chưa start_writer nên không ai lấy ra

    assert socket.emit("e", 1) is True
    assert socket.emit("e", 2) is True
    assert socket.emit("e", 3) is False         # đầy -> chính sách "close"

    await asyncio.sleep(0)                      # để task đóng chạy
    assert socket.closing is True
    assert socket.ws.closed[0] == CloseCode.TRY_AGAIN_LATER   # type: ignore[union-attr]


async def test_chinh_sach_bo_tin_cu_nhat():
    ns = Namespace("/ws/t")
    socket = _socket(ns, queue_size=2, overflow="drop_oldest")

    socket.emit("e", 1)
    socket.emit("e", 2)
    assert socket.emit("e", 3) is True
    assert socket.closing is False
    assert socket.pending == 2

    socket.start_writer()
    await asyncio.sleep(0.05)
    await socket.stop_writer()
    con_lai = [parse_frame(text).data for text in socket.ws.sent]   # type: ignore[union-attr]
    assert con_lai == [2, 3]                    # tin số 1 đã bị bỏ


async def test_khong_gui_duoc_khi_dang_dong():
    ns = Namespace("/ws/t")
    socket = _socket(ns)
    await socket.close()
    assert socket.emit("e", 1) is False


# --------------------------------------------------------- đơn vị: server
async def test_server_bao_loi_khi_co_nhieu_namespace():
    server = WebSocketServer(Settings(APP_DB=DatabaseSettings(driver="memory")))
    server.namespace("/a")
    server.namespace("/b")
    with pytest.raises(RuntimeError, match="namespace nào"):
        await server.emit("e", 1)
    assert await server.emit("e", 1, namespace="/a") == 0


async def test_tin_tu_worker_khac_duoc_gui_tiep():
    """Adapter gọi _on_remote; tin phải tới đúng phòng của namespace tương ứng."""
    server = WebSocketServer(Settings(APP_DB=DatabaseSettings(driver="memory")))
    ns = server.namespace("/ws/chat")
    socket = _socket(ns, user_id="an")
    socket.join("r")

    server._on_remote(
        {"origin": "worker-khac", "ns": "/ws/chat", "event": "e", "data": {"x": 1}, "room": "r"}
    )
    assert socket.pending == 1

    server._on_remote({"origin": "worker-khac", "ns": "/khong-co", "event": "e", "data": None})
    assert socket.pending == 1      # namespace lạ thì bỏ qua, không nổ


def test_frame_json_giu_tieng_viet_va_datetime():
    from datetime import datetime

    from pymodular.core.compat import UTC
    from pymodular.core.websocket.protocol import Frame

    text = Frame("e", {"ten": "Nguyễn", "at": datetime(2026, 1, 1, tzinfo=UTC)}).to_json()
    assert "Nguyễn" in text                      # không bị escape thành ạ...
    assert json.loads(text)["data"]["at"].startswith("2026-01-01")


@pytest.fixture(autouse=True)
def _reset_server():
    """Namespace nằm trong WebSocketServer singleton — dọn giữa các test."""
    yield
    container.reset()
