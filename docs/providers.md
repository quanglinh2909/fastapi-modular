# Provider cắm được

Chọn **bản hiện thực bằng tên lúc chạy**: cổng thanh toán lấy từ cột trong đơn
hàng, nhà mạng SMS lấy từ cấu hình, hãng camera lấy từ bản ghi thiết bị.

```
DonHangService ──── get("vnpay") ────▶ VnpayPayment
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

**`__init__.py`** — chỉ một docstring. Không có gì phải bảo trì:

```python
"""Họ provider **payment** — các bản hiện thực cắm được.

Service khai ĐÚNG năng lực nó cần:

    def __init__(self, x: Providers[PaymentGateway]) -> None: ...
"""
```

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
> thân rỗng. Khi đó `Providers[HoanTien].get("momo")` trả lỗi **501** nói rõ thiếu gì,
> thay vì một `NotImplementedError` 500 khó hiểu.

---

## 3. Dùng trong service

Khai **đúng năng lực bạn cần** — cùng khuôn với `Repository[User]`:

```python
from fastapi_modular import Providers, injectable

from src.providers.payment.capabilities import PaymentGateway


@injectable
class DonHangService:
    def __init__(self, payments: Providers[PaymentGateway]) -> None:
        self._payments = payments

    async def thanh_toan(self, don: DonHang) -> str:
        # tên cổng lấy từ DB — service không biết trước là cổng nào
        cong = self._payments.get(don.cong_thanh_toan)      # -> PaymentGateway
        return await cong.tao_giao_dich(don.so_tien, don.ma)
```

`get()` trả về **đúng kiểu năng lực bạn khai**, nên IDE gợi ý được method ngay.

**Thêm ZaloPay = thả một file `zalopay.py` vào thư mục.** Không sửa service,
không sửa `main.py`, không có danh sách import nào phải bảo trì.

### API của sổ

| Gọi | Trả về | Ném lỗi |
|---|---|---|
| `get(tên)` | provider, kiểu tĩnh là **chính năng lực đã khai** | **404** không có tên · **501** có tên nhưng thiếu năng lực |
| `supports(tên)` | `bool` — có làm được việc này không | **404** |
| `names()` | **chỉ** provider làm được việc này | — |
| `all_names()` | mọi provider của họ | — |
| `capabilities(tên)` | `["HoanTien", "PaymentGateway"]` | **404** |
| `describe()` | `[{"name", "capabilities"}, …]` cho `names()` | — |
| `family` · `capability` | tên họ · lớp năng lực | — |

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

### Cần hai năng lực thì nhận hai sổ

```python
def __init__(
    self,
    payments: Providers[PaymentGateway],
    refunds: Providers[HoanTien],
) -> None: ...
```

Đọc `__init__` là biết ngay service này đụng vào những gì — không có năng lực
nào lẩn trong thân hàm.

`names()` cũng vì thế mà có nghĩa: `refunds.names()` là **các cổng hoàn tiền
được**, không phải mọi cổng thanh toán. Không chắc thì hỏi trước:

```python
if self._refunds.supports(ten):
    await self._refunds.get(ten).hoan_tien(ma)
```

---

## 4. Viết năng lực cho dễ bảo trì

### Khi nào tách `capabilities.py` ra nhiều file

Một file cho tới khi nó phình. Ngưỡng thực tế: **quá một màn hình, hoặc từ 3
năng lực trở lên**.

Điều khiến quyết định này rẻ: **đường import không đổi**.

```python
from src.providers.device.capabilities import DoorManagement   # trước VÀ sau khi tách
```

Nên cứ bắt đầu bằng một file, tách khi thấy vướng — không phải sửa provider nào:

```
src/providers/device/
├── __init__.py
├── capabilities/
│   ├── __init__.py          # re-export, giữ nguyên đường import
│   ├── camera_management.py
│   ├── door_management.py
│   └── person_management.py
├── dahua.py
└── hik.py
```

```python
# capabilities/__init__.py
from src.providers.device.capabilities.camera_management import CameraManagement
from src.providers.device.capabilities.door_management import DoorManagement
from src.providers.device.capabilities.person_management import PersonManagement

__all__ = ["CameraManagement", "DoorManagement", "PersonManagement"]
```

Bộ quét đi vào cả package con, nên **không phải khai gì thêm**. Việc re-export ở
`__init__.py` cũng không làm năng lực bị đếm hai lần — chỉ class **định nghĩa**
ở một module mới được tính.

### Ba nguyên tắc

**1. Đặt tên theo *việc làm được*, không theo *loại thiết bị*.**

```python
class DoorManagement(ABC): ...      # ✓ cắt ngang mọi hãng
class DahuaInterface(ABC): ...      # ✗ chỉ là một class thường, không phải năng lực
```

Năng lực phải là thứ nhiều hãng cùng làm được. Buộc vào một hãng thì nó mất hết
tác dụng.

**2. Nhỏ tới mức provider nào hiện thực nó cũng hiện thực TRỌN.**

Dấu hiệu năng lực quá to: có provider phải viết method rỗng, hoặc
`raise NotImplementedError` chỉ để thoả ABC. Lúc đó tách đôi.

```python
# ✗ một interface to: Hik buộc phải viết open_door rỗng
class DeviceInterface(ABC):
    async def snapshot(self, cam_id: str) -> bytes: ...
    async def open_door(self, door_id: str) -> bool: ...

# ✓ tách: Hik chỉ kế thừa cái nó làm được
class CameraManagement(ABC): ...
class DoorManagement(ABC): ...
```

Đây chính là lý do `get()` trả **501**: để bạn **không cần** method rỗng. Thiếu
năng lực là chuyện bình thường, và khung nói ra hộ bạn.

**3. Docstring nói HỢP ĐỒNG, không nói cách làm.**

Trả gì, ném lỗi gì, gọi nhiều lần có sao không. Cách làm là việc của provider.

```python
class DoorManagement(ABC):
    """Mở/đóng cửa. Thiết bị chỉ có camera thì ĐỪNG hiện thực."""

    @abstractmethod
    async def open_door(self, door_id: str) -> bool:
        """Mở cửa `door_id`. True nếu thiết bị xác nhận đã mở.

        Ném NotFoundError nếu không có cửa đó. Gọi nhiều lần vô hại.
        """
```

### Đừng để kiểu riêng của một hãng lọt vào năng lực

Năng lực là hợp đồng chung, nên tham số và giá trị trả về phải là thứ mọi hãng
đều diễn đạt được — kiểu dựng sẵn, DTO của bạn, hoặc enum bạn định nghĩa.

```python
async def snapshot(self, cam_id: str) -> bytes: ...          # ✓
async def snapshot(self, cam: DahuaCameraHandle) -> bytes: ...  # ✗ hãng khác lấy đâu ra
```

---

## 5. Những điều cần biết

**Họ suy ra từ vị trí file.** `src/providers/payment/vnpay.py` thuộc họ
`payment`. Không phải khai lại tên họ trong `@provider`.

**`Providers[X]` trỏ tới họ ĐỊNH NGHĨA `X`.** Năng lực khai ở
`src/providers/payment/capabilities.py` thì sổ của nó chứa provider của họ
`payment`. Hai họ cùng đặt tên một năng lực sẽ bị chặn lúc khởi động — container
tra theo tên lớp.

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

## 6. Đổi chỗ đặt

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

## 7. Test

Provider là class thuần, test thẳng không cần gì:

```python
def test_vnpay_tao_duoc_giao_dich():
    assert VnpayPayment(settings).tao_giao_dich(10_000, "DH1")
```

Đổi cả sổ trong test — service không biết gì:

```python
from fastapi_modular import Providers

def test_service_dung_cong_gia_lap():
    so = Providers("payment", PaymentGateway, {"vnpay": CongGiaLap})
    container.override("Providers[PaymentGateway]", so)
    ...
```

---

## 8. Gặp sự cố

| Triệu chứng | Nguyên nhân | Cách chữa |
|---|---|---|
| `Chưa ai dựng sổ provider` | quên `register_providers()` trong `src/main.py` | gọi nó TRƯỚC `register_routes()`, hoặc dùng `create_app()` |
| `Hai họ cùng khai năng lực tên 'X'` | hai `capabilities.py` đặt trùng tên lớp | đổi tên một cái |
| `Không có provider 'y' trong họ 'x'` (404) | sai tên, hoặc file chưa được quét | so tên với `names()`; file phải nằm dưới `src/providers/x/` và không bắt đầu bằng `_` |
| `Provider 'y' không hỗ trợ Z` (501) | provider không kế thừa `Z` | đúng như thiết kế — dùng `supports()` để kiểm trước, hoặc cho `y` hiện thực `Z` |
| `capabilities()` trả rỗng | interface không kế thừa `ABC` | năng lực phải là lớp con của `ABC` mới được nhận diện |
| `Họ 'x' có hai provider cùng tên` | hai file cùng khai `@provider("y")` | đổi tên một cái |
| Provider mới thêm mà không thấy | server chưa khởi động lại | `fam dev` tự reload; `fam run` thì phải restart |

---

## 9. Bảng lệnh

```bash
fam provider payment vnpay    # lần đầu: tạo cả họ
fam provider payment momo     # lần sau: chỉ thêm provider, kèm stub
fam dev
```
