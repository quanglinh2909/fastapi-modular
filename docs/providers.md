# Provider cắm được

---

## Bạn đang cần làm gì?

| Việc bạn muốn làm | Đọc mục |
|---|---|
| "Khách chọn cổng thanh toán nào thì gọi cổng đó" | [1. Nó giải quyết vấn đề gì?](#1-nó-giải-quyết-vấn-đề-gì) |
| "Làm theo từng bước, từ số 0" | [3. Làm theo](#3-làm-theo-từ-không-có-gì-tới-hai-cổng-thanh-toán) |
| "Thêm một hãng camera mới mà không sửa service" | [3. Làm theo](#3-làm-theo-từ-không-có-gì-tới-hai-cổng-thanh-toán) |
| "**Hãng này không mở được cửa, hãng kia thì được**" | [5. Khi provider không làm được hết mọi việc](#5-khi-provider-không-làm-được-hết-mọi-việc) |
| "Nên dùng cái này, hay chỉ cần `@injectable`?" | [2. Khi nào dùng, khi nào ĐỪNG](#2-khi-nào-dùng-khi-nào-đừng) |
| "File `capabilities.py` phình to quá" | [8. Viết năng lực cho dễ bảo trì](#8-viết-năng-lực-cho-dễ-bảo-trì) |
| "Sinh sẵn khung cho tôi" | [10. Bảng lệnh](#10-bảng-lệnh) — `fam provider <họ> <tên>` |
| "Gọi vào thì lỗi 404 / 501" | [9. Hỏng thì tra ở đây](#9-hỏng-thì-tra-ở-đây) |
| "Tra chữ ký API" | [6. Bảng tra API](#6-bảng-tra-api) |

---

## 1. Nó giải quyết vấn đề gì?

Bạn làm chức năng thanh toán. Công ty dùng **VNPay**. Bạn viết:

```python
@injectable
class DonHangService:
    def __init__(self, vnpay: VNPayClient) -> None:
        self._vnpay = vnpay

    async def thanh_toan(self, don: DonHang) -> str:
        return await self._vnpay.tao_giao_dich(don.so_tien, don.ma)
```

Ba tháng sau sếp bảo: *"thêm Momo, khách chọn cổng nào thì dùng cổng đó."* Bạn sửa:

```python
async def thanh_toan(self, don: DonHang) -> str:
    if don.cong == "vnpay":
        return await self._vnpay.tao_giao_dich(don.so_tien, don.ma)
    elif don.cong == "momo":
        return await self._momo.tao_thanh_toan(don.so_tien, don.ma)   # tên method khác!
    raise ValueError("không biết cổng này")
```

Rồi ZaloPay. Rồi hoàn tiền, cũng if/elif. Rồi tra cứu trạng thái, lại if/elif.

Cái sai không nằm ở `if` — nó nằm ở chỗ **service phải biết tên mọi cổng**. Thêm
một cổng là phải mở service ra sửa, ở nhiều chỗ, và dễ sót một chỗ.

**Provider lật ngược lại:** service chỉ nói *"tôi cần một thứ thanh toán được"*,
còn *thứ nào* thì quyết định lúc chạy.

```python
@injectable
class DonHangService:
    def __init__(self, payments: Providers[PaymentGateway]) -> None:
        self._payments = payments

    async def thanh_toan(self, don: DonHang) -> str:
        cong = self._payments.get(don.cong)          # "vnpay" / "momo" / "zalopay"
        return await cong.tao_giao_dich(don.so_tien, don.ma)
```

Thêm ZaloPay = **thả một file vào thư mục**. Không mở service ra nữa, mãi mãi.

---

## 2. Khi nào dùng, khi nào ĐỪNG

Dùng khi cả ba điều sau cùng đúng:

1. Có **nhiều bản hiện thực** cho cùng một việc, và
2. Chọn bản nào **chỉ biết lúc chạy** (từ DB, từ `.env`, từ người dùng chọn), và
3. Danh sách bản hiện thực **sẽ còn dài ra**.

| Tình huống | Dùng provider? |
|---|---|
| 3 cổng thanh toán, khách chọn | **Có** |
| Nhiều hãng camera: Dahua, Hik, Oryza | **Có** |
| Nhà mạng SMS đổi theo cấu hình từng môi trường | **Có** |
| Lưu file: MinIO ở prod, ổ đĩa ở máy dev | **Có** |
| Chỉ có đúng một bản hiện thực | Không — `@injectable` là đủ |
| Chọn cái nào biết ngay lúc viết code | Không — cứ nhận thẳng qua `__init__` |
| Đổi backend database | Không — `Repository[T]` đã lo |
| Bật/tắt một tính năng | Không — một cờ trong `Settings` là xong |

> **Đừng dùng cho một bản hiện thực.** Provider đánh đổi: bạn được cắm thêm dễ,
> nhưng phải viết thêm một interface. Chỉ có một bản thì đó là lỗ ròng.

---

## 3. Làm theo: từ không có gì tới hai cổng thanh toán

### Bước 1 — sinh khung

```bash
fam provider payment vnpay
```

```
Đã tạo họ 'payment' và provider 'vnpay':
    src/providers/payment/__init__.py       <- chỉ docstring, không phải sửa
    src/providers/payment/capabilities.py   <- khai việc cần làm được
    src/providers/payment/vnpay.py          <- bản hiện thực đầu tiên
```

### Bước 2 — khai VIỆC cần làm được

Mở `capabilities.py`, xoá cái mẫu, viết việc thật của bạn:

```python
# src/providers/payment/capabilities.py
from abc import ABC, abstractmethod


class PaymentGateway(ABC):
    """Việc mà MỌI cổng thanh toán đều phải làm được."""

    @abstractmethod
    async def tao_giao_dich(self, so_tien: int, ma_don: str) -> str:
        """Tạo giao dịch, trả về URL để chuyển khách sang."""
```

Đây là **hợp đồng**. Nó nói *làm được gì*, không nói *làm thế nào*.

### Bước 3 — viết bản hiện thực

```python
# src/providers/payment/vnpay.py
from fastapi_modular import provider

from src.providers.payment.capabilities import PaymentGateway


@provider("vnpay")                      # <- tên khách gửi lên chính là chuỗi này
class VnpayPayment(PaymentGateway):
    def __init__(self, settings: Settings) -> None:    # DI chạy như mọi service
        self._secret = settings.vnpay.secret

    async def tao_giao_dich(self, so_tien: int, ma_don: str) -> str:
        ...                              # gọi API VNPay ở đây
        return "https://vnpay.vn/pay?..."
```

Không phải đăng ký ở đâu cả. Đặt file vào `src/providers/payment/` là xong.

### Bước 4 — dùng trong service

```python
# src/api/don_hang/don_hang_service.py
from fastapi_modular import Providers, injectable

from src.providers.payment.capabilities import PaymentGateway


@injectable
class DonHangService:
    def __init__(self, payments: Providers[PaymentGateway]) -> None:
        self._payments = payments

    async def thanh_toan(self, don: DonHang) -> str:
        cong = self._payments.get(don.cong_thanh_toan)   # kiểu là PaymentGateway
        return await cong.tao_giao_dich(don.so_tien, don.ma)
```

Đọc `Providers[PaymentGateway]` là: *"đưa tôi cái sổ những thứ thanh toán được"*.
`get("vnpay")` là: *"lấy cho tôi cái tên vnpay trong sổ đó"*.

Vì bạn đã khai `PaymentGateway`, IDE gợi ý được `tao_giao_dich` ngay sau `cong.`.

### Bước 5 — thêm Momo (phần đáng giá)

```bash
fam provider payment momo
```

Lệnh này **đọc `capabilities.py`** và sinh sẵn stub đúng chữ ký:

```python
@provider("momo")
class MomoPayment(PaymentGateway):
    async def tao_giao_dich(self, so_tien: int, ma_don: str) -> str:
        raise NotImplementedError("MomoPayment.tao_giao_dich chưa được viết")
```

Điền thân hàm. **Xong.** `DonHangService` không phải mở ra. Không có `if` nào
phải thêm. `don.cong_thanh_toan = "momo"` là chạy.

---

## 4. Ba cái tên bạn vừa dùng

Giờ mới đặt tên, sau khi đã thấy chúng làm gì:

| Tên | Là gì | Trong ví dụ trên |
|---|---|---|
| **Họ** (family) | một loại dịch vụ cắm được = một thư mục | `payment` |
| **Năng lực** (capability) | interface nói "làm được gì" | `PaymentGateway` |
| **Provider** | một bản hiện thực cụ thể | `vnpay`, `momo` |

```
src/providers/
└── payment/                  <- HỌ
    ├── __init__.py
    ├── capabilities.py       <- NĂNG LỰC ở trong này
    ├── vnpay.py              <- PROVIDER
    └── momo.py               <- PROVIDER
```

Một dự án có nhiều họ: `payment/`, `sms/`, `device/`, `storage/`. Chúng độc lập
hoàn toàn — `"oryza"` bên `device` và `"oryza"` bên `sms` là hai thứ khác nhau.

### Tham số của `@provider`

| Tham số | Bắt buộc | Mặc định | Để làm gì |
|---|---|---|---|
| `name` | **có** | — | tên gọi lúc chạy: `payments.get("vnpay")`. Duy nhất trong MỘT họ; hai họ khác nhau được trùng tên |
| `scope` | không | `Scope.SINGLETON` | `SINGLETON` = một bản dùng lại cho mọi request. `Scope.REQUEST` = mỗi request một bản mới, khi provider giữ trạng thái riêng của request |

Provider nhận phụ thuộc qua `__init__` như mọi `@injectable` khác — settings,
`RedisClient`, repository, đều tiêm được.

```python
@provider("vnpay")
class VNPayGateway(PaymentGateway):
    def __init__(self, settings: Settings) -> None:
        self._key = settings.vnpay_key
```

---

## 5. Khi provider không làm được hết mọi việc

Đây là chỗ provider ăn đứt `if/elif`, và cũng là chỗ hay bị hiểu nhầm.

VNPay hoàn tiền được, Momo thì không. **Đừng** nhét vào một interface rồi bắt
Momo viết method rỗng. Tách ra:

```python
# capabilities.py
class PaymentGateway(ABC):          # mọi cổng đều làm được
    @abstractmethod
    async def tao_giao_dich(self, so_tien: int, ma_don: str) -> str: ...


class HoanTien(ABC):                # CHỈ vài cổng làm được
    @abstractmethod
    async def hoan_tien(self, ma: str) -> bool: ...
```

```python
class VnpayPayment(PaymentGateway, HoanTien):   # làm được cả hai
    ...

class MomoPayment(PaymentGateway):              # KHÔNG kế thừa HoanTien
    ...                                          # và không phải viết method rỗng
```

Service cần hoàn tiền thì **xin đúng cái sổ đó**:

```python
def __init__(
    self,
    payments: Providers[PaymentGateway],
    refunds: Providers[HoanTien],       # sổ riêng, chỉ chứa cổng hoàn tiền được
) -> None: ...
```

Ba điều xảy ra, đều có lợi:

```python
self._refunds.names()              # ['vnpay']  <- Momo không có trong đây
self._refunds.supports("momo")     # False      <- hỏi trước cho lành
self._refunds.get("momo")          # lỗi 501, KHÔNG phải 500
```

Lỗi 501 nói thẳng:

```
Provider 'momo' (họ 'payment') không hỗ trợ HoanTien. Nó làm được: PaymentGateway
```

**501 chứ không phải 500**, vì đây không phải bug: server hiểu yêu cầu, chỉ là
Momo không hoàn tiền được. Client đọc mã 501 là biết đừng thử lại.

Cách an toàn khi không chắc:

```python
if self._refunds.supports(ten):
    await self._refunds.get(ten).hoan_tien(ma)
else:
    raise BadRequestError(f"Cổng {ten} không hoàn tiền được")
```

---

## 6. Bảng tra API

Nhận sổ: `def __init__(self, x: Providers[NangLuc]) -> None`

| Gọi | Trả về | Ném lỗi |
|---|---|---|
| `get(tên)` | provider, **đúng kiểu năng lực đã khai** | **404** (`ProviderNotFoundError`) không có tên · **501** (`CapabilityNotSupportedError`) có tên nhưng thiếu năng lực |
| `supports(tên)` | `bool` — có làm được việc này không | **404** |
| `names()` | **chỉ** provider làm được việc này | — |
| `all_names()` | mọi provider của họ | — |
| `capabilities(tên)` | `["HoanTien", "PaymentGateway"]` | **404** |
| `describe()` | `[{"name", "capabilities"}, …]` | — |
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

### `@provider(...)`

```python
@provider(
    name,                    # str — tên khách gửi lên: "vnpay". Chữ thường, số, - hoặc _
    *,
    scope=Scope.SINGLETON,   # Scope.REQUEST nếu cần mỗi request một cái
)
```

---

## 7. Những điều cần biết

**Provider là *driver*, không phải *kết nối*.** Một `VnpayPayment` dùng chung
cho mọi đơn hàng — nó là singleton. Thông tin của từng đơn (hay từng camera)
phải đi theo **tham số của method**, đừng nhét vào `self`.

```python
async def open_door(self, thiet_bi: Device, door_id: str) -> bool: ...   # ✓
def __init__(self, thiet_bi: Device) -> None: ...                        # ✗ sai vai
```

**Họ suy ra từ vị trí file**, không phải khai tay. `src/providers/payment/vnpay.py`
thuộc họ `payment`.

**`Providers[X]` trỏ tới họ ĐỊNH NGHĨA `X`.** Hai họ cùng đặt tên một năng lực
sẽ bị chặn lúc khởi động — container tra theo tên lớp.

**Quét lúc khởi động, không lười.** File provider lỗi cú pháp thì `fam dev` chết
ngay kèm traceback, chứ không chết ở request đầu tiên chạm tới nó. Lúc boot:

```
providers.registered  package=src.providers families=['payment'] capabilities=['HoanTien', 'PaymentGateway']
```

**`src/main.py` phải gọi `register_providers()`** trước `register_routes()`.
`fam init` sinh sẵn dòng đó; `create_app()` cũng gọi hộ. Dự án cũ lắp tay thì tự
thêm — quên là gặp lỗi ở [mục 9](#9-hỏng-thì-tra-ở-đây).

**Không có `src/providers/` thì bỏ qua**, không lỗi. Dự án không dùng provider
không phải tạo thư mục rỗng.

**Đổi chỗ đặt** nếu bạn xếp khác:

```python
register_providers("cong_ty.plugins")                     # trong src/main.py
create_app(AppSettings(), providers_package="cong_ty.plugins")
```

---

## 8. Viết năng lực cho dễ bảo trì

### Khi nào tách `capabilities.py` ra nhiều file

Một file cho tới khi nó phình. Ngưỡng thực tế: **quá một màn hình, hoặc từ 3
năng lực trở lên**.

Bộ quét tìm năng lực ở **mọi module trong thư mục họ**, kể cả package con. Nên
tách kiểu gì cũng chạy, **không phải khai gì thêm**. Ba cách, chọn theo số file:

#### Cách 1 — phẳng: mỗi năng lực một file, để ngay trong thư mục họ

Ít việc nhất. Không thêm thư mục, không thêm `__init__.py` nào.

```
src/providers/device/
├── __init__.py
├── door_management.py        <- một năng lực
├── camera_management.py      <- một năng lực
├── dahua.py                  <- provider
└── hik.py                    <- provider
```

```python
from src.providers.device.door_management import DoorManagement
```

Hợp khi **nhiều năng lực nhưng ít provider**. Nhiều cả hai thì thư mục bắt đầu
lẫn lộn, chuyển sang cách 2.

#### Cách 2 — gom vào thư mục `capabilities/`, `__init__.py` ĐỂ RỖNG

```
src/providers/device/
├── __init__.py
├── capabilities/
│   ├── __init__.py           <- rỗng, không phải viết gì
│   ├── door_management.py
│   └── camera_management.py
├── dahua.py
└── hik.py
```

```python
from src.providers.device.capabilities.door_management import DoorManagement
```

Tách bạch năng lực với bản hiện thực, mà vẫn không có dòng nào phải bảo trì.
Đổi lại: đường import dài hơn.

#### Cách 3 — thêm re-export, CHỈ khi bạn cần đường import ngắn

```python
# capabilities/__init__.py
from src.providers.device.capabilities.camera_management import CameraManagement
from src.providers.device.capabilities.door_management import DoorManagement

__all__ = ["CameraManagement", "DoorManagement"]
```

```python
from src.providers.device.capabilities import DoorManagement    # ngắn lại
```

Khối này **hoàn toàn tuỳ chọn** — nó không phục vụ khung, chỉ phục vụ người đọc
import. Đáng viết khi đã có nhiều chỗ import theo đường ngắn và bạn không muốn
sửa hết, hoặc khi bạn muốn tự do đổi tên file mà không ai phải sửa import.

Đừng lo re-export làm năng lực bị đếm hai lần: chỉ class **định nghĩa** ở một
module mới được tính.

### Ba nguyên tắc

**1. Đặt tên theo *việc làm được*, không theo *hãng*.**

```python
class DoorManagement(ABC): ...      # ✓ cắt ngang mọi hãng
class DahuaInterface(ABC): ...      # ✗ chỉ là class thường, không phải năng lực
```

**2. Nhỏ tới mức provider nào hiện thực nó cũng hiện thực TRỌN.**

Dấu hiệu quá to: có provider phải viết method rỗng chỉ để thoả ABC. Tách đôi.
Đó chính là lý do `get()` trả 501 — để bạn **không cần** method rỗng.

**3. Docstring nói HỢP ĐỒNG, không nói cách làm.**

```python
@abstractmethod
async def open_door(self, door_id: str) -> bool:
    """Mở cửa `door_id`. True nếu thiết bị xác nhận đã mở.

    Ném NotFoundError nếu không có cửa đó. Gọi nhiều lần vô hại.
    """
```

**Đừng để kiểu riêng của một hãng lọt vào chữ ký.** Năng lực là hợp đồng chung,
nên tham số phải là thứ mọi hãng đều diễn đạt được:

```python
async def snapshot(self, cam_id: str) -> bytes: ...             # ✓
async def snapshot(self, cam: DahuaHandle) -> bytes: ...        # ✗ hãng khác lấy đâu ra
```

---

## 9. Hỏng thì tra ở đây

| Triệu chứng | Nguyên nhân | Cách chữa |
|---|---|---|
| `Chưa ai dựng sổ provider` | quên `register_providers()` | gọi nó trong `src/main.py` **trước** `register_routes()`, hoặc dùng `create_app()` |
| `Không có provider 'y' trong họ 'x'` (404) | sai tên, hoặc file chưa được quét | so với `all_names()`; file phải nằm dưới `src/providers/x/` và **không** bắt đầu bằng `_` |
| `Provider 'y' không hỗ trợ Z` (501) | provider không kế thừa `Z` | đúng như thiết kế — hỏi `supports()` trước, hoặc cho `y` hiện thực `Z` |
| `Hai họ cùng khai năng lực tên 'X'` | hai `capabilities.py` trùng tên lớp | đổi tên một cái |
| `Họ 'x' có hai provider cùng tên 'y'` | hai file cùng `@provider("y")` | đổi tên một cái |
| IDE không gợi ý method | khai sai năng lực ở `__init__` | năng lực nào có method đó thì khai đúng cái ấy |
| `names()` không thấy provider mới | nó không kế thừa năng lực của sổ đó | kiểm bằng `capabilities("tên")` |
| Provider mới thêm mà không thấy | server chưa khởi động lại | `fam dev` tự reload; `fam run` phải restart |

---

## 10. Bảng lệnh

```bash
fam provider payment vnpay    # lần đầu: tạo cả họ
fam provider payment momo     # lần sau: chỉ thêm provider, kèm stub
fam dev
```
