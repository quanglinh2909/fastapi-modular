"""Test bộ đọc biểu thức cron.

`tests/fixtures/cron_vectors.json` sinh bằng `croniter` ở vùng hai bên chắc
chắn đồng ý (không có dạng `N-N`, không có `*/n` ở trường ngày và thứ). Hai
vùng bị loại có test tay riêng bên dưới, kèm lý do.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from fastapi_modular.core.cron import parse_cron
from fastapi_modular.core.exceptions import BadRequestError

VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "cron_vectors.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("expression,base,expected", VECTORS, ids=range(len(VECTORS)))
def test_khop_croniter(expression, base, expected):
    cron = parse_cron(expression)
    moment = datetime.fromisoformat(base)
    got = []
    for _ in range(len(expected)):
        moment = cron.next_after(moment)
        got.append(moment.isoformat())
    assert got == expected


# ------------------------------------------------------------ luật ngày/thứ
def test_ngay_va_thu_noi_bang_HOAC_khi_khong_truong_nao_co_sao():
    """`0 0 1 * 1` = ngày 1 hàng tháng HOẶC mọi thứ Hai.

    Phản trực giác nhưng đúng luật cron gốc. Hiểu thành VÀ thì lịch gần như
    không bao giờ chạy.
    """
    cron = parse_cron("0 0 1 * 1")
    got = []
    moment = datetime(2026, 8, 25)
    for _ in range(3):
        moment = cron.next_after(moment)
        got.append(moment.strftime("%Y-%m-%d %a"))
    assert got == ["2026-08-31 Mon", "2026-09-01 Tue", "2026-09-07 Mon"]


def test_co_sao_o_dau_truong_thi_noi_bang_VA():
    """`*/7` vẫn tính là "có sao" — xét đúng KÝ TỰ ĐẦU như cron gốc (Vixie).

    Đây là chỗ `croniter` làm khác: nó dùng phép so `!= "*"`, nên với
    `0 0 */7 * 1` nó nối bằng HOẶC còn cron gốc nối bằng VÀ.
    """
    cron = parse_cron("0 0 */7 * 1")          # ngày 1,8,15,22,29 VÀ thứ Hai
    moment = cron.next_after(datetime(2026, 8, 25))
    assert moment.day in {1, 8, 15, 22, 29}
    assert moment.strftime("%a") == "Mon"


def test_sao_o_mot_trong_hai_truong_thi_truong_kia_quyet_dinh():
    assert parse_cron("0 0 1 * *").next_after(datetime(2026, 8, 25)).day == 1
    assert parse_cron("0 0 * * 1").next_after(datetime(2026, 8, 25)).strftime("%a") == "Mon"


def test_khoang_mot_phan_tu_la_dung_mot_gia_tri():
    """`31-31` là {31}, không phải `*`. POSIX nói vậy; croniter hiểu khác."""
    assert parse_cron("31-31 * * * *").minutes == frozenset({31})
    assert parse_cron("31 * * * *").minutes == parse_cron("31-31 * * * *").minutes


def test_chu_nhat_viet_duoc_bang_0_lan_7():
    assert parse_cron("0 0 * * 0").weekdays == parse_cron("0 0 * * 7").weekdays


def test_thu_cua_python_lech_mot_nhip_so_voi_cron():
    """Python: thứ Hai = 0. Cron: Chủ nhật = 0. Lệch là mọi lịch chạy sai ngày."""
    assert parse_cron("0 0 * * 1").next_after(datetime(2026, 8, 25)).strftime("%a") == "Mon"
    assert parse_cron("0 0 * * 0").next_after(datetime(2026, 8, 25)).strftime("%a") == "Sun"


# ------------------------------------------------------------------ lối tắt
@pytest.mark.parametrize(
    "shortcut,equivalent",
    [("@hourly", "0 * * * *"), ("@daily", "0 0 * * *"), ("@weekly", "0 0 * * 0"),
     ("@monthly", "0 0 1 * *"), ("@yearly", "0 0 1 1 *")],
)
def test_loi_tat(shortcut, equivalent):
    base = datetime(2026, 5, 17, 13, 22)
    assert parse_cron(shortcut).next_after(base) == parse_cron(equivalent).next_after(base)


# ------------------------------------------------------------------- năm nhuận
def test_29_thang_2_chi_roi_vao_nam_nhuan():
    cron = parse_cron("0 0 29 2 *")
    moment = datetime(2026, 1, 1)
    got = []
    for _ in range(3):
        moment = cron.next_after(moment)
        got.append(moment.year)
    assert got == [2028, 2032, 2036]


def test_ngay_khong_bao_gio_ton_tai_bi_chan_thay_vi_treo():
    """`0 0 30 2 *` hợp lệ về cú pháp nhưng không bao giờ xảy ra.

    Không chặn thì vòng dò chạy mãi và treo cả tiến trình LÚC KHỞI ĐỘNG.
    """
    with pytest.raises(BadRequestError, match="không có lần chạy nào"):
        parse_cron("0 0 30 2 *").next_after(datetime(2026, 1, 1))


# ------------------------------------------------------------------ khuôn sai
@pytest.mark.parametrize(
    "bad",
    ["", "   ", "* * * *", "* * * * * *", "60 * * * *", "* 24 * * *", "* * 0 * *",
     "* * * 13 *", "* * * * 8", "a * * * *", "*/0 * * * *", "*/x * * * *", "5-1 * * * *"],
)
def test_bieu_thuc_sai_bi_tu_choi_kem_cho_sai(bad):
    with pytest.raises(BadRequestError):
        parse_cron(bad)


def test_loi_noi_ro_truong_nao_sai():
    with pytest.raises(BadRequestError, match="phút"):
        parse_cron("99 * * * *")
    with pytest.raises(BadRequestError, match="tháng"):
        parse_cron("0 0 * 99 *")
