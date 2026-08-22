"""Dựng một dự án chạy được ngay.

    pym new blog     tạo thư mục blog/ rồi đổ file vào đó
    pym init         đổ file vào THƯ MỤC HIỆN TẠI

Bộ khung sinh ra:

    src/main.py            điểm vào — lắp ráp app, file của bạn, sửa thoải mái
    src/core/config.py     cấu hình: kế thừa Settings để thêm biến .env của bạn
    src/core/lifespan.py   việc lúc khởi động / lúc tắt của riêng ứng dụng
    src/api/health/        một module mẫu

Mọi thứ khác (module nghiệp vụ, gateway, consumer) sinh sau bằng `pym module`.

`init` không bao giờ GHI ĐÈ: file nào đã có thì bỏ qua và báo lại. Nhờ vậy chạy
nó trong một thư mục đang có sẵn code là an toàn, và chạy lại lần hai chỉ bù
những file còn thiếu.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

TEN_HOP_LE = re.compile(r"^[a-z][a-z0-9_-]*$")

REPO = "https://github.com/quanglinh2909/pymodular"

GOC = '''"""Ứng dụng — code của bạn.

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
'''

MAIN = '''"""Điểm vào — chạy bằng `pym dev`.

FILE NÀY LÀ CỦA BẠN. Khung cố ý không giấu phần lắp ráp: mỗi dòng dưới đây làm
đúng một việc, xoá được, đổi thứ tự được, chèn thêm được.

Thêm module nghiệp vụ thì KHÔNG phải sửa file này — `register_routes` tự quét
thư mục `src/api/`. Còn thêm middleware, đổi CORS, gắn router của thư viện
ngoài, bọc lifespan... thì sửa ngay tại đây.

Chưa cần sửa gì thì cả khối dưới rút lại còn hai dòng:

    from pymodular import create_app
    app = create_app(AppSettings())
"""

from __future__ import annotations

from pymodular import (
    add_middleware,
    bind_settings,
    configure_logging,
    new_fastapi,
    register_error_handlers,
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

# Quét app/, gắn mọi @controller và @gateway tìm được.
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

from pymodular import Settings


class AppSettings(Settings):
    """Settings của khung, cộng thêm phần của ứng dụng."""

    # Ví dụ một biến riêng — đọc từ APP_TEAM_NAME trong .env. Xoá được.
    team_name: str = Field(default="", alias="APP_TEAM_NAME")
'''

HEALTH = '''"""Module mẫu — xoá được, hoặc giữ lại làm endpoint kiểm tra sức khoẻ."""

from __future__ import annotations

from pymodular import Settings, controller, get


@controller(prefix="/health", tags=["health"])
class HealthController:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @get("", summary="Tiến trình còn sống")
    async def live(self) -> dict[str, str]:
        return {"status": "ok", "service": self._settings.name}
'''

ENV = """APP_NAME={ten}
APP_ENV=local
APP_DEBUG=true
APP_HOST=0.0.0.0
APP_PORT=8000

# Chưa chọn database thì app chạy bằng bộ nhớ tạm (mất dữ liệu khi restart).
# Thêm database:  pym install sqlite     (hoặc postgres, mongodb)
# Thêm hàng đợi:  pym install rabbitmq   (hoặc redis, mqtt, kafka)
# Mỗi lệnh vừa cài thư viện, vừa ghi biến vào file này kèm giải thích.
#
# Biến của RIÊNG bạn: thêm thẳng vào đây, rồi khai trong src/core/config.py.
# APP_TEAM_NAME=to-backend
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

README = """# {ten}

Dựng bằng [pymodular]({repo}) — FastAPI theo kiến trúc module kiểu NestJS.

## Chạy

```bash
python -m venv .venv && . .venv/bin/activate
pip install pymodular
pym dev                      # http://localhost:8000/docs
```

## Cấu trúc

```
src/
├── main.py            điểm vào: lắp ráp app — sửa thoải mái, không phải của khung
├── core/
│   ├── config.py      AppSettings — thêm biến .env của riêng bạn
│   └── lifespan.py    việc lúc khởi động / lúc tắt của riêng bạn
└── api/               mỗi thư mục con là một module; thêm module KHÔNG phải sửa main.py
    └── health/
```

## Lệnh

Rút gọn được tới khi nào tiền tố còn chỉ đúng một lệnh: `pym mo alerts` chạy y
hệt `pym module alerts`. Nhập nhằng thì `pym` hỏi lại chứ không đoán.

| Lệnh | Rút gọn | Làm gì |
|---|---|---|
| `pym dev` | `pym d` | chạy kèm autoreload |
| `pym run --workers 4` | `pym r` | chạy chế độ production |
| `pym module <tên>` | `pym mo` | sinh module: controller + service + dto + entity |
| `pym module <tên> --gateway` | | kèm gateway WebSocket (`--consumer` cho RabbitMQ) |
| `pym env <thành-phần>` | `pym e` | chỉ ghi biến vào `.env`, không cài gì |
| `pym info` | `pym inf` | đang nối vào đâu, thư viện nào đã cài |
| `pym migrate` | `pym mi` | chạy migration (Alembic) |
| `pym test` · `pym lint` | `pym t` · `pym l` | pytest · ruff |
| `pym clean` | `pym c` | xoá cache và bản dựng (không đụng `data/`) |
| **Thêm database** | | *cài thư viện **rồi** ghi biến vào `.env`* |
| `pym install sqlite` | `pym ins s` | file `.db`, không cần server |
| `pym install postgres` | `pym ins p` | PostgreSQL |
| `pym install mongodb` | `pym ins mo` | MongoDB |
| **Thêm hàng đợi** | | *cài thư viện **rồi** ghi biến vào `.env`* |
| `pym install rabbitmq` | `pym ins ra` | hàng đợi bền, thử lại + DLQ |
| `pym install redis` | `pym ins re` | cache, đếm nguyên tử, pub/sub |
| `pym install mqtt` | `pym ins mq` | thiết bị IoT |
| `pym install kafka` | `pym ins k` | nhật ký sự kiện đọc lại được |
| `pym install ws-redis` | `pym ins w` | phát tin WebSocket xuyên nhiều worker |
| `pym install all` | `pym ins a` | tất cả những thứ trên |

`pym --help` cho danh sách đầy đủ. Host và cổng lấy từ `APP_HOST` / `APP_PORT`
trong `.env`.

`pym install` ghi vào `.env` mỗi biến kèm giải thích, cho biết nó **bắt buộc hay
tuỳ chọn** và **mặc định là gì** nếu xoá dòng đi. Không cài, không bật thì lớp đó
nằm im — không import thư viện, không mở kết nối, không đổi hành vi nào.

## Thêm module

```bash
pym module {vi_du}                 # controller + service + dto + entity
pym module {vi_du} --gateway       # kèm gateway WebSocket
pym module {vi_du} --consumer      # kèm consumer RabbitMQ
```

Route xuất hiện ngay; chỉ thân hàm trong service là chưa viết (gọi vào trả 501
kèm tên hàm).

## Thêm biến cấu hình của riêng bạn

```python
# src/core/config.py
from pydantic import Field

from pymodular import Settings


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


def lam_sach_ten(tho: str) -> str:
    """Tên thư mục -> tên dự án dùng được: "Dự Án Mới" -> "du-an-moi"."""
    bo_dau = unicodedata.normalize("NFD", tho).encode("ascii", "ignore").decode()
    gon = re.sub(r"[^a-zA-Z0-9]+", "-", bo_dau).strip("-").lower()
    return gon or "app"


def tao_du_an(ten: str, root: Path) -> int:
    """`new`: tạo thư mục mới rồi đổ file vào."""
    if not TEN_HOP_LE.match(ten):
        print(f"Tên dự án không hợp lệ: {ten!r}. Chữ thường, số, gạch ngang hoặc gạch dưới.")
        return 1

    dich = root / ten
    if dich.exists() and any(dich.iterdir()):
        print(
            f"{dich} đã tồn tại và không rỗng — chọn tên khác, xoá nó trước, "
            f"hoặc vào trong đó chạy `pym init`."
        )
        return 1

    so = _ghi(dich, ten, ghi_de=True)
    print(f"Đã tạo {dich}/ với {so} file:")
    _in_cay(dich, ten)
    print(f"\nChạy thử:\n    cd {ten}\n    pip install pymodular\n    pym dev")
    print("\nRồi mở http://localhost:8000/docs")
    return 0


def init_du_an(root: Path, ten: str | None = None) -> int:
    """`init`: đổ file vào THƯ MỤC HIỆN TẠI, không tạo thêm một cấp.

    Không ghi đè file nào đã có — thư mục đang có code vẫn chạy được lệnh này.
    """
    dich = root.resolve()
    ten = ten or lam_sach_ten(dich.name)
    if not TEN_HOP_LE.match(ten):
        print(f"Tên dự án không hợp lệ: {ten!r}. Dùng --name để đặt tên khác.")
        return 1

    da_co = [d for d in _noi_dung(ten) if (dich / d).exists()]
    so = _ghi(dich, ten, ghi_de=False)

    if not so:
        print(f"{dich} đã có đủ file rồi, không phải làm gì.")
        return 0

    print(f"Đã thêm {so} file vào {dich} (tên dự án: {ten}):")
    _in_cay(dich, ten, bo_qua=set(da_co))
    if da_co:
        print("\nGiữ nguyên file đã có, KHÔNG ghi đè:")
        for d in da_co:
            print(f"    {d}")
    print("\nChạy thử:\n    pip install pymodular\n    pym dev")
    return 0


def _noi_dung(ten: str) -> dict[str, str]:
    return {
        "src/__init__.py": GOC,
        "src/main.py": MAIN,
        "src/core/__init__.py": '"""Thứ dùng chung của ứng dụng: config, helper, guard riêng."""\n',
        "src/core/config.py": CONFIG,
        "src/core/lifespan.py": LIFESPAN,
        "src/api/__init__.py": '"""Các module nghiệp vụ; mỗi thư mục con là một module."""\n',
        "src/api/health/__init__.py": '"""Module health."""\n',
        "src/api/health/health_controller.py": HEALTH,
        ".env": ENV.format(ten=ten),
        ".gitignore": GITIGNORE,
        "README.md": README.format(ten=ten, vi_du="alerts", repo=REPO),
    }


def _ghi(dich: Path, ten: str, *, ghi_de: bool) -> int:
    """Ghi file, trả về số file thật sự được tạo."""
    so = 0
    for duong_dan, noi_dung in _noi_dung(ten).items():
        f = dich / duong_dan
        if f.exists() and not ghi_de:
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(noi_dung, encoding="utf-8")
        so += 1
    return so


def _in_cay(dich: Path, ten: str, bo_qua: set[str] | None = None) -> None:
    for duong_dan in _noi_dung(ten):
        if not bo_qua or duong_dan not in bo_qua:
            print(f"    {duong_dan}")
