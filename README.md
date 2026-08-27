> **English** · [Tiếng Việt](https://github.com/quanglinh2909/fastapi-modular/blob/main/README.vi.md)

# fastapi-modular

**NestJS-style modular architecture for FastAPI.** A dependency-injection
container, class-based controllers, auto-discovered modules, a shared repository
over four databases, a WebSocket gateway with rooms, and optional RabbitMQ /
Redis / MQTT / Kafka layers that stay dormant until you enable them.

If you have written NestJS and wished FastAPI came with the same structure —
modules that register themselves, `@Injectable` services, `@Controller` classes,
`@WebSocketGateway`, `@EventPattern` — this is that, in Python.

```bash
pip install fastapi-modular
fam init && fam dev
```

The full documentation lives in `docs/` and is written in **Vietnamese**; the
public API, and this README, are in English. Start with
[docs/architecture.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/architecture.md).

## Coming from NestJS?

| NestJS | fastapi-modular |
|---|---|
| `@Module()` + module scanning | a directory under `src/api/`, auto-scanned |
| `@Controller('users')` | `@controller(prefix="/users", tags=["users"])` |
| `@Get()` `@Post()` `@Patch()` `@Delete()` | `@get()` `@post()` `@patch()` `@delete()` |
| sync handler runs on the main thread (Nest) | `def` handler runs in a thread pool, `async def` on the event loop — FastAPI's own rule |
| `@Injectable()` | `@injectable` |
| `@Injectable({scope: Scope.REQUEST})` | `@injectable(scope=Scope.REQUEST)` |
| `forwardRef(() => X)` | `Lazy[X]` |
| `@InjectRepository(X) repo: Repository<X>` | `repo: Repository[X]` |
| `@Transaction()` / `queryRunner.startTransaction()` | `async with db.transaction():` — nested blocks become SAVEPOINTs |
| `queryRunner.rollbackTransaction()` | automatic on exception; `await tx.rollback()` to bail out without raising |
| `repo.createQueryBuilder()` (TypeORM) | `repo.query().join(X).where(Event.score >= …)` — real SQL, `.sql()` to see it |
| `Repository.find({where: {score: MoreThan(…)}})` (TypeORM) | `class Event(Entity)` then `.where(Event.score >= …)`, or `.where(score__gte=…)` |
| `.groupBy().having()` (TypeORM) | `.group_by(Event.camera_id).select(n=count()).having(count() > 5)` |
| `.leftJoin()` / `.orWhere()` (TypeORM) | `.left_join(X)` / `.or_where(…)` — one method per join kind |
| `.orderBy('x', 'DESC')` (TypeORM) | `.order_by_desc("x")` — the direction is in the method name |
| `Like()` / `In()` / `IsNull()` (TypeORM) | `.like(X.name, "a%")` · `.in_(X.zone, [...])` · `.is_null(X.ip)` — right on the builder |
| `select([...])` / `AS` (TypeORM) | `.select(fields=…, exclude=…, rename={"new": "col"})` — same names on `include` |
| `addSelect()` (TypeORM) | `.select(add={"cam_name": Camera.name})` — keep every column, add one |
| `find({relations: {events: true}})` (TypeORM) | `.include(Event)` — nested rows, one extra query, not N+1 |
| `relations: {camera: {logs: {items: true}}}` (TypeORM) | `.nest_under(Camera, CameraLog, ItemLog)` — one query per level |
| *(no TypeORM equivalent)* | `.nest_under(Camera)` — filter on events, get cameras back with them nested |
| `@ManyToOne(…, {onDelete: 'CASCADE'})` (TypeORM) | `field(metadata=reference(Camera, on_delete="CASCADE"))` — a real FK in the database |
| `@UseGuards()` | `guards=[...]` on the controller or a single route |
| `@WebSocketGateway()` | `@gateway(path="/ws/…")` |
| `@SubscribeMessage('x')` | `@subscribe("x")` |
| `@EventPattern('x')` (RabbitMQ) | `@rabbitmq_subscriber("events", "x", queue="…")` |
| `@MessagePattern('x')` | `@rabbitmq_responder("x", queue="…")` — the return value is sent back |
| `@Interval()` / `@Cron()` / `@Timeout()` | `@interval(seconds=5)` / `@cron("0 3 * * *")` / `@timeout(seconds=10)` |
| `@OnEvent('x')` + `EventEmitter2` | `@on_event("x")` + `EventBus.emit()` — in-process fanout |
| `client.emit(p, d)` / `client.send(p, d)` | `broker.emit(p, d, queue=…)` / `await broker.send(p, d, queue=…)` |
| `CacheModule` / `CACHE_MANAGER` | `RedisClient.cached(key, factory, ttl=…)` |
| socket.io Redis adapter | `APP_WS__ADAPTER=redis` |

Full side-by-side table in
[docs/architecture.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/architecture.md).

## Getting started

Requires **Python 3.10+**.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install fastapi-modular

fam init          # scaffolds into the CURRENT directory, no extra nesting
fam dev
```

Open http://localhost:8000/docs — a working `health` module is already there.

`fam init` takes the project name from the current directory name; use
`fam init --name other-name` to override. It never overwrites an existing file,
so it is safe to run inside a directory that already has code. If you want it to
create the directory for you, use `fam new <name>`.

The core pulls in **no** database driver and **no** queue client. Add only what
you need:

```bash
fam install sqlite      # or postgres, mongodb
fam install rabbitmq    # or redis, mqtt, kafka
fam install all         # everything above
```

`fam install` does three things: installs the libraries, writes the matching
variables into `.env`, and **records the component in `requirements.txt`** so a
teammate who clones the repo only needs `pip install -r requirements.txt` — the
same job `package.json` does for `npm i`.

```
# requirements.txt, after `fam install sqlite` and `fam install redis`
fastapi-modular[redis,sqlite]>=0.2.1
```

It records the extras, not the individual packages: the version ranges of
`sqlalchemy`, `motor` and friends belong to fastapi-modular and change per
release, so a flattened snapshot would go stale silently. If the project uses
`pyproject.toml` and already lists fastapi-modular there, that line is updated
instead and no `requirements.txt` is created. `fam install dev` goes to
`requirements-dev.txt` — production should not have to install pytest.

Plain pip works too: `pip install "fastapi-modular[sqlite,rabbitmq]"`.

## Commands

One program, two names: `fastapi-modular` (full) and `fam` (short). Examples use
`fam`.

Command names can be abbreviated as long as the prefix is unambiguous — `fam mo
alerts` is exactly `fam module alerts`. When ambiguous, it asks instead of
guessing:

```
$ fam m
fam: lệnh 'm' chưa rõ — khớp với migrate, module. Gõ thêm vài chữ cho rõ.
```

| Command | Short | What it does |
|---|---|---|
| `fam init [--name <n>]` | `fam ini` | scaffold **into the current directory**; never overwrites; name defaults to the directory name |
| `fam new <name>` | `fam n` | scaffold into a new directory |
| `fam dev` | `fam d` | run with autoreload |
| `fam run --workers 4` | `fam r` | run in production mode |
| `fam module <name>` | `fam mo` | generate a module: controller + service + dto + entity |
| `fam module <name> --gateway` | | plus a WebSocket gateway (`--consumer` for RabbitMQ) |
| `fam module <name> --gateway-only` | | add a gateway to an **existing** module (`--consumer-only` for RabbitMQ) |
| `fam module <name> --entity <N>` | | set the entity class name; guessed from the module name otherwise |
| `fam provider <family> <name>` | `fam pr` | generate a pluggable provider: capability interfaces + implementation stub |
| `fam env <component>` | `fam e` | only write config variables into `.env` (installs nothing) |
| `fam clean` | `fam c` | remove caches and build output (leaves `data/` alone) |
| `fam build` · `fam publish [--test]` | `fam b` · `fam pu` | build wheel/sdist · upload to PyPI |
| `fam info` | `fam inf` | what it connects to, what is installed, production config warnings |
| `fam migrate [up\|down\|history\|sql\|create]` | `fam mi` | Alembic |
| `fam test` · `fam lint [--fix]` | `fam t` · `fam l` | pytest · ruff. Bare `fam lint` checks `src`; pass paths to check elsewhere |
| **Databases** | | *installs libraries **then** writes `.env`* |
| `fam install sqlite` | `fam ins s` | a `.db` file, no server needed |
| `fam install postgres` | `fam ins p` | PostgreSQL |
| `fam install mongodb` | `fam ins mo` | MongoDB |
| **Queues** | | *installs libraries **then** writes `.env`* |
| `fam install rabbitmq` | `fam ins ra` | 5 exchange types, durable queues, TTL, retry + DLQ |
| `fam install redis` | `fam ins re` | cache, atomic counters, pub/sub |
| `fam install mqtt` | `fam ins mq` | IoT devices |
| `fam install kafka` | `fam ins k` | replayable event log |
| `fam install ws-redis` | `fam ins w` | WebSocket broadcast across workers |
| `fam install dev` | `fam ins d` | pytest · pytest-asyncio · httpx · ruff |
| `fam install all` | `fam ins a` | everything above **except** `dev` |

Host and port come from `APP_HOST` / `APP_PORT` in `.env`, so `fam dev` needs no
arguments. `fam --help` lists everything.

## Adding a module

```bash
fam module alerts              # controller + service + dto + entities
fam module alerts --gateway    # plus a WebSocket gateway
fam module alerts --consumer   # plus a RabbitMQ consumer
```

Routes appear immediately, the table is created, validation runs — only the
method bodies are missing (calling them returns 501 with the function name). Your
job: add fields to the entity and the DTO, then write the service bodies.

Nothing else to edit — no registration step. Details:
[docs/architecture.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/architecture.md#thêm-module-mới).

## Choosing a database

One shared `Repository[T]` over **memory, SQLite, PostgreSQL and MongoDB** —
switching the backend does not change your service code.

```bash
fam install sqlite      # or postgres, mongodb
fam env sqlite          # write .env only, install nothing
fam info                # what it is connected to right now
```

Details: [docs/database.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/database.md) (SQL) · [docs/mongodb.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/mongodb.md) (MongoDB).

## Your own configuration

Subclass `Settings` and the new variables are readable from `.env` — no framework
file to edit:

```python
# src/core/config.py — generated by fam init
class AppSettings(Settings):
    team_name: str = Field(default="", alias="APP_TEAM_NAME")
    jwt: JwtSettings = Field(default_factory=JwtSettings, alias="APP_JWT")   # -> APP_JWT__SECRET
```

Services receive `AppSettings` through DI with full type hints. Details:
[docs/config.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/config.md).

## The entry point is your file

`fam init` generates `src/main.py` with every assembly step spelled out — add
middleware, change CORS, mount a third-party router right there:

```python
settings = bind_settings(AppSettings())
configure_logging(settings.log)

app = new_fastapi(settings, lifespan=lifespan)
add_middleware(app, settings)                       # CORS + request-id + access log
register_error_handlers(app, debug=settings.debug)
register_routes(app, prefix=settings.api_prefix)    # scans src/api/
```

If you need none of that, the whole block collapses to
`app = create_app(AppSettings())` — `create_app` runs exactly that sequence,
nothing more.

Lifespan works the same way: `src/core/lifespan.py` is yours, and it simply
**wraps** the framework's infrastructure:

```python
@asynccontextmanager
async def lifespan(app):
    async with framework_lifespan(app):   # framework opens database, queues
        await warm_cache()                # your work — the database is ready
        try:
            yield
        finally:
            await flush_ledger()          # your work — the database is STILL up
```

## Realtime (WebSocket)

One connection per client; join **rooms** for group messages, or receive messages
addressed to you alone:

```python
@gateway(path="/ws/alerts", guards=[WsJwt], client_rooms=True)
class AlertGateway:
    @subscribe("alert.ack")
    async def ack(self, socket: Socket, payload: AlertAck) -> dict:
        return {"ok": True}
```

```bash
fam dev
# ws://localhost:8000/ws/chat?client_id=an

fam module alerts --gateway-only   # add a gateway to an existing module
fam install ws-redis               # required when running multiple workers
```

To push from REST or a background task, take `WebSocketServer` in `__init__` and
call `to_room` / `to_user` / `to_socket`.

Full guide — including **Postman** and a **Next.js** client, plus the four things
every client must do:
[docs/websocket.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/websocket.md).

## Queues (RabbitMQ — optional)

```python
await self._mq.publish("events", "alert.created.hanoi", {"id": "A1"})

# Default: exactly ONE queue on the broker; failures are dropped (and logged).
@rabbitmq_subscriber("events", "alert.created", queue="alert-mailer")
async def send_mail(self, payload: AlertCreated) -> None: ...

# Opt in when the message is worth money -> adds alert-mailer.retry and .dlq
@rabbitmq_subscriber("events", "alert.created", queue="alert-mailer",
                     max_retries=3, dead_letter=True)
async def send_mail(self, payload: AlertCreated) -> None: ...

# All 5 exchange types: topic (default), direct, fanout, headers, default
@rabbitmq_subscriber("cache-events", queue=f"drop-cache-{HOSTNAME}", exchange_type="fanout")
# hostname in the queue name -> every worker gets a copy, instead of taking turns
async def drop_cache(self, payload: dict) -> None: ...

# Time to live: per message (ttl), per queue (message_ttl), for the queue itself
await self._mq.publish("events", "car.position", {"lat": 21.0}, ttl=5)
```

Not installed and not enabled means it behaves as if it never existed. If the
broker goes down the app keeps serving and reconnects on its own. Details:
[docs/rabbitmq.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/rabbitmq.md).

## Background work

Four different things, none of which needs any infrastructure:

```python
# on a SCHEDULE — @nestjs/schedule
@interval(seconds=5)
async def update_cameras(self) -> None: ...

@cron("0 3 * * *", timezone="Asia/Ho_Chi_Minh")     # defaults to UTC!
async def clean_logs(self) -> None: ...

# on DEMAND — an in-process asyncio.Queue, processed in order
@job("detect", thread=True)            # thread: runs in a thread, for YOLO
def detect(self, payload: dict, ctx: WorkerContext) -> None:
    ctx.run(self._db.save(...))        # write to the DB from inside the thread

await self._jobs.submit("detect", {"path": p})      # returns immediately

# a LONG-RUNNING LOOP — N instances, one per key
@worker("camera")
async def watch(self, data: dict, ctx: WorkerContext) -> None:
    cap = await ctx.blocking(cv2.VideoCapture, data["ip"])   # setup, OUTSIDE the loop
    while ctx.running:
        frame = await ctx.blocking(cap.read)                 # blocking call -> a thread
        await self._db.save(...)                             # plain await

for camera in cameras:
    await service.watch(camera.id, {"ip": camera.ip})   # key + data at call time

await self.watch.stop(camera.id)       # stops ONE instance, waits for its cleanup

# FANOUT inside the process — one event, N listeners, in PARALLEL
@on_event("order.paid")                       # also "order.*" / "camera.#"
async def send_receipt(self, data: dict) -> None: ...

@on_event("order.paid")                       # a second listener is normal here
async def update_stats(self, data: dict) -> None: ...

await self._events.emit("order.paid", {"id": id})   # waits for all of them
self._events.dispatch("order.paid", {"id": id})     # returns immediately
```

All five decorators come in two shapes: `async def` (the default) and
`thread=True` for bodies that are all blocking calls. `ctx` is optional — take
it when you need `ctx.running` to leave a loop, `ctx.blocking(...)` to call
blocking code, or `ctx.run(...)` to write to the DB from inside a thread.

`@on_event` covers what `@job` cannot: `@job` is one name, **one** handler,
processed in order — a work queue. `@on_event` is one event, **many** handlers,
running in parallel — nobody owns the work, and the emitter doesn't know who is
listening. One listener raising doesn't stop the others. It is `fanout` /
`EventEmitter`, but in-process only: with `fam run --workers 4` an event does
not cross to the other three processes.

`@worker` covers what `@interval` and `@job` cannot: a setup phase **before**
the loop (open the camera, load the model) and a body that runs until you stop
it. Crashes restart with backoff; calling it again with the same `key` returns
the running instance instead of opening a second stream.

`stop()` waits for the loop's `finally:` to finish, so anything you write after
it runs with the camera already closed — put resource cleanup in `finally:` and
business cleanup after the call.

Write `while ctx.running:`, not `while True:` — a loop that never checks makes
Ctrl+C look dead for the whole shutdown timeout. The framework says so at
startup (`worker.endless_loop`) rather than letting you find out at 2am.

`fam run` starts 4 workers, so a hand-written `while True: sleep(5)` runs
**four times**. `single=True` (the default) locks it down: measured 5 runs
across 1 process, versus 20 runs across 4 with the lock off. The lock is
`flock` (one machine) or Redis (many), picked automatically.

The `@job` queue lives in RAM — **shutdown loses whatever hasn't run**, and the
framework logs that count instead of hiding it. Work that must not be lost
belongs in `@rabbitmq_subscriber`. Details:
[docs/background.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/background.md).

## Request/response, NestJS-compatible

`publish`/`emit` fire and forget. `send` waits for an answer — the NestJS
`client.send()` / `@MessagePattern()` pair, same wire format:

```python
# the side that answers — a normal service that happens to `return`
@rabbitmq_responder("sum", queue="math")
async def add(self, data: list[int]) -> int:
    return sum(data)

# the side that calls
total = await self._mq.send("sum", [1, 2, 3, 4], queue="math")   # -> 10
```

Available on **all four**: `@rabbitmq_responder`, `@redis_responder`,
`@mqtt_responder`, `@kafka_responder`.

The packet layout is taken from `@nestjs/microservices` source, so a NestJS
microservice and a service built on this library talk to each other with no
translation layer — verified both ways, on all four transports, against NestJS
11.2.1. Object patterns (`{"cmd": "sum"}`) included, key ordering and all.

`send` turns a queue into a network call, which brings back everything queues
exist to avoid — [docs/rpc.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/rpc.md)
says when not to use it.

## Redis, MQTT, Kafka (also optional)

Same shape as RabbitMQ: one package under `infrastructure/`, one `APP_<NAME>__*`
variable group, **off** by default, the library is imported only when enabled, and
all of them reconnect automatically.

```python
await redis.cached("report:A", compute, ttl=30)        # miss = compute, hit = skip
await mqtt.publish("devices/kitchen/light", "ON", qos=1, retain=True)
await kafka.publish("orders", order, key=order.id)     # same key = same order

@redis_subscriber("price:*")                    # Redis: every worker gets a copy
@mqtt_subscriber("devices/+/temperature", qos=1)  # MQTT: + one level, # all levels
@kafka_subscriber("orders", group="warehouse")  # Kafka: one cursor per group
```

| You need | Use |
|---|---|
| messages must not be lost, work split across workers | RabbitMQ |
| fast, every worker gets a copy, losing a few is fine | Redis |
| devices, flaky networks, long-lived connections | MQTT |
| replayable history, several independent reader groups | Kafka |

## Operations

```bash
curl localhost:8000/api/health        # liveness
curl localhost:8000/api/health/ready  # readiness, pings the database
curl localhost:8000/api/metrics       # Prometheus metrics
fam migrate                           # run migrations (SQL)
fam info                              # current config + production warnings
```

Guards, circuit breaker, metrics and tracing:
[docs/operations.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/operations.md).

## Layout of this repository

```
fastapi_modular/    THE LIBRARY — what gets packaged and installed
  core/             DI, controllers, config, WebSocket, guards, metrics
  infrastructure/   database, rabbitmq, redis, mqtt, kafka (one package each)
  cli/              init · new · module · provider · dev · run · install · env
                    info · migrate · test · lint · clean · build · publish
  factory.py        create_app()
  discovery.py      scans the application package and builds routers
src/                SAMPLE APPLICATION — not shipped in the package; delete freely
  main.py           entry point: assembles the app — your file, not the framework's
  core/config.py    AppSettings: subclass Settings to add your own .env variables
  core/lifespan.py  application-specific startup / shutdown work
  api/              business modules; every subdirectory is one module
tests/              1090 tests that need no infrastructure, 292 more with real drivers/servers
docs/               reference documentation (Vietnamese)
```

`fastapi_modular/` imports nothing from `src/`. All it knows is "there is a
package called `src.api`, go scan it" — so a different layout is fine, declared
once in `src/main.py`: `register_routes(app, package="company.service")`.

## Contributing

```bash
git clone git@github.com:quanglinh2909/fastapi-modular.git && cd fastapi-modular
pip install -e ".[all,dev]"
fam dev                             # runs the sample app in src/
fam test
fam lint fastapi_modular src tests
```

Tests that need real infrastructure only run when the matching environment
variable is set:

```bash
docker run -d -p 6379:6379 redis:7-alpine
TEST_REDIS_URL=redis://localhost:6379/0 fam test
```

See the top of each `tests/test_<name>.py` for the Docker command and the
variables it needs.

## License

MIT — see [LICENSE](https://github.com/quanglinh2909/fastapi-modular/blob/main/LICENSE).

## Documentation

Written in Vietnamese, organised for reference rather than reading front to back.

- [docs/architecture.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/architecture.md) — module layout, DI, the NestJS comparison
- [docs/config.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/config.md) — Settings, precedence, adding your own variables
- [docs/database.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/database.md) — SQL: memory / SQLite / PostgreSQL — entities, foreign keys, query builder, transactions
- [docs/mongodb.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/mongodb.md) — MongoDB: queries, nested data, and what is not there (no JOIN, no transactions)
- [docs/migrations.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/migrations.md) — Alembic: generate, run, roll back
- [docs/websocket.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/websocket.md) — WebSocket gateway, rooms, Postman, Next.js
- [docs/rabbitmq.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/rabbitmq.md) — all 5 exchange types, TTL, background consumers, `.retry` / `.dlq`
- [docs/background.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/background.md) — scheduled work (`@interval`/`@cron`/`@timeout`), an in-process job queue (`@job`), long-running loops (`@worker`) and in-process fanout (`@on_event`)
- [docs/rpc.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/rpc.md) — `emit` / `send` / `@rabbitmq_responder`, NestJS-compatible wire format
- [docs/redis.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/redis.md) — cache, atomic counters, pub/sub
- [docs/mqtt.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/mqtt.md) — QoS, retain, `+` and `#` topic matching
- [docs/kafka.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/kafka.md) — consumer groups, partitions, `.dlt`
- [docs/providers.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/providers.md) — pluggable providers: pick an implementation by name at runtime
- [docs/operations.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/operations.md) — guards, circuit breaker, metrics, tracing
