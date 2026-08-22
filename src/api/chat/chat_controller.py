"""HTTP đẩy tin xuống WebSocket.

Đây là kiểu dùng phổ biến nhất ngoài đời: một sự kiện xảy ra ở tầng nghiệp vụ
(đơn hàng đổi trạng thái, cảm biến vượt ngưỡng) và cần hiện ngay trên màn hình
người dùng. Chỉ cần nhận `WebSocketServer` qua __init__ rồi gọi — không cần
biết client nào đang nối vào đâu.
"""

from __future__ import annotations

from pymodular.core.controller import controller, get, post
from pymodular.core.websocket import WebSocketServer
from pymodular.infrastructure.rabbitmq import RabbitBroker, validate_routing_key
from src.api.chat.dto.chat_dto import BroadcastIn, BroadcastOut, PublishIn, PublishOut


@controller(prefix="/chat", tags=["chat"])
class ChatController:
    def __init__(self, server: WebSocketServer, broker: RabbitBroker) -> None:
        self._server = server
        self._broker = broker

    @post("/broadcast", response_model=BroadcastOut, summary="Đẩy tin vào một phòng")
    async def broadcast(self, payload: BroadcastIn) -> BroadcastOut:
        delivered = await self._server.to_room(
            payload.room, payload.event, payload.data, namespace="/ws/chat"
        )
        return BroadcastOut(delivered=delivered)

    @post("/publish", response_model=PublishOut, summary="Đăng tin lên RabbitMQ")
    async def publish(self, payload: PublishIn) -> PublishOut:
        """Đăng một sự kiện lên exchange topic.

        Khác `/broadcast` ở chỗ: broadcast đẩy thẳng tới các kết nối của worker
        này, còn publish đưa tin qua RabbitMQ — mọi worker đều nhận được, và cả
        những dịch vụ khác đang nghe cùng exchange.
        """
        routing_key = validate_routing_key(payload.routing_key)
        published = await self._broker.publish(payload.exchange, routing_key, payload.data)
        return PublishOut(
            published=published, exchange=payload.exchange, routing_key=routing_key
        )

    @get("/stats", summary="Số kết nối, phòng, adapter và trạng thái RabbitMQ")
    async def stats(self) -> dict:
        return {**self._server.stats(), "mq": self._broker.stats()}
