"""Điểm vào của lệnh `pymodular`, gõ tắt là `pym`.

    pym init                 dựng dự án ngay trong THƯ MỤC HIỆN TẠI
    pym new duan-cua-toi     dựng dự án trong một thư mục mới
    pym dev                  chạy kèm autoreload
    pym run --workers 4      chạy chế độ production
    pym module alerts        sinh module nghiệp vụ
    pym install postgres     cài thư viện của một thành phần + ghi .env
    pym env postgres         chỉ ghi biến cấu hình vào .env
    pym info                 đang nối vào đâu, thư viện nào đã cài
    pym migrate              chạy migration (Alembic)
    pym test / pym lint      chạy test / soi lỗi tĩnh

Hai tên gọi cùng một chương trình; `pymodular` là tên đầy đủ cho script và tài
liệu, `pym` là để gõ hằng ngày.

Lệnh và tham số dạng danh sách đều rút gọn được, miễn là KHÔNG NHẬP NHẰNG:

    pym mo alerts     = pym module alerts
    pym ins sq        = pym install sqlite
    pym d             = pym dev

`pym m` thì báo lỗi kèm gợi ý, vì cả `module` lẫn `migrate` đều bắt đầu bằng "m"
— thà hỏi lại còn hơn đoán bừa rồi chạy nhầm lệnh.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from pymodular import __version__


def giai_nghia(tu: str, lua_chon: list[str], nhan: str) -> str:
    """Đổi một từ viết tắt thành lựa chọn đầy đủ, nếu chỉ có đúng một khả năng.

    Trả về nguyên `tu` khi nó đã là lựa chọn đầy đủ hoặc không khớp gì — để
    argparse tự báo lỗi theo cách của nó. Chỉ ném lỗi khi NHẬP NHẰNG, vì đó là
    trường hợp duy nhất mà im lặng đoán bừa sẽ chạy nhầm việc.
    """
    if tu in lua_chon:
        return tu
    khop = [c for c in lua_chon if c.startswith(tu)]
    if len(khop) == 1:
        return khop[0]
    if len(khop) > 1:
        raise SystemExit(
            f"pym: {nhan} {tu!r} chưa rõ — khớp với {', '.join(sorted(khop))}. "
            "Gõ thêm vài chữ cho rõ."
        )
    return tu


def _mo_rong_vietat(argv: list[str], lenh: list[str]) -> list[str]:
    """Mở rộng từ viết tắt ở vị trí TÊN LỆNH, và ở tham số dạng danh sách."""
    vi_tri = next((i for i, t in enumerate(argv) if not t.startswith("-")), None)
    if vi_tri is None:
        return argv

    argv = list(argv)
    argv[vi_tri] = giai_nghia(argv[vi_tri], lenh, "lệnh")

    ke_tiep = next(
        (i for i in range(vi_tri + 1, len(argv)) if not argv[i].startswith("-")), None
    )
    if ke_tiep is None:
        return argv

    if argv[vi_tri] in ("install", "env"):
        from pymodular.cli.configure_env import BLOCKS
        from pymodular.cli.install import THANH_PHAN

        chon = THANH_PHAN if argv[vi_tri] == "install" else sorted(BLOCKS)
        argv[ke_tiep] = giai_nghia(argv[ke_tiep], chon, "thành phần")
    elif argv[vi_tri] == "migrate":
        argv[ke_tiep] = giai_nghia(
            argv[ke_tiep], ["up", "down", "history", "sql", "create"], "việc"
        )
    return argv


def main(argv: list[str] | None = None) -> int:
    """Điểm vào thật. Bọc `_main` để `pym info | head` không văng traceback."""
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
        prog="pym",
        description="FastAPI theo kiến trúc module kiểu NestJS (gõ đầy đủ: pymodular)",
    )
    parser.add_argument("--version", action="version", version=f"pymodular {__version__}")
    lenh = parser.add_subparsers(dest="lenh", required=True)

    p_init = lenh.add_parser(
        "init", help="dựng dự án ngay trong thư mục hiện tại (không tạo thêm cấp)"
    )
    p_init.add_argument("--name", help="tên dự án; mặc định lấy theo tên thư mục")
    p_init.add_argument("--root", type=Path, default=Path("."), help="thư mục đích")

    p_new = lenh.add_parser("new", help="dựng dự án trong một thư mục MỚI")
    p_new.add_argument("ten", help="tên thư mục dự án")
    p_new.add_argument("--root", type=Path, default=Path("."), help="tạo ở đâu (mặc định: .)")

    p_mod = lenh.add_parser("module", help="sinh module nghiệp vụ")
    p_mod.add_argument("ten", help="tên module, dạng số nhiều viết thường: alerts")
    p_mod.add_argument("--entity", help="tên entity dạng số ít; mặc định đoán từ tên module")
    p_mod.add_argument(
        "--root", type=Path, default=Path("src/api"), help="thư mục chứa các module"
    )
    p_mod.add_argument("--gateway", action="store_true", help="tạo kèm gateway WebSocket")
    p_mod.add_argument("--gateway-only", action="store_true")
    p_mod.add_argument("--consumer", action="store_true", help="tạo kèm consumer RabbitMQ")
    p_mod.add_argument("--consumer-only", action="store_true")

    p_dev = lenh.add_parser("dev", help="chạy kèm autoreload")
    p_run = lenh.add_parser("run", help="chạy chế độ production, nhiều worker")
    for p_chay in (p_dev, p_run):
        p_chay.add_argument("--app", default="src.main:app", help="điểm vào ASGI")
        p_chay.add_argument("--host", help="mặc định lấy APP_HOST")
        p_chay.add_argument("--port", type=int, help="mặc định lấy APP_PORT")
    p_run.add_argument("--workers", type=int, default=4)

    lenh.add_parser("info", help="cấu hình đang dùng và thư viện đã cài")

    p_test = lenh.add_parser("test", help="chạy pytest")
    p_test.add_argument("them", nargs="*", help="tham số truyền thẳng cho pytest")

    p_lint = lenh.add_parser("lint", help="soi lỗi tĩnh bằng ruff")
    p_lint.add_argument("--fix", action="store_true", help="tự sửa những lỗi sửa được")
    p_lint.add_argument("duong_dan", nargs="*", default=["app"], help="mặc định: app")

    p_mig = lenh.add_parser("migrate", help="chạy migration (Alembic)")
    p_mig.add_argument(
        "viec",
        nargs="?",
        default="up",
        choices=["up", "down", "history", "sql", "create"],
        help="up (mặc định) | down | history | sql | create",
    )
    p_mig.add_argument("-m", "--message", help="mô tả, dùng với `create`")

    from pymodular.cli.install import THANH_PHAN

    p_ins = lenh.add_parser(
        "install", help="cài thư viện của một thành phần rồi ghi biến vào .env"
    )
    p_ins.add_argument("thanh_phan", choices=THANH_PHAN, metavar="thành-phần",
                       help=" | ".join(THANH_PHAN))
    p_ins.add_argument("--no-env", action="store_true", help="chỉ cài, đừng đụng .env")
    p_ins.add_argument("--file", type=Path, default=Path(".env"))

    lenh.add_parser("clean", help="xoá cache và bản dựng (không đụng dữ liệu)")

    p_build = lenh.add_parser("build", help="dựng wheel + sdist vào dist/")
    p_build.add_argument("--no-clean", action="store_true", help="giữ dist/ cũ")

    p_pub = lenh.add_parser("publish", help="đẩy gói lên PyPI")
    p_pub.add_argument("--test", action="store_true", help="đẩy lên TestPyPI trước")

    p_env = lenh.add_parser("env", help="ghi biến cấu hình của một thành phần vào .env")
    p_env.add_argument("thanh_phan", help="sqlite | postgres | mongodb | redis | "
                                          "rabbitmq | mqtt | kafka | ws-redis")
    p_env.add_argument("--file", type=Path, default=Path(".env"))

    args = parser.parse_args(
        _mo_rong_vietat(list(argv if argv is not None else sys.argv[1:]), list(lenh.choices))
    )

    # Import muộn: `pymodular new` không cần kéo theo bộ sinh module, và
    # `--version` thì không cần kéo theo gì cả.
    if args.lenh == "init":
        from pymodular.cli.new_project import init_du_an

        return init_du_an(args.root, args.name)

    if args.lenh == "new":
        from pymodular.cli.new_project import tao_du_an

        return tao_du_an(args.ten, args.root)

    if args.lenh == "module":
        from pymodular.cli.new_module import main as sinh_module

        argv2 = [args.ten, "--root", str(args.root)]
        if args.entity:
            argv2 += ["--entity", args.entity]
        for co, ten_co in (
            (args.gateway, "--gateway"),
            (args.gateway_only, "--gateway-only"),
            (args.consumer, "--consumer"),
            (args.consumer_only, "--consumer-only"),
        ):
            if co:
                argv2.append(ten_co)
        return sinh_module(argv2)

    if args.lenh in ("dev", "run"):
        from pymodular.cli.serve import serve

        return serve(
            target=args.app,
            reload=args.lenh == "dev",
            workers=getattr(args, "workers", None),
            host=args.host,
            port=args.port,
        )

    if args.lenh == "info":
        from pymodular.cli.info import info

        return info()

    if args.lenh == "install":
        from pymodular.cli.install import install

        return install(args.thanh_phan, ghi_env=not args.no_env, env_file=args.file)

    if args.lenh == "clean":
        from pymodular.cli.clean import clean

        return clean()

    if args.lenh in ("test", "lint", "migrate", "build", "publish"):
        from pymodular.cli.cong_cu import chay_cong_cu

        return chay_cong_cu(args)

    from pymodular.cli.configure_env import main as ghi_env

    return ghi_env(args.thanh_phan, args.file)


if __name__ == "__main__":
    raise SystemExit(main())
