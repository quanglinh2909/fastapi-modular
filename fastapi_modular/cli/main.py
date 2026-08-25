"""Điểm vào của lệnh `fastapi-modular`, gõ tắt là `fam`.

    fam init                 dựng dự án ngay trong THƯ MỤC HIỆN TẠI
    fam new duan-cua-toi     dựng dự án trong một thư mục mới
    fam dev                  chạy kèm autoreload
    fam run --workers 4      chạy chế độ production
    fam module alerts        sinh module nghiệp vụ
    fam provider sms viettel sinh provider cắm được
    fam install postgres     cài thư viện của một thành phần + ghi .env
    fam env postgres         chỉ ghi biến cấu hình vào .env
    fam info                 đang nối vào đâu, thư viện nào đã cài
    fam migrate              chạy migration (Alembic)
    fam test / fam lint      chạy test / soi lỗi tĩnh

Hai tên gọi cùng một chương trình; `fastapi-modular` là tên đầy đủ cho script và tài
liệu, `fam` là để gõ hằng ngày.

Lệnh và tham số dạng danh sách đều rút gọn được, miễn là KHÔNG NHẬP NHẰNG:

    fam mo alerts     = fam module alerts
    fam ins sq        = fam install sqlite
    fam d             = fam dev

`fam m` thì báo lỗi kèm gợi ý, vì cả `module` lẫn `migrate` đều bắt đầu bằng "m"
— thà hỏi lại còn hơn đoán bừa rồi chạy nhầm lệnh.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from fastapi_modular import __version__


def explain(typed: str, options: list[str], what: str) -> str:
    """Đổi một từ viết tắt thành lựa chọn đầy đủ, nếu chỉ có đúng một khả năng.

    Trả về nguyên `tu` khi nó đã là lựa chọn đầy đủ hoặc không khớp gì — để
    argparse tự báo lỗi theo cách của nó. Chỉ ném lỗi khi NHẬP NHẰNG, vì đó là
    trường hợp duy nhất mà im lặng đoán bừa sẽ chạy nhầm việc.
    """
    if typed in options:
        return typed
    matched = [c for c in options if c.startswith(typed)]
    if len(matched) == 1:
        return matched[0]
    if len(matched) > 1:
        raise SystemExit(
            f"fam: {what} {typed!r} chưa rõ — khớp với {', '.join(sorted(matched))}. "
            "Gõ thêm vài chữ cho rõ."
        )
    return typed


def _expand_abbrev(argv: list[str], command: list[str]) -> list[str]:
    """Mở rộng từ viết tắt ở vị trí TÊN LỆNH, và ở tham số dạng danh sách."""
    position = next((i for i, t in enumerate(argv) if not t.startswith("-")), None)
    if position is None:
        return argv

    argv = list(argv)
    argv[position] = explain(argv[position], command, "lệnh")

    next_delay = next(
        (i for i in range(position + 1, len(argv)) if not argv[i].startswith("-")), None
    )
    if next_delay is None:
        return argv

    if argv[position] in ("install", "env"):
        from fastapi_modular.cli.configure_env import BLOCKS
        from fastapi_modular.cli.install import COMPONENTS

        chosen = COMPONENTS if argv[position] == "install" else sorted(BLOCKS)
        argv[next_delay] = explain(argv[next_delay], chosen, "thành phần")
    elif argv[position] == "migrate":
        argv[next_delay] = explain(
            argv[next_delay], ["up", "down", "history", "sql", "create"], "việc"
        )
    return argv


def main(argv: list[str] | None = None) -> int:
    """Điểm vào thật. Bọc `_main` để `fam info | head` không văng traceback."""
    try:
        return _main(argv)
    except BrokenPipeError:
        # Người ta nối vào `head`/`less` rồi thoát sớm: đó là chuyện bình
        # thường, không phải lỗi. Trỏ stdout vào /dev/null để lúc Python dọn
        # dẹp không cố ghi thêm lần nữa.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    except KeyboardInterrupt:
        return 130


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fam",
        description="FastAPI theo kiến trúc module kiểu NestJS (gõ đầy đủ: fastapi-modular)",
    )
    parser.add_argument("--version", action="version", version=f"fastapi-modular {__version__}")
    command = parser.add_subparsers(dest="command", required=True)

    p_init = command.add_parser(
        "init", help="dựng dự án ngay trong thư mục hiện tại (không tạo thêm cấp)"
    )
    p_init.add_argument("--name", help="tên dự án; mặc định lấy theo tên thư mục")
    p_init.add_argument("--root", type=Path, default=Path("."), help="thư mục đích")

    p_new = command.add_parser("new", help="dựng dự án trong một thư mục MỚI")
    p_new.add_argument("name", help="tên thư mục dự án")
    p_new.add_argument("--root", type=Path, default=Path("."), help="tạo ở đâu (mặc định: .)")

    p_mod = command.add_parser("module", help="sinh module nghiệp vụ")
    p_mod.add_argument("name", help="tên module, dạng số nhiều viết thường: alerts")
    p_mod.add_argument("--entity", help="tên entity dạng số ít; mặc định đoán từ tên module")
    p_mod.add_argument(
        "--root", type=Path, default=Path("src/api"), help="thư mục chứa các module"
    )
    p_mod.add_argument("--gateway", action="store_true", help="tạo kèm gateway WebSocket")
    p_mod.add_argument("--gateway-only", action="store_true")
    p_mod.add_argument("--consumer", action="store_true", help="tạo kèm consumer RabbitMQ")
    p_mod.add_argument("--consumer-only", action="store_true")

    p_prov = command.add_parser("provider", help="sinh provider cắm được (họ + năng lực)")
    p_prov.add_argument("family", help="tên họ: payment, sms, device")
    p_prov.add_argument("name", help="tên provider: vnpay, viettel, dahua")
    p_prov.add_argument(
        "--root", type=Path, default=Path("src/providers"), help="thư mục chứa các họ"
    )

    p_dev = command.add_parser("dev", help="chạy kèm autoreload")
    p_run = command.add_parser("run", help="chạy chế độ production, nhiều worker")
    for p_serve in (p_dev, p_run):
        p_serve.add_argument("--app", default="src.main:app", help="điểm vào ASGI")
        p_serve.add_argument("--host", help="mặc định lấy APP_HOST")
        p_serve.add_argument("--port", type=int, help="mặc định lấy APP_PORT")
    p_run.add_argument("--workers", type=int, default=4)

    command.add_parser("info", help="cấu hình đang dùng và thư viện đã cài")

    p_test = command.add_parser("test", help="chạy pytest")
    p_test.add_argument("them", nargs="*", help="tham số truyền thẳng cho pytest")

    p_lint = command.add_parser("lint", help="soi lỗi tĩnh bằng ruff")
    p_lint.add_argument("--fix", action="store_true", help="tự sửa những lỗi sửa được")
    p_lint.add_argument("duong_dan", nargs="*", default=["src"], help="mặc định: src")

    p_mig = command.add_parser("migrate", help="chạy migration (Alembic)")
    p_mig.add_argument(
        "viec",
        nargs="?",
        default="up",
        choices=["up", "down", "history", "sql", "create"],
        help="up (mặc định) | down | history | sql | create",
    )
    p_mig.add_argument("-m", "--message", help="mô tả, dùng với `create`")

    from fastapi_modular.cli.install import COMPONENTS

    p_ins = command.add_parser(
        "install", help="cài thư viện của một thành phần rồi ghi biến vào .env"
    )
    p_ins.add_argument("thanh_phan", choices=COMPONENTS, metavar="thành-phần",
                       help=" | ".join(COMPONENTS))
    p_ins.add_argument("--no-env", action="store_true", help="chỉ cài, đừng đụng .env")
    p_ins.add_argument("--file", type=Path, default=Path(".env"))

    command.add_parser("clean", help="xoá cache và bản dựng (không đụng dữ liệu)")

    p_build = command.add_parser("build", help="dựng wheel + sdist vào dist/")
    p_build.add_argument("--no-clean", action="store_true", help="giữ dist/ cũ")

    p_pub = command.add_parser("publish", help="đẩy gói lên PyPI")
    p_pub.add_argument("--test", action="store_true", help="đẩy lên TestPyPI trước")

    p_env = command.add_parser("env", help="ghi biến cấu hình của một thành phần vào .env")
    p_env.add_argument("thanh_phan", help="sqlite | postgres | mongodb | redis | "
                                          "rabbitmq | mqtt | kafka | ws-redis")
    p_env.add_argument("--file", type=Path, default=Path(".env"))

    args = parser.parse_args(
        _expand_abbrev(list(argv if argv is not None else sys.argv[1:]), list(command.choices))
    )

    # Import muộn: `fastapi-modular new` không cần kéo theo bộ sinh module, và
    # `--version` thì không cần kéo theo gì cả.
    if args.command == "init":
        from fastapi_modular.cli.new_project import init_project

        return init_project(args.root, args.name)

    if args.command == "new":
        from fastapi_modular.cli.new_project import create_project

        return create_project(args.name, args.root)

    if args.command == "module":
        from fastapi_modular.cli.new_module import main as sinh_module

        argv2 = [args.name, "--root", str(args.root)]
        if args.entity:
            argv2 += ["--entity", args.entity]
        for has, existing_names in (
            (args.gateway, "--gateway"),
            (args.gateway_only, "--gateway-only"),
            (args.consumer, "--consumer"),
            (args.consumer_only, "--consumer-only"),
        ):
            if has:
                argv2.append(existing_names)
        return sinh_module(argv2)

    if args.command == "provider":
        from fastapi_modular.cli.new_provider import main as sinh_provider

        return sinh_provider([args.family, args.name, "--root", str(args.root)])

    if args.command in ("dev", "run"):
        from fastapi_modular.cli.serve import serve

        return serve(
            target=args.app,
            reload=args.command == "dev",
            workers=getattr(args, "workers", None),
            host=args.host,
            port=args.port,
        )

    if args.command == "info":
        from fastapi_modular.cli.info import info

        return info()

    if args.command == "install":
        from fastapi_modular.cli.install import install

        return install(args.thanh_phan, write_env=not args.no_env, env_file=args.file)

    if args.command == "clean":
        from fastapi_modular.cli.clean import clean

        return clean()

    if args.command in ("test", "lint", "migrate", "build", "publish"):
        from fastapi_modular.cli.tools import run_tool

        return run_tool(args)

    from fastapi_modular.cli.configure_env import main as write_env

    return write_env(args.thanh_phan, args.file)


if __name__ == "__main__":
    raise SystemExit(main())
