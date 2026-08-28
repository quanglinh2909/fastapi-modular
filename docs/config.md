# Cấu hình

Mọi cấu hình đi qua một đối tượng `Settings` (pydantic-settings). Khung định
nghĩa phần của khung; **ứng dụng kế thừa để thêm phần của mình**.

---

## Bạn đang cần làm gì?

| Việc bạn muốn làm | Đọc mục |
|---|---|
| "Thêm biến cấu hình của riêng tôi" | [Thêm biến của riêng bạn](#thêm-biến-của-riêng-bạn) |
| "Biến nào thắng: .env hay biến môi trường?" | [Thứ tự ưu tiên](#thứ-tự-ưu-tiên) |
| "`.env` sinh ra sao, đổi thế nào" | [`.env` do `fam env` sinh ra](#env-do-fam-env-sinh-ra) |
| "Sợ mang DEBUG=true lên production" | [Chặn cấu hình nguy hiểm](#chặn-cấu-hình-nguy-hiểm-lên-production) |
| "Tra một biến `APP_*` nghĩa là gì" | [Các nhóm định nghĩa sẵn](#các-nhóm-khung-định-nghĩa-sẵn) — và bảng chi tiết trong doc của từng phần |
| "Sửa `.env` mà không thấy đổi gì" | [Hỏng thì tra ở đây](#hỏng-thì-tra-ở-đây) |

---

## Thứ tự ưu tiên

```
biến môi trường  >  file .env  >  giá trị mặc định trong model
```

Nghĩa là `APP_DB__DRIVER=postgres fam dev` thắng dòng `APP_DB__DRIVER=sqlite`
trong `.env`. Đây cũng là cách bộ test tự cắt mọi đường ra hạ tầng thật.

Quy tắc đặt tên:

| Trong code | Trong .env |
|---|---|
| `settings.name` | `APP_NAME` |
| `settings.db.driver` | `APP_DB__DRIVER` — nhóm lồng nhau ngăn bằng **hai** gạch dưới |
| `settings.jwt.ttl_seconds` | `APP_JWT__TTL_SECONDS` |

Biến lạ trong `.env` bị **bỏ qua**, không gây lỗi (`extra="ignore"`) — nên gõ
sai tên biến thì app vẫn chạy với giá trị mặc định. Với những biến khung từng
đổi tên, có cảnh báo `config.deprecated_env` lúc khởi động; biến của bạn thì
không ai canh được, hãy tự kiểm bằng `/api/health/ready` hoặc log lúc boot.

---

## Thêm biến của riêng bạn

Ba bước, không cần đăng ký ở đâu cả:

```python
# src/core/config.py
from pydantic import BaseModel, Field
from fastapi_modular import Settings


class JwtSettings(BaseModel):          # nhóm lồng nhau -> APP_JWT__*
    secret: str = ""
    ttl_seconds: int = 3600


class AppSettings(Settings):           # Settings của khung + phần của bạn
    team_name: str = Field(default="chua-dat", alias="APP_TEAM_NAME")
    jwt: JwtSettings = Field(default_factory=JwtSettings, alias="APP_JWT")
```

```python
# src/main.py
from src.core.config import AppSettings
from fastapi_modular import create_app

app = create_app(AppSettings())
```

```bash
# .env
APP_TEAM_NAME=to-backend
APP_JWT__SECRET=doi-cai-nay-di
APP_JWT__TTL_SECONDS=7200
```

Xong. Không phải sửa file nào của khung.

### Dùng trong service

Nhận bằng **chính lớp con** để có gợi ý kiểu đầy đủ:

```python
@injectable
class TokenService:
    def __init__(self, settings: AppSettings) -> None:
        self._secret = settings.jwt.secret          # IDE gợi ý được
```

`bind_settings()` (và `create_app()`) đăng ký instance dưới **cả** `Settings` lẫn mọi lớp con trong
chuỗi kế thừa. Nhờ vậy thư viện hỏi `Settings`, code của bạn hỏi `AppSettings`,
và cả hai nhận **đúng một đối tượng**:

```python
container.resolve(Settings) is container.resolve(AppSettings)   # True
```

Chi tiết đáng biết: container tra provider theo **tên lớp**. Nếu chỉ đăng ký
`Settings` thì khai `def __init__(self, settings: AppSettings)` sẽ báo
`Không có provider 'AppSettings'`.

### Chỗ khung tự dựng cấu hình

Alembic và vài đường vào khác không đi qua `create_app()`. Bảo chúng dùng lớp
của bạn:

```python
# migrations/env.py
from src.core.config import AppSettings
from fastapi_modular import use_settings

use_settings(AppSettings)
settings = get_settings()          # giờ trả về AppSettings
```

`create_app(AppSettings())` đã gọi `use_settings` giúp bạn, nên chỉ những điểm
vào độc lập mới phải gọi tay.

| Hàm | Làm gì |
|---|---|
| `create_app(settings)` | dùng instance này, và ghi nhớ lớp của nó |
| `use_settings(cls)` | khai lớp cho những chỗ khung tự dựng cấu hình |
| `get_settings()` | instance dùng chung, dựng từ lớp đã khai (có cache) |
| `settings_class()` | lớp đang được dùng |

Ba hàm đầu import thẳng từ `fastapi-modular`. Riêng `settings_class()` chưa được
xuất ở gốc, phải lấy từ module con:

```python
from fastapi_modular import create_app, get_settings, use_settings
from fastapi_modular.core.config import settings_class
```

---

## `.env` do `fam env` sinh ra

Mỗi thành phần chiếm một **khối** có mốc đầu/cuối:

```
# >>> rabbitmq (sinh bởi fam env) >>>
...
# <<< rabbitmq <<<
```

Chạy lại `fam env rabbitmq` chỉ **thay khối đó**. Mọi dòng nằm ngoài các
mốc — kể cả biến của riêng bạn — được giữ nguyên, nên cứ để `APP_JWT__SECRET`
cạnh chúng cũng không sao.

Mỗi biến sinh ra kèm ba thứ: giải thích, **bắt buộc hay tuỳ chọn**, và **giá trị
mặc định nếu xoá dòng đi**:

```
# Backend database đang dùng. Xoá dòng này thì app chạy bằng bộ nhớ tạm và mất
# sạch dữ liệu mỗi lần restart.
# BẮT BUỘC — xoá dòng này thì app quay về memory, gần như chắc chắn không phải
# thứ bạn muốn
APP_DB__DRIVER=sqlite
```

Giá trị mặc định in ra không phải gõ tay: nó được đọc thẳng từ model Settings,
nên không thể lệch với code.

---

## Chặn cấu hình nguy hiểm lên production

`APP_ENV=prod` bật một loạt kiểm tra, mỗi thứ sai là một dòng
`config.unsafe_for_production` lúc khởi động: CORS mở cho mọi domain,
`debug=True`, database `memory`, `schema_mode` khác `off`, `drop_columns=True`,
WebSocket adapter `local` khi chạy nhiều worker.

Cảnh báo chứ không chặn — app vẫn lên, vì tắt sản xuất vì một cấu hình đáng ngờ
còn tệ hơn. Muốn chặn thì đọc `settings.check_production_safety()` rồi tự quyết.

---

## Hỏng thì tra ở đây

| Bạn thấy gì | Nguyên nhân |
|---|---|
| Sửa `.env` mà app không đổi gì | biến môi trường **thắng** `.env` — `echo $APP_DB__DRIVER` xem có ai đặt sẵn không; hoặc chưa khởi động lại |
| Biến bạn thêm vào `.env` không tới được code | chưa khai trong `AppSettings` — [Thêm biến của riêng bạn](#thêm-biến-của-riêng-bạn). Biến lạ bị **bỏ qua trong im lặng** (`extra="ignore"`), không báo lỗi |
| Nhóm lồng nhau không nhận | thiếu **hai** gạch dưới: `APP_DB__DRIVER` chứ không phải một gạch |
| `fam env <x>` ghi đè mất chỉnh sửa của tôi | nó chỉ THAY khối giữa hai mốc `# >>> x <<<`; mọi dòng ngoài khối được giữ nguyên. Đã sửa trong khối thì chép ra ngoài |
| `config.unsafe_for_production` lúc khởi động | `APP_ENV=prod` mà còn CORS mở, `debug=true`, database `memory`, `schema_mode` khác `off`, hoặc WebSocket adapter `local` với nhiều worker |
| App chạy bằng bộ nhớ tạm, restart là mất dữ liệu | thiếu `APP_DB__DRIVER` — chạy `fam install sqlite` |
| Không chắc app đang đọc cấu hình nào | `fam info` in ra driver, kết nối và thư viện đã cài |

---

## Các nhóm khung định nghĩa sẵn

| Nhóm | Tiền tố | Tài liệu |
|---|---|---|
| ứng dụng | `APP_NAME`, `APP_VERSION`, `APP_ENV`, `APP_DEBUG`, `APP_HOST`, `APP_PORT`, `APP_API_PREFIX` | [bên dưới](#ứng-dụng) |
| log | `APP_LOG__*` | [bên dưới](#log) |
| CORS | `APP_CORS__*` | [bên dưới](#cors) |
| database | `APP_DB__*` | [database.md](database.md) · [mongodb.md](mongodb.md) |
| WebSocket | `APP_WS__*` | [websocket.md](websocket.md) |
| RabbitMQ | `APP_RABBITMQ__*` | [rabbitmq.md](rabbitmq.md) |
| Redis | `APP_REDIS__*` | [redis.md](redis.md) |
| MQTT | `APP_MQTT__*` | [mqtt.md](mqtt.md) |
| Kafka | `APP_KAFKA__*` | [kafka.md](kafka.md) |
| việc theo lịch | `APP_SCHEDULER__*` | [background.md](background.md) |
| hàng đợi việc | `APP_JOBS__*` | [background.md](background.md) |
| worker chạy nền | `APP_WORKERS__*` | [background.md](background.md) |
| sự kiện trong tiến trình | `APP_EVENTS__*` | [background.md](background.md) |

### ứng dụng

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `APP_NAME` | `fastapi-modular` | Tên hiện trong trang `/docs` và trong log |
| `APP_VERSION` | `0.1.0` | Phiên bản hiện trong OpenAPI. Của **ứng dụng bạn**, không phải của khung |
| `APP_ENV` | `local` | `local` / `dev` / `staging` / `prod`. `prod` tắt `/docs`, `/redoc`, `/openapi.json` và bật kiểm tra an toàn |
| `APP_DEBUG` | `true` | `true` = trả chi tiết lỗi ra client. Đặt `false` ở prod |
| `APP_HOST` | `0.0.0.0` | `fam dev` / `fam run` lấy từ đây, không cần truyền tham số |
| `APP_PORT` | `8000` | nt |
| `APP_API_PREFIX` | `/api` | Tiền tố của **mọi** route REST. Đổi thành `/v1` thì health là `/v1/health`. Không ảnh hưởng đường dẫn WebSocket |

### log

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `APP_LOG__LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `APP_LOG__JSON_FORMAT` | `false` | `true` = mỗi dòng log là một JSON, cho hệ thống gom log. `false` = log màu, dễ đọc khi chạy local |

Áp dụng khi `src/main.py` gọi `configure_logging(settings.log)` — `create_app()`
đã gọi sẵn.

### CORS

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `APP_CORS__ALLOW_ORIGINS` | `["*"]` | Danh sách origin được gọi API |
| `APP_CORS__ALLOW_METHODS` | `["*"]` | Method được phép |
| `APP_CORS__ALLOW_HEADERS` | `["*"]` | Header được phép |
| `APP_CORS__ALLOW_CREDENTIALS` | `true` | Cho phép gửi kèm cookie / `Authorization` |

Ba biến đầu là **danh sách**, phải viết dạng JSON trong `.env`. Dạng ngăn bằng
dấu phẩy KHÔNG chạy — nó ném `SettingsError` ngay lúc khởi động:

```dotenv
APP_CORS__ALLOW_ORIGINS=["https://app.cua-toi.vn","https://admin.cua-toi.vn"]
```

`["*"]` đi cùng `allow_credentials=true` khiến Starlette phản chiếu lại mọi
`Origin` — tức **bất kỳ website nào** cũng gọi được API kèm cookie của người
dùng. Vì vậy ở `APP_ENV=prod` nó bị `check_production_safety()` cảnh báo; hãy
liệt kê domain cụ thể.
