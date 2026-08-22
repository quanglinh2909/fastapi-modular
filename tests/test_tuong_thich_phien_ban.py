"""Khung phải cư xử GIỐNG NHAU trên mọi phiên bản Python được hỗ trợ (3.10+).

Bẫy lớn nhất: `asyncio.wait_for` ném `asyncio.TimeoutError`, mà từ 3.11 lớp đó
CHÍNH LÀ `TimeoutError` dựng sẵn, còn 3.10 thì là một lớp khác hẳn. Bắt bằng
`except TimeoutError` trần sẽ trúng trên 3.11 và TRƯỢT trên 3.10 — một lỗi chỉ
lộ ra ở máy người dùng.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from pymodular.core.compat import UTC, StrEnum, TimeoutErrors
from pymodular.infrastructure.database.base import is_transient_error


def test_bat_duoc_timeout_cua_wait_for_o_moi_phien_ban():
    async def treo() -> None:
        await asyncio.sleep(10)

    async def chay() -> str:
        try:
            await asyncio.wait_for(treo(), 0.01)
        except TimeoutErrors:
            return "bắt được"
        return "trượt"

    assert asyncio.run(chay()) == "bắt được"


def test_timeout_tinh_la_loi_tam_thoi():
    """Nếu không thì mạch ngắt không bao giờ mở ra vì database chậm."""
    assert is_transient_error(asyncio.TimeoutError()) is True
    assert is_transient_error(TimeoutError()) is True
    assert is_transient_error(ValueError("sai dữ liệu")) is False


def test_strenum_in_ra_dung_gia_tri():
    class Mau(StrEnum):
        DO = "do"

    assert Mau.DO == "do"
    assert str(Mau.DO) == "do", "Enum thường sẽ in ra 'Mau.DO' — khác nhau lúc ghi log"
    assert f"{Mau.DO}" == "do"


def test_utc_la_mui_gio_that():
    from datetime import datetime

    assert datetime(2026, 1, 1, tzinfo=UTC).tzinfo is UTC
    assert datetime.now(UTC).utcoffset().total_seconds() == 0


def test_khong_dung_tinh_nang_chi_co_tu_311():
    """Quét nguồn: `asyncio.timeout` và `enum.StrEnum` nhập thẳng sẽ vỡ trên 3.10."""
    goc = Path(__file__).resolve().parent.parent / "pymodular"
    pham: list[str] = []
    for file in goc.rglob("*.py"):
        if file.name == "compat.py":
            continue                       # chỗ duy nhất được phép biết khác biệt
        noi_dung = file.read_text(encoding="utf-8")
        for cam in ("asyncio.timeout(", "from enum import StrEnum", "from datetime import UTC"):
            if cam in noi_dung:
                pham.append(f"{file.relative_to(goc.parent)}: {cam}")
    assert not pham, "dùng tính năng 3.11+ ngoài compat.py:\n  " + "\n  ".join(pham)


@pytest.mark.skipif(sys.version_info >= (3, 11), reason="chỉ có ý nghĩa trên 3.10")
def test_tren_310_hai_lop_timeout_that_su_khac_nhau():
    """Chứng minh bẫy là có thật, không phải lo xa."""
    assert asyncio.TimeoutError is not TimeoutError
