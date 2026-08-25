"""Provider cắm được — chọn bản hiện thực bằng TÊN lúc chạy.

Container giải quyết phụ thuộc theo **kiểu**, quyết định lúc viết code:

    def __init__(self, repo: Repository[User]) -> None: ...

Nhưng có loại phụ thuộc chỉ biết tên lúc chạy — cổng thanh toán lấy từ cột
trong đơn hàng, nhà mạng SMS lấy từ cấu hình, hãng camera lấy từ bản ghi thiết
bị. Đây là chỗ lấp vào, và dùng đúng một khuôn với `Repository[User]`:

    # src/providers/payment/capabilities.py — việc mà cổng thanh toán làm được
    class PaymentGateway(ABC):
        @abstractmethod
        async def tao_giao_dich(self, so_tien: int, ma_don: str) -> str: ...

    # src/providers/payment/vnpay.py — một bản hiện thực
    @provider("vnpay")
    class VnpayPayment(PaymentGateway):
        def __init__(self, settings: Settings) -> None:   # DI chạy bình thường
            self._key = settings.vnpay.secret

    # src/api/don_hang/don_hang_service.py — khai ĐÚNG năng lực cần
    @injectable
    class DonHangService:
        def __init__(self, payments: Providers[PaymentGateway]) -> None:
            self._payments = payments

        async def thanh_toan(self, don: DonHang) -> str:
            cong = self._payments.get(don.cong_thanh_toan)   # -> PaymentGateway
            return await cong.tao_giao_dich(don.so_tien, don.ma)

Thêm cổng mới = thả một file vào `src/providers/payment/`. Không sửa service,
không sửa `main.py`, không có danh sách import nào phải bảo trì.

Vì sao khai NĂNG LỰC chứ không phải tên họ: năng lực là thứ service thật sự
cần, và là tên lớp có thật nên IDE gợi ý được method ngay sau `get(...)`. Nó
cũng khiến "quên nói mình cần năng lực nào" trở thành chuyện không xảy ra được.

Vì sao TÁCH NHỎ năng lực thay vì một interface to: camera Hik không mở được
cửa, nên nó chỉ hiện thực `CameraManagement`. Service nào cần mở cửa thì khai
`Providers[DoorManagement]`, và hỏi `"hik"` sẽ nhận lỗi 501 nói rõ thiếu gì —
thay vì bắt Hik viết method rỗng chỉ để thoả ABC.
"""

from __future__ import annotations

import re
from abc import ABC
from collections.abc import Callable
from typing import Any, Generic, TypeVar

from fastapi_modular.core.container import Scope, container
from fastapi_modular.core.exceptions import AppError

T = TypeVar("T", bound=type)

#: Năng lực mà một sổ phục vụ — `Providers[PaymentGateway]`.
C = TypeVar("C")

#: Gói mặc định chứa các họ provider. Đổi bằng `register_providers(package=...)`.
DEFAULT_PROVIDERS_PACKAGE = "src.providers"

#: Thuộc tính đánh dấu do @provider gắn lên class.
_TEN_PROVIDER = "__provider_name__"
_SCOPE_PROVIDER = "__provider_scope__"

_TEN_HOP_LE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class ProviderNotFoundError(AppError):
    """Tên provider không có trong họ — thường là client gửi sai tên."""

    status_code = 404
    error_code = "provider_not_found"
    message = "Provider không tồn tại"


class CapabilityNotSupportedError(AppError):
    """Provider có thật nhưng không làm được việc được yêu cầu.

    501 chứ không phải 500: server hiểu yêu cầu, chỉ là bản hiện thực này không
    có năng lực đó (Hik không mở được cửa). Đó không phải bug.
    """

    status_code = 501
    error_code = "capability_not_supported"
    message = "Provider không hỗ trợ năng lực này"


def provider(name: str, *, scope: Scope = Scope.SINGLETON) -> Callable[[T], T]:
    """Đăng ký class làm provider mang tên `name` trong họ chứa nó.

    HỌ suy ra từ vị trí file, không phải khai tay: `src/providers/payment/vnpay.py`
    thuộc họ `payment`. Đặt file vào đúng thư mục là xong.

    Provider được dựng qua container nên `__init__` nhận phụ thuộc như mọi
    service khác. Nhưng KHÔNG vào sổ `_REGISTRY` toàn cục: sổ đó tra theo tên
    class, mà hai họ có quyền cùng có một `OryzaProvider`.
    """
    if not _TEN_HOP_LE.match(name):
        raise ValueError(
            f"Tên provider không hợp lệ: {name!r}. Chữ thường, số, gạch ngang hoặc gạch dưới."
        )

    def decorate(target: T) -> T:
        setattr(target, _TEN_PROVIDER, name)
        setattr(target, _SCOPE_PROVIDER, scope)
        return target

    return decorate


def capabilities_of(provider_cls: type) -> list[str]:
    """Tên các interface năng lực mà `provider_cls` hiện thực.

    Nhận diện theo BẢN CHẤT (base nào là ABC) chứ không theo tên module. Lọc
    theo tiền tố module thì chỉ đúng với đúng một cách xếp thư mục, và im lặng
    trả rỗng với mọi dự án xếp khác.
    """
    return sorted(
        base.__name__
        for base in provider_cls.__mro__
        if base not in (provider_cls, ABC, object) and ABC in base.__mro__
    )


class Providers(Generic[C]):
    """Sổ các provider hiện thực MỘT năng lực. Nhận qua DI: `Providers[NangLuc]`.

    Không dựng tay — `register_providers()` dựng sẵn mỗi (họ, năng lực) một sổ
    và cắm vào container.
    """

    def __init__(self, family: str, capability: type, classes: dict[str, type]) -> None:
        self._family = family
        self._capability = capability
        #: Sổ dùng CHUNG dict của cả họ, nên thêm provider là mọi năng lực thấy ngay.
        self._classes = classes

    def __repr__(self) -> str:
        ten = ", ".join(self.names()) or "rỗng"
        return f"<Providers[{self._capability.__name__}] (họ {self._family}): {ten}>"

    @property
    def family(self) -> str:
        return self._family

    @property
    def capability(self) -> type:
        return self._capability

    # -- tra cứu ----------------------------------------------------------

    def get(self, name: str) -> C:
        """Provider `name`, đã khẳng định hiện thực năng lực của sổ này.

        Kiểm bằng `issubclass` chứ không phải `hasattr`: method abstract kế thừa
        vẫn cho `hasattr == True` dù lớp con chưa hiện thực gì.
        """
        provider_cls = self._classes.get(name)
        if provider_cls is None:
            co = ", ".join(sorted(self._classes)) or "(chưa có cái nào)"
            raise ProviderNotFoundError(
                f"Không có provider '{name}' trong họ '{self._family}'. Đang có: {co}"
            )

        if not issubclass(provider_cls, self._capability):
            lam_duoc = ", ".join(capabilities_of(provider_cls)) or "(không có năng lực nào)"
            raise CapabilityNotSupportedError(
                f"Provider '{name}' (họ '{self._family}') không hỗ trợ "
                f"{self._capability.__name__}. Nó làm được: {lam_duoc}"
            )

        return container.build(
            provider_cls,
            key=f"providers:{self._family}:{name}",
            scope=getattr(provider_cls, _SCOPE_PROVIDER, Scope.SINGLETON),
        )

    def supports(self, name: str) -> bool:
        """Provider `name` có làm được việc của sổ này không. 404 nếu không có tên."""
        provider_cls = self._classes.get(name)
        if provider_cls is None:
            raise ProviderNotFoundError(
                f"Không có provider '{name}' trong họ '{self._family}'."
            )
        return issubclass(provider_cls, self._capability)

    def names(self) -> list[str]:
        """CHỈ những provider hiện thực năng lực này.

        `Providers[HoanTien].names()` là "các cổng hoàn tiền được", không phải
        "mọi cổng thanh toán" — thường đúng thứ endpoint cần trả về.
        """
        return sorted(
            ten for ten, cls in self._classes.items() if issubclass(cls, self._capability)
        )

    def all_names(self) -> list[str]:
        """Mọi provider của họ, kể cả cái không làm được việc này."""
        return sorted(self._classes)

    def capabilities(self, name: str) -> list[str]:
        provider_cls = self._classes.get(name)
        if provider_cls is None:
            raise ProviderNotFoundError(
                f"Không có provider '{name}' trong họ '{self._family}'."
            )
        return capabilities_of(provider_cls)

    def describe(self) -> list[dict[str, Any]]:
        """Bảng tóm tắt — trả thẳng ra endpoint liệt kê được."""
        return [{"name": ten, "capabilities": self.capabilities(ten)} for ten in self.names()]


def register_providers(package: str = DEFAULT_PROVIDERS_PACKAGE) -> dict[str, Providers]:
    """Quét `package`, dựng một sổ cho MỖI (họ, năng lực), cắm vào container.

    Gọi một lần trong `src/main.py` (hoặc để `create_app()` gọi hộ). Quét NGAY
    LÚC KHỞI ĐỘNG chứ không lười: file provider lỗi cú pháp thì `fam dev` chết
    ngay kèm traceback, thay vì chết ở request đầu tiên chạm tới nó.

    Không có `src/providers/` thì im lặng bỏ qua — dự án không dùng provider
    không phải tạo thư mục rỗng.

    Trả về {tên năng lực: sổ}.
    """
    import importlib
    import pkgutil

    from fastapi_modular.core.logging import get_logger

    log = get_logger(__name__)

    try:
        goc = importlib.import_module(package)
    except ModuleNotFoundError:
        return {}

    duong_dan = getattr(goc, "__path__", None)
    if duong_dan is None:
        raise RuntimeError(f"'{package}' phải là một package (có __init__.py), không phải module.")

    so: dict[str, Providers] = {}
    ho_cua_nang_luc: dict[str, str] = {}

    for info in pkgutil.iter_modules(list(duong_dan)):
        if not info.ispkg or info.name.startswith("_"):
            continue

        family = info.name
        goi_ho = f"{package}.{family}"
        classes: dict[str, type] = {}
        nang_luc: dict[str, type] = {}

        for con in pkgutil.walk_packages([f"{p}/{family}" for p in duong_dan], prefix=f"{goi_ho}."):
            if con.name.rsplit(".", 1)[-1].startswith("_"):
                continue
            module = importlib.import_module(con.name)
            for doi_tuong in vars(module).values():
                if not isinstance(doi_tuong, type):
                    continue
                # Chỉ nhận thứ ĐỊNH NGHĨA ở module này. Không lọc thì một class
                # được import sang file khác sẽ bị đếm hai lần.
                if doi_tuong.__module__ != con.name:
                    continue

                ten = getattr(doi_tuong, _TEN_PROVIDER, None)
                if ten is not None:
                    truoc = classes.get(ten)
                    if truoc is not None and truoc is not doi_tuong:
                        raise RuntimeError(
                            f"Họ '{family}' có hai provider cùng tên '{ten}': "
                            f"{truoc.__module__}.{truoc.__qualname__} và "
                            f"{doi_tuong.__module__}.{doi_tuong.__qualname__}. Đổi tên một cái."
                        )
                    classes[ten] = doi_tuong
                elif ABC in doi_tuong.__mro__ and doi_tuong is not ABC:
                    nang_luc[doi_tuong.__name__] = doi_tuong

        for ten_cap, cap in nang_luc.items():
            truoc_ho = ho_cua_nang_luc.get(ten_cap)
            if truoc_ho is not None:
                raise RuntimeError(
                    f"Hai họ cùng khai năng lực tên '{ten_cap}': '{truoc_ho}' và '{family}'. "
                    "Container tra theo TÊN LỚP nên phải đặt khác nhau."
                )
            ho_cua_nang_luc[ten_cap] = family
            so_cua_cap = Providers(family, cap, classes)
            so[ten_cap] = so_cua_cap
            container.override(f"Providers[{ten_cap}]", so_cua_cap)

    if so:
        log.info(
            "providers.registered",
            package=package,
            families=sorted(set(ho_cua_nang_luc.values())),
            capabilities=sorted(so),
        )
    return so
