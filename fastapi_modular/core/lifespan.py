"""Vòng đời hạ tầng: mở kết nối khi boot, dọn dẹp khi tắt.

Đây là phần của KHUNG — database, WebSocket, và những lớp hạ tầng đang bật.
Việc riêng của ứng dụng thì đừng sửa vào đây; BỌC nó lại trong dự án của bạn:

    # src/core/lifespan.py
    from fastapi_modular import lifespan as framework_lifespan

    @asynccontextmanager
    async def lifespan(app):
        async with framework_lifespan(app):   # database, hàng đợi... sẵn sàng
            await warm_cache()                # việc riêng lúc khởi động
            try:
                yield
            finally:
                await flush_ledger()          # việc riêng lúc tắt, TRƯỚC khi khung đóng

    # src/main.py
    app = new_fastapi(settings, lifespan=lifespan)

`fam init` sinh sẵn file đó, chỉ việc điền vào.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from fastapi_modular.core.config import Settings, check_deprecated_env, get_settings
from fastapi_modular.core.container import _ENTITIES, container
from fastapi_modular.core.logging import get_logger
from fastapi_modular.core.websocket import WebSocketServer

log = get_logger(__name__)


def _resize_blocking_pool(settings: Settings) -> Any:
    """Đổi pool thread mà `ctx.blocking(...)` dùng, nếu có khai kích thước.

    Mặc định của Python là `min(32, số nhân + 4)`. Nhiều worker cùng gọi hàm
    chặn liên tục hơn số đó thì chúng xếp hàng chờ nhau.
    """
    size = settings.workers.thread_pool_size
    if size <= 0:
        return None

    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    pool = ThreadPoolExecutor(max_workers=size, thread_name_prefix="fam-blocking")
    asyncio.get_running_loop().set_default_executor(pool)
    log.info("workers.thread_pool", size=size)
    return pool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Lấy từ container: create_app() đã nạp vào đó, nên Settings truyền tay khi
    # test được tôn trọng thay vì đọc lại .env.
    try:
        settings = container.resolve(Settings)
    except RuntimeError:
        settings = get_settings()
    app.state.container = container

    for problem in settings.check_production_safety():
        log.warning("config.unsafe_for_production", problem=problem)

    # Biến môi trường tên cũ bị pydantic bỏ qua trong im lặng: app chạy với giá
    # trị mặc định mà không ai biết. Gom vào MỘT dòng — mười ba dòng cảnh báo
    # rời rạc thì người ta cuộn qua, một dòng có số đếm thì đọc.
    if deprecated := check_deprecated_env():
        log.warning(
            "config.deprecated_env",
            count=len(deprecated),
            problems=deprecated,
            hint="những biến này đang BỊ BỎ QUA, app dùng giá trị mặc định thay thế",
        )

    # Import muộn: Database kéo theo factory driver, mà factory chỉ được chạm
    # tới sau khi mọi module đã nạp xong (entity phải đăng ký trước create_schema).
    from fastapi_modular.core.jobs import JobQueue, JobRunner
    from fastapi_modular.core.scheduler import SchedulerRunner
    from fastapi_modular.core.workers import WorkerPool
    from fastapi_modular.infrastructure.database import Database
    from fastapi_modular.infrastructure.kafka import (
        KafkaBroker,
        KafkaResponderRunner,
        KafkaRunner,
    )
    from fastapi_modular.infrastructure.mqtt import (
        MqttClient,
        MqttResponderRunner,
        MqttRunner,
    )
    from fastapi_modular.infrastructure.rabbitmq import (
        RabbitBroker,
        RabbitmqResponderRunner,
        RabbitmqRunner,
    )
    from fastapi_modular.infrastructure.redis import (
        RedisClient,
        RedisResponderRunner,
        RedisRunner,
    )

    database = container.resolve(Database)

    log.info("app.starting", env=settings.env, version=settings.version)
    await database.startup(*_ENTITIES.values())
    app.state.database = database

    # Lớp WebSocket: mở kênh phát tin xuyên worker (nếu dùng adapter redis).
    websockets = container.resolve(WebSocketServer)
    await websockets.startup()
    app.state.websockets = websockets

    # RabbitMQ (tuỳ chọn). Tắt thì hai lời gọi dưới đây đều không làm gì.
    # Bật mà broker chưa lên thì app VẪN CHẠY và nối lại ngầm.
    broker = container.resolve(RabbitBroker)
    await broker.startup()
    consumers = container.resolve(RabbitmqRunner)
    await consumers.startup()
    responders = container.resolve(RabbitmqResponderRunner)
    await responders.startup()
    app.state.broker = broker

    # Ba lớp dưới đây cũng TUỲ CHỌN và độc lập nhau. Tắt (mặc định) thì mỗi lời
    # gọi startup() là một lệnh `return` — không import thư viện, không mở kết
    # nối. Bật mà server chưa lên thì app VẪN CHẠY và nối lại ngầm.
    redis = container.resolve(RedisClient)
    await redis.startup()
    channels = container.resolve(RedisRunner)
    await channels.startup()
    redis_responders = container.resolve(RedisResponderRunner)
    await redis_responders.startup()
    app.state.redis = redis

    # MqttRunner phải chạy TRƯỚC client: nó là chỗ khai danh sách topic, mà
    # client đăng ký topic ngay trong lần bắt tay đầu tiên.
    mqtt_runner = container.resolve(MqttRunner)
    await mqtt_runner.startup()
    mqtt_responders = container.resolve(MqttResponderRunner)
    await mqtt_responders.startup()
    mqtt = container.resolve(MqttClient)
    await mqtt.startup()
    app.state.mqtt = mqtt

    kafka = container.resolve(KafkaBroker)
    await kafka.startup()
    kafka_consumers = container.resolve(KafkaRunner)
    await kafka_consumers.startup()
    kafka_responders = container.resolve(KafkaResponderRunner)
    await kafka_responders.startup()
    app.state.kafka = kafka

    # Việc chạy nền bật CUỐI CÙNG: chúng dùng database và hàng đợi, nên phải
    # đợi những thứ đó sẵn sàng. Không cần hạ tầng gì để chạy, nhưng không có
    # @interval/@cron/@timeout hay @job nào thì hai lời gọi này không làm gì.
    jobs = container.resolve(JobRunner)
    await jobs.startup()
    scheduler = container.resolve(SchedulerRunner)
    await scheduler.startup()
    app.state.jobs = container.resolve(JobQueue)
    app.state.scheduler = scheduler
    workers = container.resolve(WorkerPool)
    app.state.workers = workers
    blocking_pool = _resize_blocking_pool(settings)

    log.info(
        "app.started",
        driver=database.driver,
        ws_adapter=websockets.adapter_name,
        mq=broker.stats(),
        redis=redis.stats(),
        mqtt=mqtt.stats(),
        kafka=kafka.stats(),
        entities=sorted(_ENTITIES),
        providers=sorted(container.registered),
    )

    try:
        yield
    finally:
        log.info("app.stopping")
        # Thứ tự tắt là ngược lại thứ tự bật, và có lý do cho từng bước:
        #   consumer trước  — chúng còn đang truy vấn database
        #   WebSocket       — client nhận mã 1001 để nối lại ngay
        #   broker, rồi database — hai thứ mọi tầng trên đều dựa vào
        # Worker tắt ĐẦU TIÊN: chúng là vòng lặp sống mãi, còn chạy là còn
        # sinh việc và còn dùng database.
        await workers.stop_all()
        # Lịch tắt TRƯỚC hàng đợi việc: nó là một nguồn sinh việc, dừng nó
        # trước thì hàng đợi mới cạn được thay vì bị bơm thêm trong lúc đang dọn.
        await scheduler.shutdown()
        await jobs.shutdown()
        await kafka_consumers.shutdown()
        await kafka.shutdown()
        await mqtt.shutdown()
        await channels.shutdown()
        await consumers.shutdown()
        await websockets.shutdown()
        await broker.shutdown()
        await redis.shutdown()
        await database.shutdown()
        if blocking_pool is not None:
            blocking_pool.shutdown(wait=False, cancel_futures=True)
        container.reset()
        app.state.container = None
        app.state.database = None
        app.state.websockets = None
        app.state.broker = None
        app.state.redis = None
        app.state.mqtt = None
        app.state.kafka = None
        log.info("app.stopped")
