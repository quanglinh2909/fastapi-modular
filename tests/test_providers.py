"""Test provider cắm được: đăng ký, năng lực, DI, quét thư mục, và lệnh fam.

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
    ProviderNotFoundError,
    Providers,
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


_LOP = {"vnpay": VNPayProvider, "momo": MomoProvider}


@pytest.fixture
def co_ban() -> Providers[PaymentGateway]:
    return Providers("payment", PaymentGateway, dict(_LOP))


@pytest.fixture
def hoan() -> Providers[HoanTien]:
    return Providers("payment", HoanTien, dict(_LOP))


# --------------------------------------------------------------- tra cứu
def test_get_tra_ve_dung_provider(co_ban: Providers[PaymentGateway]):
    assert isinstance(co_ban.get("vnpay"), VNPayProvider)
    assert isinstance(co_ban.get("momo"), MomoProvider)


def test_ten_khong_co_thi_404_va_liet_ke_cai_dang_co(co_ban: Providers[PaymentGateway]):
    with pytest.raises(ProviderNotFoundError) as loi:
        co_ban.get("zalopay")
    assert loi.value.status_code == 404
    # Thông báo phải chỉ ra đang có gì, nếu không người dùng phải đi mò.
    assert "momo" in str(loi.value) and "vnpay" in str(loi.value)


def test_thieu_nang_luc_thi_501_chu_khong_phai_500(hoan: Providers[HoanTien]):
    """Momo có thật, chỉ là không hoàn tiền được — đó không phải bug của server."""
    with pytest.raises(CapabilityNotSupportedError) as loi:
        hoan.get("momo")
    assert loi.value.status_code == 501
    assert "HoanTien" in str(loi.value)
    # Nói luôn nó làm được gì, để người đọc biết phải đổi sang đâu.
    assert "PaymentGateway" in str(loi.value)


def test_names_chi_liet_ke_provider_lam_duoc_viec_cua_so(
    co_ban: Providers[PaymentGateway], hoan: Providers[HoanTien]
):
    """`Providers[HoanTien].names()` là "cổng hoàn tiền được", không phải mọi cổng."""
    assert co_ban.names() == ["momo", "vnpay"]
    assert hoan.names() == ["vnpay"]
    assert hoan.all_names() == ["momo", "vnpay"]


def test_supports_va_describe(co_ban: Providers[PaymentGateway], hoan: Providers[HoanTien]):
    assert hoan.supports("vnpay") is True
    assert hoan.supports("momo") is False
    assert co_ban.describe() == [
        {"name": "momo", "capabilities": ["PaymentGateway"]},
        {"name": "vnpay", "capabilities": ["HoanTien", "PaymentGateway"]},
    ]
    assert hoan.describe() == [{"name": "vnpay", "capabilities": ["HoanTien", "PaymentGateway"]}]


def test_supports_ten_khong_co_van_la_404(hoan: Providers[HoanTien]):
    with pytest.raises(ProviderNotFoundError):
        hoan.supports("khong-co")


def test_ten_provider_khong_hop_le_bi_chan_ngay():
    with pytest.raises(ValueError, match="không hợp lệ"):
        provider("VNPay Hoa Toc")


# ------------------------------------------------------ nhận diện năng lực
def test_nang_luc_nhan_dien_duoc_du_interface_nam_o_dau():
    """Lọc theo BẢN CHẤT (là ABC) chứ không theo tiền tố module.

    Lọc theo tên module là lỗi của bản registry tham khảo: interface đặt ngoài
    đúng một thư mục quy ước thì capabilities() im lặng trả rỗng.
    """
    assert capabilities_of(VNPayProvider) == ["HoanTien", "PaymentGateway"]
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


@provider("can-settings")
class ProviderCanSettings(CoPhuThuoc):
    """Provider có phụ thuộc — registry tham khảo dựng bằng `cls()` nên chết ở đây."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def ten_app(self) -> str:
        return self._settings.name


def test_provider_nhan_duoc_phu_thuoc_qua_container():
    container.override(Settings, Settings(APP_NAME="thu-nghiem", APP_DB=DatabaseSettings()))
    so = Providers("cfg", CoPhuThuoc, {"can-settings": ProviderCanSettings})
    assert so.get("can-settings").ten_app() == "thu-nghiem"


def test_provider_la_singleton_theo_mac_dinh(co_ban: Providers[PaymentGateway]):
    assert co_ban.get("vnpay") is co_ban.get("vnpay")


@provider("theo-request", scope=Scope.REQUEST)
class ProviderTheoRequest(CoPhuThuoc):
    def ten_app(self) -> str:
        return "moi-request-mot-cai"


@pytest.mark.asyncio
async def test_scope_request_duoc_ton_trong():
    so = Providers("cfg", CoPhuThuoc, {"theo-request": ProviderTheoRequest})
    async with request_scope():
        a, b = so.get("theo-request"), so.get("theo-request")
        assert a is b
    async with request_scope():
        assert so.get("theo-request") is not a


def test_service_nhan_duoc_so_qua_annotation_Providers():
    """Đúng cách người dùng viết: `def __init__(self, x: Providers[PaymentGateway])`."""
    container.override("Providers[PaymentGateway]", Providers("payment", PaymentGateway, dict(_LOP)))

    @injectable
    class DonHangService:
        def __init__(self, payments: Providers[PaymentGateway]) -> None:
            self._payments = payments

        def cong(self, ten: str):
            return self._payments.get(ten)

    assert isinstance(container.resolve(DonHangService).cong("vnpay"), VNPayProvider)


def test_quen_goi_register_providers_thi_bao_dung_nguyen_nhan():
    """Lỗi hay gặp nhất: main.py lắp tay mà quên register_providers().

    Câu "thiếu @injectable" mặc định dẫn người đọc đi sai hướng.
    """
    with pytest.raises(RuntimeError) as loi:
        container.resolve("Providers", ("ChuaTungCo",))
    assert "register_providers()" in str(loi.value)
    assert "@injectable" not in str(loi.value)


# ------------------------------------------------------- quét thư mục thật
def _du_an_mau(root: Path) -> None:
    """Dựng một cây src/providers/ thật trên đĩa để test register_providers."""
    ho = root / "providers_thu" / "sms"
    ho.mkdir(parents=True)
    (root / "providers_thu" / "__init__.py").write_text("", encoding="utf-8")
    (ho / "__init__.py").write_text('"""Họ sms."""\n', encoding="utf-8")
    (ho / "capabilities.py").write_text(
        textwrap.dedent(
            """
            from abc import ABC, abstractmethod

            class GuiSms(ABC):
                @abstractmethod
                async def gui(self, so: str, noi_dung: str) -> bool: ...

            class GuiHangLoat(ABC):
                @abstractmethod
                async def gui_nhieu(self, so: list[str]) -> int: ...
            """
        ),
        encoding="utf-8",
    )
    (ho / "viettel.py").write_text(
        textwrap.dedent(
            """
            from fastapi_modular import provider
            from providers_thu.sms.capabilities import GuiHangLoat, GuiSms

            @provider("viettel")
            class ViettelSms(GuiSms, GuiHangLoat):
                async def gui(self, so, noi_dung): return True
                async def gui_nhieu(self, so): return len(so)
            """
        ),
        encoding="utf-8",
    )
    (ho / "vina.py").write_text(
        textwrap.dedent(
            """
            from fastapi_modular import provider
            from providers_thu.sms.capabilities import GuiSms

            @provider("vina")
            class VinaSms(GuiSms):
                async def gui(self, so, noi_dung): return True
            """
        ),
        encoding="utf-8",
    )
    # File bắt đầu bằng _ phải bị bỏ qua.
    (ho / "_nhap.py").write_text("raise AssertionError('không được import file _')", encoding="utf-8")


def _don_sys_modules() -> None:
    for ten in [m for m in sys.modules if m.startswith("providers_thu")]:
        del sys.modules[ten]


def test_register_providers_dung_mot_so_cho_moi_nang_luc(tmp_path: Path):
    _du_an_mau(tmp_path)
    sys.path.insert(0, str(tmp_path))
    try:
        so = register_providers("providers_thu")
        assert sorted(so) == ["GuiHangLoat", "GuiSms"]
        assert so["GuiSms"].names() == ["viettel", "vina"]
        assert so["GuiHangLoat"].names() == ["viettel"]      # vina không gửi hàng loạt

        # Đúng đường đi mà service dùng.
        assert container.resolve("Providers", ("GuiSms",)) is so["GuiSms"]
        assert so["GuiSms"].family == "sms"
    finally:
        sys.path.remove(str(tmp_path))
        _don_sys_modules()


def test_khong_co_thu_muc_providers_thi_im_lang_bo_qua():
    assert register_providers("khong_he_ton_tai_dau_nhe") == {}


def test_hai_ho_trung_ten_nang_luc_bi_chan(tmp_path: Path):
    """Container tra theo TÊN LỚP nên hai họ không được cùng tên năng lực."""
    _du_an_mau(tmp_path)
    ho2 = tmp_path / "providers_thu" / "email"
    ho2.mkdir()
    (ho2 / "__init__.py").write_text("", encoding="utf-8")
    (ho2 / "capabilities.py").write_text(
        "from abc import ABC, abstractmethod\n\n"
        "class GuiSms(ABC):\n    @abstractmethod\n    async def gui(self) -> bool: ...\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(tmp_path))
    try:
        with pytest.raises(RuntimeError, match="cùng khai năng lực tên"):
            register_providers("providers_thu")
    finally:
        sys.path.remove(str(tmp_path))
        _don_sys_modules()


# ------------------------------------------------- hai họ độc lập với nhau
def test_hai_ho_dung_chung_mot_ten_class_van_song_hoa_binh():
    """`OryzaProvider` bên thiết bị và bên thông báo là HAI thứ khác nhau.

    Đây đúng là lý do khái niệm "họ" tồn tại. Bản đầu tôi cho @provider gọi
    @injectable nên chúng đụng nhau ở sổ toàn cục tra theo tên class.
    """

    class GuiTin(ABC):
        @abstractmethod
        def gui(self) -> str: ...

    @provider("oryza")
    class OryzaProvider(GuiTin):
        def gui(self) -> str:
            return "thiet-bi"

    ho_a = Providers("device", GuiTin, {"oryza": OryzaProvider})

    @provider("oryza")
    class OryzaProvider(GuiTin):
        def gui(self) -> str:
            return "thong-bao"

    ho_b = Providers("notification", GuiTin, {"oryza": OryzaProvider})

    assert ho_a.get("oryza").gui() == "thiet-bi"
    assert ho_b.get("oryza").gui() == "thong-bao"


def test_provider_khong_lam_ban_so_dang_ky_toan_cuc():
    """@provider KHÔNG được đẩy class vào _REGISTRY — đó là sổ tra theo tên."""
    from fastapi_modular.core.container import _REGISTRY

    assert "VNPayProvider" not in _REGISTRY


# --------------------------------------------------------- lệnh fam provider
def test_fam_provider_sinh_ho_moi_roi_them_provider(tmp_path: Path):
    from fastapi_modular.cli.new_provider import main as sinh

    goc = tmp_path / "src" / "providers"
    assert sinh(["payment", "vnpay", "--root", str(goc)]) == 0
    ho = goc / "payment"
    assert (ho / "__init__.py").exists()
    assert (ho / "capabilities.py").exists()

    # __init__.py giờ chỉ là docstring — không còn lớp token phải bảo trì.
    assert "class " not in (ho / "__init__.py").read_text(encoding="utf-8")

    vnpay = (ho / "vnpay.py").read_text(encoding="utf-8")
    assert '@provider("vnpay")' in vnpay
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


def test_tach_capabilities_thanh_package_van_quet_duoc(tmp_path: Path):
    """Tách mỗi năng lực một file phải chạy nguyên vẹn — đó là lời hứa trong docs.

    Và re-export ở `capabilities/__init__.py` KHÔNG được làm năng lực bị đếm
    hai lần: chỉ class định nghĩa ở một module mới được tính.
    """
    goi = tmp_path / "prov_tach"
    caps = goi / "device" / "capabilities"
    caps.mkdir(parents=True)
    (goi / "__init__.py").write_text("", encoding="utf-8")
    (goi / "device" / "__init__.py").write_text('"""Họ device."""\n', encoding="utf-8")
    (caps / "door.py").write_text(
        "from abc import ABC, abstractmethod\n\n"
        "class DoorManagement(ABC):\n"
        "    @abstractmethod\n"
        "    async def open_door(self, door_id: str) -> bool: ...\n",
        encoding="utf-8",
    )
    (caps / "camera.py").write_text(
        "from abc import ABC, abstractmethod\n\n"
        "class CameraManagement(ABC):\n"
        "    @abstractmethod\n"
        "    async def snapshot(self, cam_id: str) -> bytes: ...\n",
        encoding="utf-8",
    )
    (caps / "__init__.py").write_text(
        "from prov_tach.device.capabilities.camera import CameraManagement\n"
        "from prov_tach.device.capabilities.door import DoorManagement\n\n"
        '__all__ = ["CameraManagement", "DoorManagement"]\n',
        encoding="utf-8",
    )
    (goi / "device" / "hik.py").write_text(
        "from fastapi_modular import provider\n"
        "from prov_tach.device.capabilities import CameraManagement\n\n"
        '@provider("hik")\n'
        "class HikDevice(CameraManagement):\n"
        "    async def snapshot(self, cam_id): return b''\n",
        encoding="utf-8",
    )

    sys.path.insert(0, str(tmp_path))
    try:
        so = register_providers("prov_tach")
        assert sorted(so) == ["CameraManagement", "DoorManagement"]
        assert so["CameraManagement"].names() == ["hik"]
        assert so["DoorManagement"].names() == []      # hik không mở cửa được
    finally:
        sys.path.remove(str(tmp_path))
        for ten in [m for m in sys.modules if m.startswith("prov_tach")]:
            del sys.modules[ten]
