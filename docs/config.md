# Cấu hình

Mọi cấu hình đi qua một đối tượng `Settings` (pydantic-settings). Khung định
nghĩa phần của khung; **ứng dụng kế thừa để thêm phần của mình**.

---

## Thứ tự ưu tiên

```
biến môi trường  >  file .env  >  giá trị mặc định trong model
```

Nghĩa là `APP_DB__DRIVER=postgres pym dev` thắng dòng `APP_DB__DRIVER=sqlite`
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
from pymodular import Settings


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
from pymodular import create_app

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
from pymodular import use_settings

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

---

## `.env` do `pym env` sinh ra

Mỗi thành phần chiếm một **khối** có mốc đầu/cuối:

```
# >>> rabbitmq (sinh bởi pym env) >>>
...
# <<< rabbitmq <<<
```

Chạy lại `pym env rabbitmq` chỉ **thay khối đó**. Mọi dòng nằm ngoài các
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

## Các nhóm khung định nghĩa sẵn

| Nhóm | Tiền tố | Tài liệu |
|---|---|---|
| ứng dụng | `APP_NAME`, `APP_ENV`, `APP_DEBUG`, `APP_HOST`, `APP_PORT` | — |
| log | `APP_LOG__*` | [operations.md](operations.md) |
| CORS | `APP_CORS__*` | [operations.md](operations.md) |
| database | `APP_DB__*` | [database.md](database.md) |
| WebSocket | `APP_WS__*` | [websocket.md](websocket.md) |
| RabbitMQ | `APP_RABBITMQ__*` | [rabbitmq.md](rabbitmq.md) |
| Redis | `APP_REDIS__*` | [redis.md](redis.md) |
| MQTT | `APP_MQTT__*` | [mqtt.md](mqtt.md) |
| Kafka | `APP_KAFKA__*` | [kafka.md](kafka.md) |
