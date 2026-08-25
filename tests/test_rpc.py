"""Test phần dùng chung của `emit`/`send` — khuôn tin tương thích NestJS.

Không cần hạ tầng nào: đây là chỗ kiểm khuôn tin và sổ chờ. Phần chạy qua
broker thật nằm ở tests/test_rabbitmq.py.

`tests/fixtures/nestjs_patterns.json` là dữ liệu do CHÍNH NestJS sinh ra
(`transformPatternToRoute` của @nestjs/microservices 11.2.1), không phải do tôi
gõ tay. Cách sinh lại nằm trong docs/rpc.md.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from fastapi_modular.core.rpc import (
    NO_MESSAGE_HANDLER,
    PendingReplies,
    RpcRemoteError,
    RpcTimeoutError,
    decode,
    encode,
    error_packet,
    event_packet,
    normalize_pattern,
    ok_packet,
    read_packet,
    read_reply,
    request_packet,
)

VECTOR = json.loads(
    (Path(__file__).parent / "fixtures" / "nestjs_patterns.json").read_text(encoding="utf-8")
)


# ------------------------------------------------------- chuẩn hoá pattern
@pytest.mark.parametrize("pattern,mong_doi", VECTOR, ids=range(len(VECTOR)))
def test_chuan_hoa_pattern_khop_nestjs(pattern, mong_doi):
    """Khớp tới từng ký tự với hàm thật của NestJS.

    Lệch một ký tự là bên kia không tìm thấy handler, và KHÔNG có lỗi nào được
    ném ra để mà lần — chỉ là lời gọi treo tới hết giờ.
    """
    assert normalize_pattern(pattern) == mong_doi


def test_khoa_sap_theo_localecompare_chu_khong_theo_ma_ky_tu():
    """Cái bẫy đắt nhất: NestJS sắp khoá bằng `localeCompare`.

    So mã ký tự xếp "M" (77) trước "a" (97). `localeCompare` xếp ngược lại.
    """
    assert normalize_pattern({"z": 1, "a": 2, "M": 3}) == '{"a":2,"M":3,"z":1}'
    assert sorted({"z": 1, "a": 2, "M": 3}) == ["M", "a", "z"], "sorted() thường thì khác"


def test_chuan_hoa_theo_TANG_chu_khong_theo_tung_ky_tu():
    """ICU so hết tầng chữ rồi mới tới tầng hoa/thường.

    "Ab" và "aC": so từng ký tự thì "aC" trước (a < A), so theo tầng thì "Ab"
    trước (b < c). NestJS theo tầng.
    """
    assert normalize_pattern({"Ab": 1, "aC": 2}) == '{"Ab":1,"aC":2}'


def test_pattern_qua_sau_hoac_qua_nhieu_khoa_bi_cat_dung_nhu_nestjs():
    sau = cur = {}
    for _ in range(8):
        cur["x"] = {}
        cur = cur["x"]
    assert "[MAX_DEPTH_REACHED]" in normalize_pattern(sau)
    assert normalize_pattern({f"k{i}": i for i in range(30)}) == "[TOO_MANY_KEYS]"


# ------------------------------------------------------------------ gói tin
def test_send_co_id_con_emit_thi_khong():
    """Chính `id` phân biệt hai loại — NestJS quyết định dựa vào đúng chỗ này."""
    yeu_cau = request_packet("sum", [1, 2], "abc")
    assert yeu_cau == {"pattern": "sum", "data": [1, 2], "id": "abc"}
    su_kien = event_packet("sum", [1, 2])
    assert su_kien == {"pattern": "sum", "data": [1, 2]}
    assert "id" not in su_kien


def test_doc_goi_nestjs():
    assert read_packet({"pattern": "a", "data": 1, "id": "x"}) == ("a", 1, "x")
    assert read_packet({"pattern": "a", "data": 1}) == ("a", 1, None)
    # pattern dạng object được chuẩn hoá ngay lúc đọc, để tra bảng cho khớp
    assert read_packet({"pattern": {"cmd": "x"}, "data": None})[0] == '{"cmd":"x"}'


@pytest.mark.parametrize(
    "raw",
    [
        {"pattern": "a"},                            # thiếu data
        {"data": 1},                                 # thiếu pattern
        {"pattern": "a", "data": 1, "thua": 2},      # thừa khoá
        {"pattern": ["a"], "data": 1},               # pattern sai kiểu
        {"pattern": "a", "data": 1, "id": 5},        # id sai kiểu
        [1, 2, 3],
        "chuỗi thuần",
        None,
    ],
)
def test_payload_thuong_khong_bi_nham_la_goi_nestjs(raw):
    """Phép thử cố ý CHẶT.

    Đoán nhầm một payload nghiệp vụ thành gói NestJS thì handler nhận đúng nửa
    dữ liệu (`data`) và mất phần còn lại, mà không có gì báo.
    """
    assert read_packet(raw) is None


def test_goi_tra_loi_gop_co_ket_thuc_vao_cung_mot_goi():
    """NestJS gộp `isDisposed` vào gói dữ liệu cuối thay vì gửi thêm gói rỗng."""
    assert ok_packet("abc", 6) == {"response": 6, "isDisposed": True, "id": "abc"}


def test_goi_loi_mang_du_ba_truong_nestjs_can():
    goi = error_packet("abc", RuntimeError("hỏng"))
    assert goi["err"] == "RuntimeError: hỏng"
    assert goi["isDisposed"] is True and goi["status"] == "error"
    assert error_packet("abc", "chuỗi thẳng")["err"] == "chuỗi thẳng"


# ---------------------------------------------------------------- mở trả lời
def test_doc_tra_loi_thanh_cong():
    assert read_reply({"response": 6, "isDisposed": True}, nguon="sum") == 6


def test_loi_dang_chuoi_va_dang_object_deu_doc_duoc():
    """NestJS khi thì gửi `err` là chuỗi, khi thì là object — gặp cả hai rồi."""
    with pytest.raises(RpcRemoteError, match="hỏng cố ý"):
        read_reply({"err": "hỏng cố ý", "isDisposed": True}, nguon="x")
    with pytest.raises(RpcRemoteError, match="Internal server error"):
        read_reply(
            {"err": {"status": "error", "message": "Internal server error"}}, nguon="x"
        )


def test_thieu_handler_dung_nguyen_van_cua_nestjs():
    """Client NestJS vốn đã biết đọc câu này — đừng dịch nó ra tiếng khác."""
    with pytest.raises(RpcRemoteError, match="no matching message handler"):
        read_reply(error_packet("x", NO_MESSAGE_HANDLER), nguon="x")


def test_dich_vu_khong_dung_khuon_nay_van_goi_duoc():
    """Trả lời không mang khoá nào của NestJS thì coi NGUYÊN gói là câu trả lời.

    NestJS cũng xử đúng vậy (`IncomingResponseDeserializer.isExternal`), nhờ đó
    gọi sang được dịch vụ không dùng NestJS lẫn khung này.
    """
    assert read_reply({"ket_qua": 9}, nguon="x") == {"ket_qua": 9}
    assert read_reply(42, nguon="x") == 42


def test_ma_hoa_giai_ma_chiu_duoc_chuoi_thuan():
    assert decode(encode({"a": 1})) == {"a": 1}
    assert decode(b"ON") == "ON", "thiết bị hay gửi chuỗi thuần chứ không phải JSON"
    assert decode(b'{"a":1}') == {"a": 1}


# ------------------------------------------------------------------ sổ chờ
async def test_giu_cho_truoc_khi_gui_nen_tra_loi_som_khong_bi_lac():
    """Bên kia có thể trả lời xong trước khi lệnh gửi của ta kịp trả về."""
    so = PendingReplies("test")
    ma, cho = so.open()
    assert len(so) == 1
    so.deliver(ma, ok_packet(ma, "sớm"))          # trả lời TRƯỚC khi ai đó wait
    assert await so.wait(ma, cho, 1.0, dich="x") == "sớm"
    assert len(so) == 0, "chờ xong phải trả chỗ"


async def test_het_gio_thi_don_cho_va_noi_ro_khong_bao_dam_gi():
    so = PendingReplies("test")
    ma, cho = so.open()
    with pytest.raises(RpcTimeoutError, match="KHÔNG bảo đảm bên kia chưa làm gì"):
        await so.wait(ma, cho, 0.05, dich="cham")
    assert len(so) == 0, "hết giờ cũng phải trả chỗ, nếu không sổ chờ phình mãi"


async def test_tra_loi_toi_muon_khong_lam_sap_gi():
    so = PendingReplies("test")
    ma, cho = so.open()
    with pytest.raises(RpcTimeoutError):
        await so.wait(ma, cho, 0.05, dich="x")
    assert so.deliver(ma, ok_packet(ma, 1)) is False, "không ai đợi nữa"


async def test_dut_ket_noi_danh_thuc_moi_nguoi_dang_doi():
    """Không có bước này thì mỗi lần rớt mạng là một loạt lời gọi đứng đủ timeout."""
    so = PendingReplies("test")
    ma, cho = so.open()
    so.fail_all("rớt mạng")
    with pytest.raises(RpcTimeoutError, match="rớt mạng"):
        await so.wait(ma, cho, 5.0, dich="x")


async def test_tat_app_thi_huy_lang_le():
    so = PendingReplies("test")
    _, cho = so.open()
    so.cancel_all()
    assert cho.cancelled()
    with pytest.raises(asyncio.CancelledError):
        await cho
