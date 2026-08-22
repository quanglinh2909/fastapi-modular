"""Thư viện WebSocket của khung: gateway, phòng, gửi thẳng cho một client.

Đọc `docs/websocket.md` để có hướng dẫn đầy đủ (kèm ví dụ Postman và Next.js).

    from pymodular.core.websocket import Socket, WebSocketServer, gateway, subscribe
"""

from __future__ import annotations

from pymodular.core.websocket.adapter import BroadcastAdapter, LocalAdapter, RedisAdapter
from pymodular.core.websocket.gateway import build_ws_router, gateway, gateways_in, subscribe
from pymodular.core.websocket.namespace import Namespace
from pymodular.core.websocket.protocol import CloseCode, Frame, ProtocolError
from pymodular.core.websocket.server import WebSocketServer
from pymodular.core.websocket.socket import Socket

__all__ = [
    "BroadcastAdapter",
    "CloseCode",
    "Frame",
    "LocalAdapter",
    "Namespace",
    "ProtocolError",
    "RedisAdapter",
    "Socket",
    "WebSocketServer",
    "build_ws_router",
    "gateway",
    "gateways_in",
    "subscribe",
]
