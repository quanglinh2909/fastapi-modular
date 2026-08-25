"""Test sổ đăng ký provider: đăng ký, năng lực, DI, và quét thư mục.

Không cần hạ tầng gì — provider ở đây là class thuần.
"""

from __future__ import annotations

import sys
import textwrap
from abc import ABC, abstractmethod
from pathlib import Path

import pytest

from fastapi_modular.core.config import DatabaseSettings, Settings
from fastapi_modular.core.container import Scope, container, injectable, request_scope
from fastapi_modular.core.providers import (
    CapabilityNotSupportedError,
    ProviderFamily,
    ProviderNotFoundError,
    capabilities_of,
    provider,
    register_providers,
)


# ----------------------------------------------------------------- năng lực
class PaymentGateway(ABC):
    @abstractmethod
    async def tao_giao_dich(self, so_tien: int) -> str: ...


class HoanTien(ABC):
    @abstractmethod
    async def hoan_tien(self, ma: str) -> bool: ...


@provider("vnpay")
class VNPayProvider(PaymentGateway, HoanTien):
    async def tao_giao_dich(self, so_tien: int) -> str:
        return f"vnpay:{so_tien}"

    async def hoan_tien(self, ma: str) -> bool:
        return True


@provider("momo")
class MomoProvider(PaymentGateway):
    """Không hiện thực HoanTien — cố ý, để test lỗi 501."""

    async def tao_giao_dich(self, so_tien: int) -> str:
        return f"momo:{so_tien}"


class PaymentProviders(ProviderFamily[PaymentGateway], family="payment"):
    pass


@pytest.fixture
def so() -> PaymentProviders:
    r = PaymentProviders()
    r.add("vnpay", VNPayProvider)
    r.add("momo", MomoProvider)
    return r


# --------------------------------------------------------------- tra cứu
def test_require_tra_ve_dung_provider(so: PaymentProviders):
    assert isinstance(so.require("vnpay", PaymentGateway), VNPayProvider)
    assert isinstance(so.require("momo", PaymentGateway), MomoProvider)


def test_ten_khong_co_thi_404_va_liet_ke_cai_dang_co(so: PaymentProviders):
    with pytest.raises(ProviderNotFoundError) as loi:
        so.get_class("zalopay")
    assert loi.value.status_code == 404
    # Thông báo phải chỉ ra đang có gì, nếu không người dùng phải đi mò.
    assert "momo" in str(loi.value) and "vnpay" in str(loi.value)


def test_thieu_nang_luc_thi_501_chu_khong_phai_500(so: PaymentProviders):
    """Momo có thật, chỉ là không hoàn tiền được — đó không phải bug của server."""
    with pytest.raises(CapabilityNotSupportedError) as loi:
        so.require("momo", HoanTien)
    assert loi.value.status_code == 501
    assert "HoanTien" in str(loi.value)
    # Nói luôn nó làm được gì, để người đọc biết phải đổi sang đâu.
    assert "PaymentGateway" in str(loi.value)


def test_supports_va_describe(so: PaymentProviders):
    assert so.supports("vnpay", HoanTien) is True
    assert so.supports("momo", HoanTien) is False
    assert so.names() == ["momo", "vnpay"]
    assert so.describe() == [
        {"name": "momo", "capabilities": ["PaymentGateway"]},
        {"name": "vnpay", "capabilities": ["HoanTien", "PaymentGateway"]},
    ]


def test_hai_provider_trung_ten_bi_chan():
    r = PaymentProviders()
    r.add("x", VNPayProvider)
    r.add("x", VNPayProvider)  # cùng một class: vô hại
    with pytest.raises(RuntimeError, match="hai provider cùng tên"):
        r.add("x", MomoProvider)


def test_ten_provider_khong_hop_le_bi_chan_ngay():
    with pytest.raises(ValueError, match="không hợp lệ"):
        provider("VNPay Hoa Toc")


# ------------------------------------------------------ nhận diện năng lực
def test_nang_luc_nhan_dien_duoc_du_interface_nam_o_dau():
    """Lọc theo BẢN CHẤT (là ABC) chứ không theo tiền tố module.

    Lọc theo tên module là lỗi của bản registry cũ: interface đặt ngoài đúng
    một thư mục quy ước thì capabilities() im lặng trả rỗng.
    """
    assert capabilities_of(VNPayProvider) == ["HoanTien", "PaymentGateway"]
    # ABC và object không được tính là năng lực.
    assert "ABC" not in capabilities_of(VNPayProvider)
    assert "object" not in capabilities_of(VNPayProvider)


def test_class_khong_ke_thua_gi_thi_khong_co_nang_luc():
    @injectable
    class Tran:
        pass

    assert capabilities_of(Tran) == []


# ----------------------------------------------------------------- DI
class CoPhuThuoc(ABC):
    @abstractmethod
    def ten_app(self) -> str: ...


class CfgProviders(ProviderFamily[CoPhuThuoc], family="cfg"):
    pass


@provider("can-settings")
class ProviderCanSettings(CoPhuThuoc):
    """Provider có phụ thuộc — bản registry cũ dựng bằng `cls()` nên chết ở đây."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def ten_app(self) -> str:
        return self._settings.name


def test_provider_nhan_duoc_phu_thuoc_qua_container():
    container.override(Settings, Settings(APP_NAME="thu-nghiem", APP_DB=DatabaseSettings()))
    r = CfgProviders()
    r.add("can-settings", ProviderCanSettings)
    assert r.require("can-settings", CoPhuThuoc).ten_app() == "thu-nghiem"


def test_provider_la_singleton_theo_mac_dinh(so: PaymentProviders):
    assert so.get("vnpay") is so.get("vnpay")


@provider("theo-request", scope=Scope.REQUEST)
class ProviderTheoRequest(CoPhuThuoc):
    def ten_app(self) -> str:
        return "moi-request-mot-cai"


@pytest.mark.asyncio
async def test_scope_request_duoc_ton_trong():
    r = CfgProviders()
    r.add("theo-request", ProviderTheoRequest)
    async with request_scope():
        a, b = r.get("theo-request"), r.get("theo-request")
        assert a is b
    async with request_scope():
        assert r.get("theo-request") is not a


# ------------------------------------------------------- quét thư mục thật
def _du_an_mau(root: Path) -> None:
    """Dựng một cây src/providers/ thật trên đĩa để test register_providers."""
    ho = root / "providers_thu" / "sms"
    ho.mkdir(parents=True)
    (root / "providers_thu" / "__init__.py").write_text("", encoding="utf-8")
    (ho / "__init__.py").write_text(
        textwrap.dedent(
            """
            from fastapi_modular import ProviderFamily
            from providers_thu.sms.capabilities import GuiSms

            class SmsProviders(ProviderFamily[GuiSms], family="sms"):
                pass
            """
        ),
        encoding="utf-8",
    )
    (ho / "capabilities.py").write_text(
        textwrap.dedent(
            """
            from abc import ABC, abstractmethod

            class GuiSms(ABC):
                @abstractmethod
                async def gui(self, so: str, noi_dung: str) -> bool: ...
            """
        ),
        encoding="utf-8",
    )
    (ho / "viettel.py").write_text(
        textwrap.dedent(
            """
            from fastapi_modular import provider
            from providers_thu.sms.capabilities import GuiSms

            @provider("viettel")
            class ViettelSms(GuiSms):
                async def gui(self, so, noi_dung): return True
            """
        ),
        encoding="utf-8",
    )
    # File bắt đầu bằng _ phải bị bỏ qua.
    (ho / "_nhap.py").write_text("raise AssertionError('không được import file _')", encoding="utf-8")


def test_register_providers_quet_dung_va_cam_vao_container(tmp_path: Path):
    _du_an_mau(tmp_path)
    sys.path.insert(0, str(tmp_path))
    try:
        so = register_providers("providers_thu")
        assert sorted(so) == ["sms"]
        assert so["sms"].names() == ["viettel"]
        assert so["sms"].describe() == [{"name": "viettel", "capabilities": ["GuiSms"]}]

        # Đúng đường đi mà service dùng: resolve theo lớp token của họ.
        import providers_thu.sms as goi_sms

        assert container.resolve(goi_sms.SmsProviders) is so["sms"]
    finally:
        sys.path.remove(str(tmp_path))
        for ten in [m for m in sys.modules if m.startswith("providers_thu")]:
            del sys.modules[ten]


def test_khong_co_thu_muc_providers_thi_im_lang_bo_qua():
    assert register_providers("khong_he_ton_tai_dau_nhe") == {}


# ------------------------------------------------- nhận qua DI trong service
def test_service_nhan_duoc_so_qua_token_lop():
    """Đúng cách người dùng viết: `def __init__(self, payments: PaymentProviders)`."""
    r = PaymentProviders()
    r.add("vnpay", VNPayProvider)
    container.override(PaymentProviders, r)

    @injectable
    class DonHangService:
        def __init__(self, payments: PaymentProviders) -> None:
            self._payments = payments

        def cong(self, ten: str):
            return self._payments.require(ten, PaymentGateway)

    dich_vu = container.resolve(DonHangService)
    assert isinstance(dich_vu.cong("vnpay"), VNPayProvider)


def test_token_thieu_ten_ho_bi_chan_ngay_luc_dinh_nghia():
    with pytest.raises(TypeError, match="thiếu tên họ"):

        class Thieu(ProviderFamily[str]):
            pass


def test_ho_chua_co_token_thi_bao_ro_phai_them_gi(tmp_path: Path):
    """Quên khai lớp token thì phải nói rõ phải thêm gì, không để lặng lẽ hỏng."""
    _du_an_mau(tmp_path)
    # Xoá lớp token đi để mô phỏng người dùng quên khai.
    (tmp_path / "providers_thu" / "sms" / "__init__.py").write_text("", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        with pytest.raises(RuntimeError, match="chưa có token DI"):
            register_providers("providers_thu")
    finally:
        sys.path.remove(str(tmp_path))
        for ten in [m for m in sys.modules if m.startswith("providers_thu")]:
            del sys.modules[ten]


# ------------------------------------------------- hai họ độc lập với nhau
def test_hai_ho_dung_chung_mot_ten_class_van_song_hoa_binh():
    """`OryzaProvider` bên thiết bị và bên thông báo là HAI thứ khác nhau.

    Đây đúng là lý do khái niệm "họ" tồn tại. Bản đầu tôi cho @provider gọi
    @injectable nên chúng đụng nhau ở sổ toàn cục tra theo tên class.
    """

    class GuiTin(ABC):
        @abstractmethod
        def gui(self) -> str: ...

    class DeviceProviders(ProviderFamily[GuiTin], family="device"):
        pass

    class NotifProviders(ProviderFamily[GuiTin], family="notification"):
        pass

    ho_a, ho_b = DeviceProviders(), NotifProviders()

    @provider("oryza")
    class OryzaProvider(GuiTin):
        def gui(self) -> str:
            return "thiet-bi"

    ho_a.add("oryza", OryzaProvider)

    @provider("oryza")
    class OryzaProvider(GuiTin):
        def gui(self) -> str:
            return "thong-bao"

    ho_b.add("oryza", OryzaProvider)

    assert ho_a.require("oryza", GuiTin).gui() == "thiet-bi"
    assert ho_b.require("oryza", GuiTin).gui() == "thong-bao"


def test_provider_khong_lam_ban_so_dang_ky_toan_cuc():
    """@provider KHÔNG được đẩy class vào _REGISTRY — đó là sổ tra theo tên."""
    from fastapi_modular.core.container import _REGISTRY

    assert "VNPayProvider" not in _REGISTRY


# --------------------------------------------------------- lệnh fam provider
def test_fam_provider_sinh_ho_moi_roi_them_provider(tmp_path: Path, monkeypatch):
    from fastapi_modular.cli.new_provider import main as sinh

    monkeypatch.chdir(tmp_path)
    goc = tmp_path / "src" / "providers"

    assert sinh(["payment", "vnpay", "--root", str(goc)]) == 0
    ho = goc / "payment"
    assert (ho / "__init__.py").exists()
    assert (ho / "capabilities.py").exists()
    assert (ho / "vnpay.py").exists()

    # Token DI mang tên suy ra từ tên họ.
    assert 'class PaymentProviders(ProviderFamily[PaymentBasic], family="payment")' in (
        ho / "__init__.py"
    ).read_text(encoding="utf-8")

    # Provider kế thừa năng lực mẫu và có stub.
    vnpay = (ho / "vnpay.py").read_text(encoding="utf-8")
    assert "@provider(\"vnpay\")" in vnpay
    assert "class VnpayPayment(PaymentBasic)" in vnpay
    assert "async def ping(self) -> bool:" in vnpay

    # Thêm một năng lực rồi sinh provider thứ hai: phải bắt được năng lực mới.
    caps = ho / "capabilities.py"
    caps.write_text(
        caps.read_text(encoding="utf-8")
        + "\n\nclass HoanTien(ABC):\n"
        "    @abstractmethod\n"
        "    async def hoan_tien(self, ma: str) -> bool: ...\n",
        encoding="utf-8",
    )
    assert sinh(["payment", "momo", "--root", str(goc)]) == 0
    momo = (ho / "momo.py").read_text(encoding="utf-8")
    assert "class MomoPayment(PaymentBasic, HoanTien)" in momo
    assert "async def hoan_tien(self, ma: str) -> bool:" in momo


def test_fam_provider_khong_ghi_de_file_da_co(tmp_path: Path):
    from fastapi_modular.cli.new_provider import main as sinh

    goc = tmp_path / "providers"
    assert sinh(["sms", "viettel", "--root", str(goc)]) == 0
    assert sinh(["sms", "viettel", "--root", str(goc)]) == 1  # lần hai bị chặn


def test_fam_provider_chan_ten_khong_hop_le(tmp_path: Path):
    from fastapi_modular.cli.new_provider import main as sinh

    # "Payment" được hạ chữ thường thành "payment" — cố ý, cho dễ gõ.
    assert sinh(["payment", "VN Pay", "--root", str(tmp_path)]) == 1
    assert sinh(["1payment", "vnpay", "--root", str(tmp_path)]) == 1


# ------------------------------------------- năng lực chính khai ở lớp token
def test_require_mot_tham_so_dung_nang_luc_chinh(so: PaymentProviders):
    """`ProviderFamily[PaymentGateway]` khai năng lực chính, nên require() gọn."""
    assert PaymentProviders.__capability__ is PaymentGateway
    assert isinstance(so.require("vnpay"), VNPayProvider)
    assert isinstance(so.require("momo"), MomoProvider)


def test_nang_luc_tuy_chon_van_khai_tuong_minh(so: PaymentProviders):
    assert isinstance(so.require("vnpay", HoanTien), VNPayProvider)
    with pytest.raises(CapabilityNotSupportedError):
        so.require("momo", HoanTien)


def test_supports_cung_dung_nang_luc_chinh(so: PaymentProviders):
    assert so.supports("momo") is True
    assert so.supports("momo", HoanTien) is False


def test_ho_khong_khai_nang_luc_thi_require_doi_tham_so_thu_hai():
    """Không khai `ProviderFamily[...]` thì phải nói rõ, đừng đoán bừa."""

    class KhongKhai(ProviderFamily, family="mo-ho"):
        pass

    r = KhongKhai()
    r.add("vnpay", VNPayProvider)
    assert KhongKhai.__capability__ is None
    with pytest.raises(TypeError, match="chưa khai năng lực chính"):
        r.require("vnpay")
    # truyền tay thì vẫn chạy
    assert isinstance(r.require("vnpay", PaymentGateway), VNPayProvider)


def test_quen_goi_register_providers_thi_bao_dung_nguyen_nhan():
    """Lỗi hay gặp nhất: main.py lắp tay mà quên register_providers().

    Câu "thiếu @injectable" mặc định dẫn người đọc đi sai hướng — token của họ
    provider không bao giờ nằm trong _REGISTRY.
    """

    class ChuaDungSo(ProviderFamily[PaymentGateway], family="chua-dung"):
        pass

    with pytest.raises(RuntimeError) as loi:
        container.resolve(ChuaDungSo)
    assert "register_providers()" in str(loi.value)
    assert "@injectable" not in str(loi.value)


def test_require_voi_nang_luc_tuy_chon_tra_ve_dung_doi_tuong(so: PaymentProviders):
    """Overload chỉ ảnh hưởng KIỂU TĨNH; lúc chạy vẫn phải trả đúng provider.

    Kiểu tĩnh đã kiểm riêng bằng mypy: require(ten) -> năng lực chính,
    require(ten, HoanTien) -> HoanTien.
    """
    doi_tuong = so.require("vnpay", HoanTien)
    assert isinstance(doi_tuong, VNPayProvider)
    assert isinstance(doi_tuong, HoanTien)
