"""Dựng một dự án chạy được ngay.

    fam new blog     tạo thư mục blog/ rồi đổ file vào đó
    fam init         đổ file vào THƯ MỤC HIỆN TẠI

Bộ khung sinh ra:

    src/main.py            điểm vào — lắp ráp app, file của bạn, sửa thoải mái
    src/core/config.py     cấu hình: kế thừa Settings để thêm biến .env của bạn
    src/core/lifespan.py   việc lúc khởi động / lúc tắt của riêng ứng dụng
    src/api/health/        một module mẫu

Mọi thứ khác (module nghiệp vụ, gateway, consumer) sinh sau bằng `fam module`.

`init` không bao giờ GHI ĐÈ: file nào đã có thì bỏ qua và báo lại. Nhờ vậy chạy
nó trong một thư mục đang có sẵn code là an toàn, và chạy lại lần hai chỉ bù
những file còn thiếu.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from fastapi_modular import __version__

VALID_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")

REPO = "https://github.com/quanglinh2909/fastapi-modular"

ROOT = '''"""Ứng dụng — code của bạn.

    src/main.py     điểm vào: lắp ráp app, sửa thoải mái
    src/core/       thứ dùng chung của ứng dụng (config, helper, guard riêng)
    src/api/        các module nghiệp vụ; mỗi thư mục con là một module
"""
'''

LIFESPAN = '''"""Vòng đời ứng dụng — FILE CỦA BẠN.

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
from fastapi_modular import get_logger
from fastapi_modular import lifespan as framework_lifespan

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
'''

MAIN = '''"""Điểm vào — chạy bằng `fam dev`.

FILE NÀY LÀ CỦA BẠN. Khung cố ý không giấu phần lắp ráp: mỗi dòng dưới đây làm
đúng một việc, xoá được, đổi thứ tự được, chèn thêm được.

Thêm module nghiệp vụ thì KHÔNG phải sửa file này — `register_routes` tự quét
thư mục `src/api/`. Còn thêm middleware, đổi CORS, gắn router của thư viện
ngoài, bọc lifespan... thì sửa ngay tại đây.

Chưa cần sửa gì thì cả khối dưới rút lại còn hai dòng:

    from fastapi_modular import create_app
    app = create_app(AppSettings())
"""

from __future__ import annotations

from fastapi_modular import (
    add_middleware,
    bind_settings,
    configure_logging,
    new_fastapi,
    register_error_handlers,
    register_providers,
    register_routes,
)

from src.core.config import AppSettings
from src.core.lifespan import lifespan

# Đọc .env, chốt lớp cấu hình cho cả tiến trình, cắm vào DI container.
settings = bind_settings(AppSettings())
configure_logging(settings.log)

# lifespan: khung lo database và hạ tầng, phần việc riêng nằm ở
# src/core/lifespan.py — sửa ở đó, không phải ở đây.
app = new_fastapi(settings, lifespan=lifespan)

# CORS + access log + request-id. Middleware của bạn thêm sau dòng này sẽ chạy
# TRƯỚC ba cái đó (FastAPI chạy ngược thứ tự add).
add_middleware(app, settings)

# Đổi lỗi nghiệp vụ thành JSON có mã và request_id.
register_error_handlers(app, debug=settings.debug)

# Quét src/providers/, dựng sổ cho mỗi họ provider. Không có thư mục đó thì
# bỏ qua, không lỗi. Phải chạy TRƯỚC register_routes: service nhận sổ qua
# __init__, mà controller nhận service.
register_providers()

# Quét src/api/, gắn mọi @controller và @gateway tìm được.
register_routes(app, prefix=settings.api_prefix)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {"service": settings.name, "version": settings.version}
'''

CONFIG = '''"""Cấu hình của ứng dụng — thêm biến `.env` của riêng bạn ở đây.

`Settings` mang sẵn phần của khung (database, WebSocket, RabbitMQ, Redis, MQTT,
Kafka, log, CORS). Kế thừa nó là đủ để pydantic-settings đọc thêm biến của bạn,
theo đúng quy tắc cũ: biến môi trường thắng .env, .env thắng giá trị mặc định,
nhóm lồng nhau ngăn bằng hai gạch dưới.

    class JwtSettings(BaseModel):        # -> APP_JWT__SECRET, APP_JWT__TTL_SECONDS
        secret: str = ""
        ttl_seconds: int = 3600

    class AppSettings(Settings):
        jwt: JwtSettings = Field(default_factory=JwtSettings, alias="APP_JWT")

Service nhận nó qua DI bằng chính lớp con, và vẫn có gợi ý kiểu đầy đủ:

    @injectable
    class TokenService:
        def __init__(self, settings: AppSettings) -> None:
            self._secret = settings.jwt.secret
"""

from __future__ import annotations

from pydantic import Field

from fastapi_modular import Settings


class AppSettings(Settings):
    """Settings của khung, cộng thêm phần của ứng dụng."""

    # Ví dụ một biến riêng — đọc từ APP_TEAM_NAME trong .env. Xoá được.
    team_name: str = Field(default="", alias="APP_TEAM_NAME")
'''

HEALTH = '''"""Module mẫu — xoá được, hoặc giữ lại làm endpoint kiểm tra sức khoẻ."""

from __future__ import annotations

from fastapi_modular import Settings, controller, get


@controller(prefix="/health", tags=["health"])
class HealthController:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @get("", summary="Tiến trình còn sống")
    async def live(self) -> dict[str, str]:
        return {"status": "ok", "service": self._settings.name}
'''

# Năm biến gốc, tách riêng vì `fam install` cũng cần: chạy `fam install sqlite`
# trong một thư mục chưa `fam init` thì .env chỉ có khối database, thiếu sạch
# APP_NAME/APP_ENV/APP_HOST — app chạy với toàn giá trị mặc định mà không ai báo.
BASE_ENV = """APP_NAME={name}
APP_ENV=local
APP_DEBUG=true
APP_HOST=0.0.0.0
APP_PORT=8000
"""

ENV = BASE_ENV + """
# Chưa chọn database thì app chạy bằng bộ nhớ tạm (mất dữ liệu khi restart).
# Thêm database:  fam install sqlite     (hoặc postgres, mongodb)
# Thêm hàng đợi:  fam install rabbitmq   (hoặc redis, mqtt, kafka)
# Mỗi lệnh vừa cài thư viện, vừa ghi biến vào file này kèm giải thích.
#
# Biến của RIÊNG bạn: thêm thẳng vào đây, rồi khai trong src/core/config.py.
# APP_TEAM_NAME=to-backend
"""

REQUIREMENTS = """\
# Thư viện của dự án. Người khác clone về chỉ cần:
#     pip install -r requirements.txt
#
# `fam install <thành-phần>` tự cập nhật dòng dưới — cài thêm redis thì nó thành
# fastapi-modular[redis,sqlite]>=... Đừng liệt kê tay sqlalchemy hay motor:
# khoảng phiên bản của chúng do fastapi-modular giữ.
fastapi-modular>={version}
"""

GITIGNORE = """# ---------------------------------------------------------------- Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python

# Gói và bản dựng
build/
dist/
sdist/
wheels/
*.egg
*.egg-info
.eggs/
MANIFEST

# Môi trường ảo
.venv/
venv/
ENV/
env/
.python-version

# Test, độ phủ, kiểm kiểu, lint
.pytest_cache/
.ruff_cache/
.mypy_cache/
.dmypy.json
.pytype/
.pyre/
.tox/
.nox/
.coverage
.coverage.*
coverage.xml
htmlcov/
*.cover
.hypothesis/
.cache/

# Jupyter
.ipynb_checkpoints/

# ------------------------------------------------------------- Dự án này
# .env chứa DSN và mật khẩu thật — KHÔNG BAO GIỜ commit.
.env
.env.*
!.env.example

data/
*.db
*.sqlite
*.sqlite3
*.log
logs/

# ------------------------------------------------------------------- IDE
.idea/
.vscode/
.fleet/
.zed/
*.sublime-project
*.sublime-workspace
*.swp
*.swo
*~

# --------------------------------------------------------- Hệ điều hành
.DS_Store
._*
Thumbs.db
Desktop.ini

# --------------------------------------------------------------- Công cụ
.direnv/
node_modules/
"""

README = """# {name}

Dựng bằng [fastapi-modular]({repo}) — FastAPI theo kiến trúc module kiểu NestJS.

## Chạy

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
fam dev                      # http://localhost:8000/docs
```

`requirements.txt` là chỗ ghi nhớ thư viện của dự án. `fam install sqlite` (hay
redis, rabbitmq...) vừa cài vừa cập nhật file này, nên người tiếp theo clone về
chỉ cần đúng lệnh trên — không phải đoán xem dự án cần driver nào.

## Cấu trúc

```
src/
├── main.py            điểm vào: lắp ráp app — sửa thoải mái, không phải của khung
├── core/
│   ├── config.py      AppSettings — thêm biến .env của riêng bạn
│   └── lifespan.py    việc lúc khởi động / lúc tắt của riêng bạn
└── api/               mỗi thư mục con là một module; thêm module KHÔNG phải sửa main.py
    └── health/

.env                   cấu hình — KHÔNG commit
requirements.txt       thư viện của dự án, `fam install` tự cập nhật
```

## Lệnh

Rút gọn được tới khi nào tiền tố còn chỉ đúng một lệnh: `fam mo alerts` chạy y
hệt `fam module alerts`. Nhập nhằng thì `fam` hỏi lại chứ không đoán.

| Lệnh | Rút gọn | Làm gì |
|---|---|---|
| `fam dev` | `fam d` | chạy kèm autoreload |
| `fam run --workers 4` | `fam r` | chạy chế độ production |
| `fam module <tên>` | `fam mo` | sinh module: controller + service + dto + entity |
| `fam module <tên> --gateway` | | kèm gateway WebSocket (`--consumer` cho RabbitMQ) |
| `fam env <thành-phần>` | `fam e` | chỉ ghi biến vào `.env`, không cài gì |
| `fam info` | `fam inf` | đang nối vào đâu, thư viện nào đã cài |
| `fam migrate` | `fam mi` | chạy migration (Alembic) |
| `fam test` · `fam lint` | `fam t` · `fam l` | pytest · ruff |
| `fam clean` | `fam c` | xoá cache và bản dựng (không đụng `data/`) |
| **Thêm database** | | *cài thư viện **rồi** ghi biến vào `.env`* |
| `fam install sqlite` | `fam ins s` | file `.db`, không cần server |
| `fam install postgres` | `fam ins p` | PostgreSQL |
| `fam install mongodb` | `fam ins mo` | MongoDB |
| **Thêm hàng đợi** | | *cài thư viện **rồi** ghi biến vào `.env`* |
| `fam install rabbitmq` | `fam ins ra` | hàng đợi bền, thử lại + DLQ |
| `fam install redis` | `fam ins re` | cache, đếm nguyên tử, pub/sub |
| `fam install mqtt` | `fam ins mq` | thiết bị IoT |
| `fam install kafka` | `fam ins k` | nhật ký sự kiện đọc lại được |
| `fam install ws-redis` | `fam ins w` | phát tin WebSocket xuyên nhiều worker |
| `fam install all` | `fam ins a` | tất cả những thứ trên |

`fam --help` cho danh sách đầy đủ. Host và cổng lấy từ `APP_HOST` / `APP_PORT`
trong `.env`.

`fam install` ghi vào `.env` mỗi biến kèm giải thích, cho biết nó **bắt buộc hay
tuỳ chọn** và **mặc định là gì** nếu xoá dòng đi. Không cài, không bật thì lớp đó
nằm im — không import thư viện, không mở kết nối, không đổi hành vi nào.

## Thêm module

```bash
fam module {vi_du}                 # controller + service + dto + entity
fam module {vi_du} --gateway       # kèm gateway WebSocket
fam module {vi_du} --consumer      # kèm consumer RabbitMQ
```

Route xuất hiện ngay; chỉ thân hàm trong service là chưa viết (gọi vào trả 501
kèm tên hàm).

## Thêm biến cấu hình của riêng bạn

```python
# src/core/config.py
from pydantic import Field

from fastapi_modular import Settings


class AppSettings(Settings):
    team_name: str = Field(default="", alias="APP_TEAM_NAME")
```

```bash
# .env
APP_TEAM_NAME=to-backend
```

Service nhận nó qua DI bằng chính lớp con:

```python
@injectable
class TokenService:
    def __init__(self, settings: AppSettings) -> None:
        self._team = settings.team_name
```

## Tài liệu

[{repo}/tree/main/docs]({repo}/tree/main/docs) — database, WebSocket, RabbitMQ,
Redis, MQTT, Kafka, cấu hình, vận hành.
"""


def clean_name(raw_: str) -> str:
    """Tên thư mục -> tên dự án dùng được: "Dự Án Mới" -> "du-an-moi"."""
    strip_accents = unicodedata.normalize("NFD", raw_).encode("ascii", "ignore").decode()
    narrowed = re.sub(r"[^a-zA-Z0-9]+", "-", strip_accents).strip("-").lower()
    return narrowed or "app"


def create_project(name: str, root: Path) -> int:
    """`new`: tạo thư mục mới rồi đổ file vào."""
    if not VALID_NAME.match(name):
        print(f"Tên dự án không hợp lệ: {name!r}. Chữ thường, số, gạch ngang hoặc gạch dưới.")
        return 1

    target = root / name
    if target.exists() and any(target.iterdir()):
        print(
            f"{target} đã tồn tại và không rỗng — chọn tên khác, xoá nó trước, "
            f"hoặc vào trong đó chạy `fam init`."
        )
        return 1

    count = _write(target, name, overwrite=True)
    print(f"Đã tạo {target}/ với {count} file:")
    _print_tree(target, name)
    print(f"\nChạy thử:\n    cd {name}\n    pip install fastapi-modular\n    fam dev")
    print("\nRồi mở http://localhost:8000/docs")
    return 0


def init_project(root: Path, name: str | None = None) -> int:
    """`init`: đổ file vào THƯ MỤC HIỆN TẠI, không tạo thêm một cấp.

    Không ghi đè file nào đã có — thư mục đang có code vẫn chạy được lệnh này.
    """
    target = root.resolve()
    name = name or clean_name(target.name)
    if not VALID_NAME.match(name):
        print(f"Tên dự án không hợp lệ: {name!r}. Dùng --name để đặt tên khác.")
        return 1

    existing = [d for d in _content(name) if (target / d).exists()]
    count = _write(target, name, overwrite=False)

    if not count:
        print(f"{target} đã có đủ file rồi, không phải làm gì.")
        return 0

    print(f"Đã thêm {count} file vào {target} (tên dự án: {name}):")
    _print_tree(target, name, skipped=set(existing))
    if existing:
        print("\nGiữ nguyên file đã có, KHÔNG ghi đè:")
        for d in existing:
            print(f"    {d}")
    print("\nChạy thử:\n    pip install fastapi-modular\n    fam dev")
    return 0


def _content(name: str) -> dict[str, str]:
    return {
        "src/__init__.py": ROOT,
        "src/main.py": MAIN,
        "src/core/__init__.py": '"""Thứ dùng chung của ứng dụng: config, helper, guard riêng."""\n',
        "src/core/config.py": CONFIG,
        "src/core/lifespan.py": LIFESPAN,
        "src/api/__init__.py": '"""Các module nghiệp vụ; mỗi thư mục con là một module."""\n',
        "src/api/health/__init__.py": '"""Module health."""\n',
        "src/api/health/health_controller.py": HEALTH,
        ".env": ENV.format(name=name),
        "requirements.txt": REQUIREMENTS.format(version=__version__),
        ".gitignore": GITIGNORE,
        "README.md": README.format(name=name, vi_du="alerts", repo=REPO),
    }


def _write(target: Path, name: str, *, overwrite: bool) -> int:
    """Ghi file, trả về số file thật sự được tạo."""
    count = 0
    for path, content in _content(name).items():
        f = target / path
        if f.exists() and not overwrite:
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
        count += 1
    return count


def _print_tree(target: Path, name: str, skipped: set[str] | None = None) -> None:
    for path in _content(name):
        if not skipped or path not in skipped:
            print(f"    {path}")
