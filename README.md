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
| `@Injectable()` | `@injectable` |
| `@Injectable({scope: Scope.REQUEST})` | `@injectable(scope=Scope.REQUEST)` |
| `forwardRef(() => X)` | `Lazy[X]` |
| `@InjectRepository(X) repo: Repository<X>` | `repo: Repository[X]` |
| `@UseGuards()` | `guards=[...]` on the controller or a single route |
| `@WebSocketGateway()` | `@gateway(path="/ws/…")` |
| `@SubscribeMessage('x')` | `@subscribe("x")` |
| `@EventPattern('x')` (RabbitMQ) | `@rabbitmq_subscriber("events", "x", queue="…")` |
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

`fam install` both installs the libraries and writes the matching variables into
`.env`. Plain pip works too: `pip install "fastapi-modular[sqlite,rabbitmq]"`.

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
| `fam install rabbitmq` | `fam ins ra` | durable queues, retry + DLQ |
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

Details:
[docs/database.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/database.md).

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
```

Not installed and not enabled means it behaves as if it never existed. If the
broker goes down the app keeps serving and reconnects on its own. Details:
[docs/rabbitmq.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/rabbitmq.md).

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
tests/              363 tests that need no infrastructure, 40 more when servers exist
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
- [docs/database.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/database.md) — memory / SQLite / PostgreSQL / MongoDB
- [docs/migrations.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/migrations.md) — Alembic: generate, run, roll back
- [docs/websocket.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/websocket.md) — WebSocket gateway, rooms, Postman, Next.js
- [docs/rabbitmq.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/rabbitmq.md) — exchanges, topics, background consumers, `.retry` / `.dlq`
- [docs/redis.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/redis.md) — cache, atomic counters, pub/sub
- [docs/mqtt.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/mqtt.md) — QoS, retain, `+` and `#` topic matching
- [docs/kafka.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/kafka.md) — consumer groups, partitions, `.dlt`
- [docs/providers.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/providers.md) — pluggable providers: pick an implementation by name at runtime
- [docs/operations.md](https://github.com/quanglinh2909/fastapi-modular/blob/main/docs/operations.md) — guards, circuit breaker, metrics, tracing
