# Provider cắm được

Chọn **bản hiện thực bằng tên lúc chạy**: cổng thanh toán lấy từ cột trong đơn
hàng, nhà mạng SMS lấy từ cấu hình, hãng camera lấy từ bản ghi thiết bị.

```
DonHangService ──── require("vnpay") ────▶ VnpayPayment
                                          MomoPayment
                                          ZaloPayPayment  <- thả file vào là có
```

Container giải quyết phụ thuộc theo **kiểu**, quyết định lúc viết code
(`repo: Repository[User]`). Đây là chỗ lấp phần còn lại: phụ thuộc mà **tên chỉ
biết lúc chạy**.

---

## Ba khái niệm, không hơn

| Khái niệm | Là gì | Ở đâu |
|---|---|---|
| **Họ** | một loại dịch vụ cắm được | thư mục `src/providers/<họ>/` |
| **Năng lực** | interface ABC — "loại này làm được gì" | `src/providers/<họ>/capabilities.py` |
| **Provider** | một bản hiện thực cụ thể | `src/providers/<họ>/<tên>.py` |

---

## 1. Sinh khung bằng một lệnh

```bash
fam provider payment vnpay
```

```
Đã tạo họ 'payment' và provider 'vnpay':
    src/providers/payment/__init__.py
    src/providers/payment/capabilities.py
    src/providers/payment/vnpay.py
```

Sửa `capabilities.py` cho đúng nghiệp vụ của bạn, rồi viết thân hàm trong
`vnpay.py`. Xong.

Thêm provider thứ hai:

```bash
fam provider payment momo
```

Lệnh này **đọc `capabilities.py`** và sinh sẵn stub đúng các method cần viết,
kèm nguyên chữ ký — nên từ provider thứ hai trở đi gần như chỉ còn việc điền
thân hàm.

---

## 2. Ba file được sinh ra

**`__init__.py`** — token DI của họ, hai dòng:

```python
from fastapi_modular import ProviderFamily

from src.providers.payment.capabilities import PaymentGateway


class PaymentProviders(ProviderFamily[PaymentGateway], family="payment"):
    """Token DI của họ. Service khai `def __init__(self, x: PaymentProviders)`."""
```

Tham số generic là **năng lực chính** của họ. Nhờ nó `require(tên)` chỉ cần một
tham số và trả về đúng kiểu `PaymentGateway` — IDE gợi ý được method.

**`capabilities.py`** — việc mà provider *có thể* làm:

```python
from abc import ABC, abstractmethod


class PaymentGateway(ABC):
    @abstractmethod
    async def tao_giao_dich(self, so_tien: int, ma_don: str) -> str: ...


class HoanTien(ABC):                       # năng lực TUỲ CHỌN
    @abstractmethod
    async def hoan_tien(self, ma: str) -> bool: ...
```

**`vnpay.py`** — bản hiện thực:

```python
from fastapi_modular import provider

from src.providers.payment.capabilities import HoanTien, PaymentGateway


@provider("vnpay")
class VnpayPayment(PaymentGateway, HoanTien):
    def __init__(self, settings: Settings) -> None:   # DI chạy bình thường
        self._secret = settings.vnpay.secret

    async def tao_giao_dich(self, so_tien: int, ma_don: str) -> str: ...
    async def hoan_tien(self, ma: str) -> bool: ...
```

> **Tách nhỏ năng lực, đừng gộp một interface to.** Momo không hoàn tiền được
> thì **bỏ `HoanTien` khỏi danh sách kế thừa** rồi xoá method đi — đừng để lại
> thân rỗng. Khi đó `require("momo", HoanTien)` trả lỗi **501** nói rõ thiếu gì,
> thay vì một `NotImplementedError` 500 khó hiểu.

---

## 3. Dùng trong service

Nhận sổ qua DI, đúng cách nhận mọi thứ khác:

```python
from fastapi_modular import injectable

from src.providers.payment import PaymentProviders


@injectable
class DonHangService:
    def __init__(self, payments: PaymentProviders) -> None:
        self._payments = payments

    async def thanh_toan(self, don: DonHang) -> str:
        # tên cổng lấy từ DB — service không biết trước là cổng nào
        cong = self._payments.require(don.cong_thanh_toan)
        return await cong.tao_giao_dich(don.so_tien, don.ma)
```

### Năng lực tuỳ chọn — và vì sao IDE không gợi ý

`require(tên)` trả về **năng lực chính** — thứ khai trong
`ProviderFamily[PaymentGateway]`. Nên IDE chỉ gợi ý method của `PaymentGateway`.

Method thuộc một năng lực **tuỳ chọn** thì phải nói ra:

```python
# IDE KHÔNG biết `hoan_tien` — nó thuộc HoanTien, không phải năng lực chính
cong = self._payments.require(ten)
await cong.hoan_tien(ma)                      # ✗ không gợi ý, type checker báo lỗi

# Nói rõ năng lực -> IDE gợi ý đúng method của HoanTien
cong = self._payments.require(ten, HoanTien)
await cong.hoan_tien(ma)                      # ✓
```

Không chắc provider có năng lực đó không thì hỏi trước, đừng để 501 bay ra:

```python
if self._payments.supports(ten, HoanTien):
    await self._payments.require(ten, HoanTien).hoan_tien(ma)
```

**Thêm ZaloPay = thả một file `zalopay.py` vào thư mục.** Không sửa service,
không sửa `main.py`, không có danh sách import nào phải bảo trì.

### API của sổ

| Gọi | Trả về | Ném lỗi |
|---|---|---|
| `require(tên)` | provider, kiểu tĩnh là **năng lực chính** | **404** không có tên · **501** thiếu năng lực |
| `require(tên, NăngLực)` | provider, kiểu tĩnh là **chính `NăngLực` đó** | nt |
| `get(tên)` | provider, **không** kiểm năng lực | **404** |
| `supports(tên[, NăngLực])` | `bool` | — |
| `names()` | `["momo", "vnpay"]` | — |
| `capabilities(tên)` | `["HoanTien", "PaymentGateway"]` | **404** |
| `describe()` | `[{"name", "capabilities"}, …]` | — |

`describe()` trả thẳng ra endpoint được:

```python
@get("", summary="Các cổng thanh toán đang có")
async def liet_ke(self) -> list[dict]:
    return self._payments.describe()
```

```json
[{"name": "momo",  "capabilities": ["PaymentGateway"]},
 {"name": "vnpay", "capabilities": ["HoanTien", "PaymentGateway"]}]
```

---

## 4. Những điều cần biết

**Họ suy ra từ vị trí file.** `src/providers/payment/vnpay.py` thuộc họ
`payment`. Không phải khai lại tên họ trong `@provider`.

**Hai họ dùng chung một tên là bình thường.** `@provider("oryza")` bên `device`
và bên `notification` là hai thứ khác nhau, kể cả khi hai class trùng tên. Tên
chỉ cần duy nhất **trong một họ**.

**Quét lúc khởi động, không lười.** `create_app()` gọi `register_providers()`
trước khi dựng route. File provider lỗi cú pháp thì `fam dev` chết ngay kèm
traceback, chứ không chết ở request đầu tiên chạm tới nó. Lúc boot có dòng log:

```
providers.registered  package=src.providers families=['payment'] count=2
```

**Provider mặc định là singleton**, dựng qua container nên `__init__` nhận được
`Settings`, `Repository[...]`, hay bất cứ provider nào khác. Cần mỗi request một
cái thì `@provider("x", scope=Scope.REQUEST)`.

**Provider là driver, không phải kết nối.** Một `VnpayPayment` dùng chung cho
mọi đơn hàng; thông tin cụ thể đi theo tham số của method. Đừng giữ trạng thái
của *một* đơn hàng hay *một* thiết bị trong `self`.

**Không có thư mục `src/providers/` thì bỏ qua**, không lỗi. Dự án không dùng
provider không phải tạo thư mục rỗng.

---

## 5. Đổi chỗ đặt

Mặc định là gói `providers` nằm cạnh gói ứng dụng: `src.api` → `src.providers`.
Xếp khác thì khai một lần:

```python
# src/main.py
register_providers("cong_ty.plugins")
```

hoặc qua `create_app`:

```python
app = create_app(AppSettings(), providers_package="cong_ty.plugins")
```

---

## 6. Test

Provider là class thuần, test thẳng không cần gì:

```python
def test_vnpay_tao_duoc_giao_dich():
    assert VnpayPayment(settings).tao_giao_dich(10_000, "DH1")
```

Đổi cả sổ trong test — service không biết gì:

```python
from src.providers.payment import PaymentProviders

def test_service_dung_cong_gia_lap():
    so = PaymentProviders()
    so.add("vnpay", CongGiaLap)
    container.override(PaymentProviders, so)
    ...
```

---

## 7. Gặp sự cố

| Triệu chứng | Nguyên nhân | Cách chữa |
|---|---|---|
| `Họ 'x' chưa có token DI` | thiếu lớp `ProviderFamily` trong `__init__.py` của họ | thêm 2 dòng, hoặc chạy lại `fam provider x <tên>` |
| `Họ 'x' chưa khai năng lực chính` | token viết `ProviderFamily` không có `[NangLuc]` | thêm tham số generic, hoặc truyền năng lực vào `require(tên, NăngLực)` |
| `Không có provider 'y' trong họ 'x'` (404) | sai tên, hoặc file chưa được quét | so tên với `names()`; file phải nằm dưới `src/providers/x/` và không bắt đầu bằng `_` |
| `Provider 'y' không hỗ trợ Z` (501) | provider không kế thừa `Z` | đúng như thiết kế — dùng `supports()` để kiểm trước, hoặc cho `y` hiện thực `Z` |
| `capabilities()` trả rỗng | interface không kế thừa `ABC` | năng lực phải là lớp con của `ABC` mới được nhận diện |
| `Họ 'x' có hai provider cùng tên` | hai file cùng khai `@provider("y")` | đổi tên một cái |
| Provider mới thêm mà không thấy | server chưa khởi động lại | `fam dev` tự reload; `fam run` thì phải restart |

---

## 8. Bảng lệnh

```bash
fam provider payment vnpay    # lần đầu: tạo cả họ
fam provider payment momo     # lần sau: chỉ thêm provider, kèm stub
fam dev
```
