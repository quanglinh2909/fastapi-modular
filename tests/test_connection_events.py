"""Test sự kiện nối/đứt (`@*_on_connect` / `@*_on_disconnect`) KHÔNG cần hạ tầng.

Luật "khi nào thì gọi handler" nằm hết ở `core/connection.py`, nên kiểm ở đây
một lần là đủ cho cả bốn hạ tầng. Phần hạ tầng thật nằm trong
`test_mqtt.py` / `test_rabbitmq.py` / `test_redis.py` / `test_kafka.py`.
"""

from __future__ import annotations

import asyncio
import importlib
import itertools

import pytest

from fastapi_modular import broker_status
from fastapi_modular.core.config import KafkaSettings, RedisSettings
from fastapi_modular.core.connection import ConnectionEvents, connection_decorator
from fastapi_modular.core.container import injectable

_counter = itertools.count()


def new_kind() -> str:
    """Mỗi test một tên hạ tầng riêng: class khai trong test này không lọt sang test khác."""
    return f"offline{next(_counter)}"


async def settle(events: ConnectionEvents) -> None:
    if events._queue is not None:
        await asyncio.wait_for(events._queue.join(), 2)


def register(cls: type, kind: str) -> type:
    """Đăng ký với tên class riêng: container tra provider THEO TÊN, trùng là lỗi."""
    cls.__name__ = cls.__qualname__ = f"{cls.__name__}_{kind}"
    return injectable(cls)


def make_listener(kind: str, seen: list):
    on_connect = connection_decorator(kind, "connect")
    on_disconnect = connection_decorator(kind, "disconnect")

    class Listener:
        @on_connect
        async def online(self, info: dict) -> None:
            seen.append(("connect", info))

        @on_disconnect
        async def offline(self, info: dict) -> None:
            seen.append(("disconnect", info))

    return register(Listener, kind)


@pytest.mark.parametrize(
    ("module", "kind"),
    [("mqtt", "mqtt"), ("rabbitmq", "rabbitmq"), ("redis", "redis"), ("kafka", "kafka")],
)
def test_ca_bon_ha_tang_deu_co_cap_decorator(module: str, kind: str):
    package = importlib.import_module(f"fastapi_modular.infrastructure.{module}")
    for event in ("connect", "disconnect"):
        name = f"{kind}_on_{event}"
        assert name in package.__all__
        assert getattr(package, name).__name__ == name


def test_bat_buoc_async_def():
    on_connect = connection_decorator(new_kind(), "connect")
    with pytest.raises(RuntimeError, match="async def"):

        @on_connect
        def sync(self) -> None: ...


async def test_viet_co_hay_khong_co_ngoac_deu_duoc():
    kind = new_kind()
    on_connect = connection_decorator(kind, "connect")
    seen: list[str] = []

    class Listener:
        @on_connect
        async def bare(self) -> None:
            seen.append("bare")

        @on_connect()
        async def called(self) -> None:
            seen.append("called")

    register(Listener, kind)
    events = ConnectionEvents(kind)
    events.start()
    events.mark_connected()
    await settle(events)
    assert sorted(seen) == ["bare", "called"]


def test_chu_ky_thua_tham_so_bi_tu_choi_luc_quet():
    kind = new_kind()
    on_disconnect = connection_decorator(kind, "disconnect")

    class Wrong:
        @on_disconnect
        async def offline(self, info: dict, extra: int) -> None: ...

    register(Wrong, kind)

    with pytest.raises(RuntimeError, match=r"\(self\) hoặc \(self, info\)"):
        ConnectionEvents(kind).start()


async def test_chi_bao_khi_trang_thai_doi():
    kind = new_kind()
    seen: list = []
    make_listener(kind, seen)
    events = ConnectionEvents(kind)
    events.start()

    events.mark_disconnected("chưa từng nối")   # chưa nối lần nào -> không phải "mất"
    events.mark_connected(url="x")
    events.mark_connected(url="x")              # trùng -> bỏ
    events.mark_disconnected(OSError("rớt mạng"), url="x")
    events.mark_disconnected("thử lại hỏng", url="x")   # trùng -> bỏ
    events.mark_connected(url="x")
    await settle(events)

    assert [name for name, _ in seen] == ["connect", "disconnect", "connect"]
    first, lost, back = (info for _, info in seen)
    assert first == {"url": "x", "reconnect": False, "downtime_seconds": None}
    assert lost == {"url": "x", "error": "OSError: rớt mạng"}
    assert back["reconnect"] is True
    assert isinstance(back["downtime_seconds"], float)


async def test_loi_khong_thong_diep_khong_de_dau_hai_cham_treo():
    kind = new_kind()
    seen: list = []
    make_listener(kind, seen)
    events = ConnectionEvents(kind)
    events.start()
    events.mark_connected()
    events.mark_disconnected(asyncio.TimeoutError())
    await settle(events)
    assert seen[-1][1]["error"] == "TimeoutError"


async def test_tat_app_khong_bao_mat_ket_noi():
    kind = new_kind()
    seen: list = []
    make_listener(kind, seen)
    events = ConnectionEvents(kind)
    events.start()
    events.mark_connected()
    await events.close()
    events.mark_disconnected("sau khi tắt")
    await settle(events)
    assert [name for name, _ in seen] == ["connect"]


async def test_handler_hong_khong_chan_handler_khac_va_su_kien_sau():
    kind = new_kind()
    on_connect = connection_decorator(kind, "connect")
    seen: list[str] = []

    class Listener:
        @on_connect
        async def a_broken(self) -> None:
            raise ValueError("hỏng")

        @on_connect
        async def b_healthy(self) -> None:
            seen.append("b")

    register(Listener, kind)
    events = ConnectionEvents(kind)
    events.start()
    events.mark_connected()
    events.mark_disconnected()
    events.mark_connected()
    await settle(events)
    assert seen == ["b", "b"]


async def test_handler_cham_khong_lam_dao_thu_tu():
    kind = new_kind()
    on_connect = connection_decorator(kind, "connect")
    on_disconnect = connection_decorator(kind, "disconnect")
    seen: list[str] = []

    class Listener:
        @on_connect
        async def online(self) -> None:
            await asyncio.sleep(0.05)
            seen.append("connect")

        @on_disconnect
        async def offline(self) -> None:
            seen.append("disconnect")

    register(Listener, kind)
    events = ConnectionEvents(kind)
    events.start()
    events.mark_connected()
    events.mark_disconnected()
    await settle(events)
    assert seen == ["connect", "disconnect"]


async def test_khong_ai_nghe_thi_khong_sinh_task():
    events = ConnectionEvents(new_kind())
    events.start()
    assert events.has_hooks is False
    events.mark_connected()
    events.mark_disconnected()
    assert events._task is None


def test_nhip_kiem_tra_mac_dinh():
    assert RedisSettings().health_check_seconds == 5.0
    assert KafkaSettings().health_check_seconds == 5.0


async def test_redis_loi_cua_lenh_khong_phai_mat_ket_noi():
    pytest.importorskip("redis")
    from redis.exceptions import ConnectionError as RedisConnectionError
    from redis.exceptions import ResponseError

    from fastapi_modular.core.config import Settings
    from fastapi_modular.infrastructure.redis import RedisClient

    client = RedisClient(Settings(APP_REDIS=RedisSettings(enabled=False)))
    seen: list = []
    kind = new_kind()
    make_listener(kind, seen)
    client._events = ConnectionEvents(kind)
    client._events.start()

    client._client = object()                   # giả như đã mở kết nối
    client._mark(True)
    client._mark(False, ResponseError("WRONGTYPE Operation against a key"))
    assert client.connected is True, "lệnh gõ sai không có nghĩa là Redis đứt"
    client._mark(False, RedisConnectionError("Connection refused"))
    assert client.connected is False
    await settle(client._events)
    assert [name for name, _ in seen] == ["connect", "disconnect"]


# ------------------------------------------------------------ broker_status()
async def test_broker_status_doc_dung_trang_thai_hien_tai():
    kind = new_kind()
    connection_decorator(kind, "connect")          # hạ tầng nào cũng sinh decorator lúc import
    events = ConnectionEvents(kind)
    assert broker_status(kind) == {
        "enabled": False, "connected": False, "since": None, "last_error": None, "disconnects": 0,
    }

    events.start(url="mqtt://x")
    status = broker_status(kind)
    assert (status["enabled"], status["connected"], status["url"]) == (True, False, "mqtt://x")
    assert kind in broker_status()

    events.mark_connected(url="mqtt://x", client_id="c1")
    status = broker_status(kind)
    assert status["connected"] is True
    assert status["client_id"] == "c1"
    assert status["since"] is not None

    events.mark_disconnected(OSError("rớt mạng"), url="mqtt://x")
    status = broker_status(kind)
    assert (status["connected"], status["last_error"], status["disconnects"]) == (
        False, "OSError: rớt mạng", 1,
    )

    status["connected"] = True
    assert broker_status(kind)["connected"] is False, "dict trả về phải là bản sao"

    await events.close()
    assert kind not in broker_status()
    assert broker_status(kind)["enabled"] is False


def test_broker_status_go_nham_ten_thi_bao_ngay():
    with pytest.raises(ValueError, match="không có hạ tầng 'mqqt'"):
        broker_status("mqqt")


async def test_client_chua_bat_khong_de_len_client_dang_chay():
    kind = new_kind()
    connection_decorator(kind, "connect")
    running = ConnectionEvents(kind)
    running.start()
    running.mark_connected()

    idle = ConnectionEvents(kind)                  # dựng ra mà không bật, rồi tắt
    await idle.close()
    assert broker_status(kind)["connected"] is True
    await running.close()


def test_ca_bon_ten_ha_tang_deu_hoi_duoc():
    for module in ("mqtt", "rabbitmq", "redis", "kafka"):
        importlib.import_module(f"fastapi_modular.infrastructure.{module}")
        assert broker_status(module)["enabled"] is False    # conftest tắt hết hạ tầng
