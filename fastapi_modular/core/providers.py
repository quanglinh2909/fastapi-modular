"""Sổ đăng ký provider — chọn bản hiện thực bằng TÊN lúc chạy.

Container giải quyết phụ thuộc theo **kiểu**, quyết định lúc viết code:

    def __init__(self, repo: Repository[User]) -> None: ...

Nhưng có loại phụ thuộc chỉ biết tên lúc chạy — cổng thanh toán lấy từ cột
trong đơn hàng, nhà mạng SMS lấy từ cấu hình, hãng camera lấy từ bản ghi thiết
bị. Container không làm được việc đó, và đây là chỗ lấp vào.

Ba khái niệm:

    HỌ         một loại dịch vụ cắm được — thư mục `src/providers/<họ>/`
    NĂNG LỰC   interface ABC mô tả "loại này làm được gì"
    PROVIDER   một bản hiện thực cụ thể, mang @provider("tên")

Dùng:

    # src/providers/payment/capabilities.py
    class PaymentGateway(ABC):
        @abstractmethod
        async def tao_giao_dich(self, so_tien: int, ma_don: str) -> str: ...

    # src/providers/payment/vnpay.py
    @provider("vnpay")
    class VNPayProvider(PaymentGateway):
        def __init__(self, settings: Settings) -> None:   # DI chạy bình thường
            self._key = settings.vnpay.secret

    # src/api/don_hang/don_hang_service.py
    @injectable
    class DonHangService:
        def __init__(self, payments: Providers["payment"]) -> None:
            self._payments = payments

        async def thanh_toan(self, don: DonHang) -> str:
            cong = self._payments.require(don.cong_thanh_toan, PaymentGateway)
            return await cong.tao_giao_dich(don.so_tien, don.ma)

Thêm cổng mới = thả một file vào `src/providers/payment/`. Không sửa service,
không sửa `main.py`, không có danh sách import nào phải bảo trì.

Vì sao TÁCH NHỎ năng lực thay vì một interface to: camera Hik không mở được
cửa. Nó chỉ hiện thực `CameraManagement`, và `require("hik", DoorManagement)`
trả lỗi 501 nói đúng thiếu gì — thay vì bắt nó viết method rỗng chỉ để thoả ABC.
"""

from __future__ import annotations

import re
from abc import ABC
from collections.abc import Callable
from typing import Any, TypeVar

from fastapi_modular.core.container import Scope, container
from fastapi_modular.core.exceptions import AppError

T = TypeVar("T", bound=type)

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


class Registry:
    """Sổ đăng ký của MỘT họ provider.

    Không dựng tay — `register_providers()` dựng sẵn mỗi họ một sổ và cắm vào
    container. Service nhận nó qua `Providers["<họ>"]`.
    """

    def __init__(self, family: str) -> None:
        self._family = family
        self._classes: dict[str, type] = {}

    def __repr__(self) -> str:
        ten = ", ".join(sorted(self._classes)) or "rỗng"
        return f"<Providers[{self._family}]: {ten}>"

    # -- đăng ký (register_providers gọi) ---------------------------------

    def add(self, name: str, provider_cls: type) -> None:
        truoc = self._classes.get(name)
        if truoc is not None and truoc is not provider_cls:
            raise RuntimeError(
                f"Họ '{self._family}' có hai provider cùng tên '{name}': "
                f"{truoc.__module__}.{truoc.__qualname__} và "
                f"{provider_cls.__module__}.{provider_cls.__qualname__}. Đổi tên một cái."
            )
        self._classes[name] = provider_cls

    # -- tra cứu ----------------------------------------------------------

    def get(self, name: str) -> Any:
        """Instance của provider `name`. Dựng qua container nên có đủ DI."""
        return self._dung(name, self.get_class(name))

    def _dung(self, name: str, provider_cls: type) -> Any:
        return container.build(
            provider_cls,
            key=f"providers:{self._family}:{name}",
            scope=getattr(provider_cls, _SCOPE_PROVIDER, Scope.SINGLETON),
        )

    def get_class(self, name: str) -> type:
        provider_cls = self._classes.get(name)
        if provider_cls is None:
            co = ", ".join(sorted(self._classes)) or "(chưa có cái nào)"
            raise ProviderNotFoundError(
                f"Không có provider '{name}' trong họ '{self._family}'. Đang có: {co}"
            )
        return provider_cls

    def require(self, name: str, capability: type) -> Any:
        """Lấy provider và khẳng định nó hiện thực `capability`.

        Kiểm bằng `issubclass` chứ không phải `hasattr`: method abstract kế thừa
        vẫn cho `hasattr == True` dù lớp con chưa hiện thực gì.
        """
        provider_cls = self.get_class(name)
        if not issubclass(provider_cls, capability):
            co = ", ".join(capabilities_of(provider_cls)) or "(không có năng lực nào)"
            raise CapabilityNotSupportedError(
                f"Provider '{name}' (họ '{self._family}') không hỗ trợ "
                f"{capability.__name__}. Nó làm được: {co}"
            )
        return self._dung(name, provider_cls)

    def supports(self, name: str, capability: type) -> bool:
        return issubclass(self.get_class(name), capability)

    def names(self) -> list[str]:
        """Tên mọi provider CỦA HỌ NÀY — không lẫn họ khác."""
        return sorted(self._classes)

    def capabilities(self, name: str) -> list[str]:
        return capabilities_of(self.get_class(name))

    def describe(self) -> list[dict[str, Any]]:
        """Bảng tóm tắt cả họ — trả thẳng ra endpoint liệt kê được."""
        return [{"name": ten, "capabilities": self.capabilities(ten)} for ten in self.names()]


class ProviderFamily:
    """Lớp nền cho token DI của một họ. `fam provider` sinh sẵn lớp con.

        # src/providers/payment/__init__.py
        class PaymentProviders(ProviderFamily, family="payment"):
            pass

        # service
        def __init__(self, payments: PaymentProviders) -> None: ...

    Vì sao là CLASS chứ không phải `Providers["payment"]`: chuỗi trong subscript
    bị ruff đọc thành forward-reference nên báo F821 ở mọi dự án bật lint. Dùng
    lớp thì annotation là một tên thật — lint sạch, IDE gợi ý được method, và
    container phân giải theo đúng đường bình thường của nó.

    Không bao giờ khởi tạo: `register_providers()` cắm sẵn một `Registry` vào
    khoá mang tên lớp này.
    """

    #: Tên họ, do `class X(ProviderFamily, family="...")` đặt.
    __family__: str = ""

    def __init_subclass__(cls, family: str = "", **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not family:
            raise TypeError(
                f"{cls.__name__} thiếu tên họ. Viết: "
                f'class {cls.__name__}(ProviderFamily, family="ten-ho")'
            )
        if not _TEN_HOP_LE.match(family):
            raise ValueError(
                f"Tên họ không hợp lệ: {family!r}. Chữ thường, số, gạch ngang hoặc gạch dưới."
            )
        cls.__family__ = family

    def __init__(self) -> None:  # pragma: no cover - không bao giờ gọi tới
        raise RuntimeError("ProviderFamily là token DI, không phải class để khởi tạo.")


def register_providers(package: str = DEFAULT_PROVIDERS_PACKAGE) -> dict[str, Registry]:
    """Quét `package`, dựng một `Registry` cho MỖI thư mục con, cắm vào container.

    Gọi một lần trong `src/main.py` (hoặc để `create_app()` gọi hộ). Quét NGAY
    LÚC KHỞI ĐỘNG chứ không lười: file provider lỗi cú pháp thì `fam dev` chết
    ngay kèm traceback, thay vì chết ở request đầu tiên chạm tới nó.

    Không có `src/providers/` thì im lặng bỏ qua — dự án không dùng provider
    không phải tạo thư mục rỗng.
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

    so: dict[str, Registry] = {}
    for info in pkgutil.iter_modules(list(duong_dan)):
        if not info.ispkg or info.name.startswith("_"):
            continue

        family = info.name
        goi_ho = f"{package}.{family}"
        registry = Registry(family)

        for con in pkgutil.walk_packages([f"{p}/{family}" for p in duong_dan], prefix=f"{goi_ho}."):
            if con.name.rsplit(".", 1)[-1].startswith("_"):
                continue
            module = importlib.import_module(con.name)
            for doi_tuong in vars(module).values():
                ten = getattr(doi_tuong, _TEN_PROVIDER, None)
                if ten is None or not isinstance(doi_tuong, type):
                    continue
                # Chỉ nhận class ĐỊNH NGHĨA ở module này. Không lọc thì một
                # class được import sang file khác sẽ bị đếm hai lần.
                if doi_tuong.__module__ != con.name:
                    continue
                registry.add(ten, doi_tuong)

        so[family] = registry
        token = _token_cua(importlib.import_module(goi_ho), family)
        if token is None:
            raise RuntimeError(
                f"Họ '{family}' chưa có token DI. Thêm vào {goi_ho}/__init__.py:\n"
                f'    class {family.replace("-", "_").title().replace("_", "")}Providers'
                f'(ProviderFamily, family="{family}"): ...'
            )
        container.override(token, registry)

    if so:
        log.info(
            "providers.registered",
            package=package,
            families=sorted(so),
            count=sum(len(r.names()) for r in so.values()),
        )
    return so


def _token_cua(goi_ho: Any, family: str) -> type | None:
    """Tìm lớp token `ProviderFamily` khai trong `__init__.py` của họ.

    Tra ngay trong package của họ chứ không giữ một map toàn cục: map toàn cục
    sống xuyên suốt tiến trình nên hai dự án (hoặc hai test) cùng tên họ sẽ
    thấy token của nhau.
    """
    for doi_tuong in vars(goi_ho).values():
        if (
            isinstance(doi_tuong, type)
            and issubclass(doi_tuong, ProviderFamily)
            and doi_tuong is not ProviderFamily
            and doi_tuong.__family__ == family
        ):
            return doi_tuong
    return None
