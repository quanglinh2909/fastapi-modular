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
_PROVIDER_NAME = "__provider_name__"
_SCOPE_PROVIDER = "__provider_scope__"

_VALID_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


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
    if not _VALID_NAME.match(name):
        raise ValueError(
            f"Tên provider không hợp lệ: {name!r}. Chữ thường, số, gạch ngang hoặc gạch dưới."
        )

    def decorate(target: T) -> T:
        setattr(target, _PROVIDER_NAME, name)
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
        provider_name = ", ".join(self.names()) or "rỗng"
        return f"<Providers[{self._capability.__name__}] (họ {self._family}): {provider_name}>"

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
            has = ", ".join(sorted(self._classes)) or "(chưa có cái nào)"
            raise ProviderNotFoundError(
                f"Không có provider '{name}' trong họ '{self._family}'. Đang có: {has}"
            )

        if not issubclass(provider_cls, self._capability):
            supported = ", ".join(capabilities_of(provider_cls)) or "(không có năng lực nào)"
            raise CapabilityNotSupportedError(
                f"Provider '{name}' (họ '{self._family}') không hỗ trợ "
                f"{self._capability.__name__}. Nó làm được: {supported}"
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
            provider_name for provider_name, cls in self._classes.items() if issubclass(cls, self._capability)
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
        return [{"name": provider_name, "capabilities": self.capabilities(provider_name)} for provider_name in self.names()]


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
        root = importlib.import_module(package)
    except ModuleNotFoundError:
        return {}

    path = getattr(root, "__path__", None)
    if path is None:
        raise RuntimeError(f"'{package}' phải là một package (có __init__.py), không phải module.")

    count: dict[str, Providers] = {}
    family_of_capability: dict[str, str] = {}

    for info in pkgutil.iter_modules(list(path)):
        if not info.ispkg or info.name.startswith("_"):
            continue

        family = info.name
        call_for_me = f"{package}.{family}"
        classes: dict[str, type] = {}
        capability_names: dict[str, type] = {}

        for child in pkgutil.walk_packages([f"{p}/{family}" for p in path], prefix=f"{call_for_me}."):
            if child.name.rsplit(".", 1)[-1].startswith("_"):
                continue
            module = importlib.import_module(child.name)
            for obj in vars(module).values():
                if not isinstance(obj, type):
                    continue
                # Chỉ nhận thứ ĐỊNH NGHĨA ở module này. Không lọc thì một class
                # được import sang file khác sẽ bị đếm hai lần.
                if obj.__module__ != child.name:
                    continue

                provider_name = getattr(obj, _PROVIDER_NAME, None)
                if provider_name is not None:
                    before = classes.get(provider_name)
                    if before is not None and before is not obj:
                        raise RuntimeError(
                            f"Họ '{family}' có hai provider cùng tên '{provider_name}': "
                            f"{before.__module__}.{before.__qualname__} và "
                            f"{obj.__module__}.{obj.__qualname__}. Đổi tên một cái."
                        )
                    classes[provider_name] = obj
                elif ABC in obj.__mro__ and obj is not ABC:
                    capability_names[obj.__name__] = obj

        for capability_name, level in capability_names.items():
            family_prefix = family_of_capability.get(capability_name)
            if family_prefix is not None:
                raise RuntimeError(
                    f"Hai họ cùng khai năng lực tên '{capability_name}': '{family_prefix}' và '{family}'. "
                    "Container tra theo TÊN LỚP nên phải đặt khác nhau."
                )
            family_of_capability[capability_name] = family
            registry_of = Providers(family, level, classes)
            count[capability_name] = registry_of
            container.override(f"Providers[{capability_name}]", registry_of)

    if count:
        log.info(
            "providers.registered",
            package=package,
            families=sorted(set(family_of_capability.values())),
            capabilities=sorted(count),
        )
    return count
