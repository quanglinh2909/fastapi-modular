"""Vòng đời ứng dụng — FILE CỦA BẠN.

Khung lo phần hạ tầng: mở/đóng database, WebSocket, và những lớp hàng đợi đang
bật (RabbitMQ, Redis, MQTT, Kafka). Việc RIÊNG của ứng dụng — nạp cache, hâm
nóng model, đăng ký với service discovery, đóng sổ khi tắt — viết ở đây.

Thứ tự quan trọng và cố ý:

    khung mở database, hàng đợi
        -> việc khởi động của bạn        (đã có database để dùng)
            -> app phục vụ request
        -> việc lúc tắt của bạn          (database VẪN CÒN để ghi nốt)
    khung đóng hàng đợi, database
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pymodular import get_logger
from pymodular import lifespan as framework_lifespan

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with framework_lifespan(app):
        # --- KHỞI ĐỘNG: chạy sau khi database và hàng đợi đã sẵn sàng ---
        log.info("app.ready")

        try:
            yield
        finally:
            # --- TẮT: chạy trước khi khung đóng database ---
            log.info("app.closing")
