"""`emit` và `send` — khuôn tin tương thích NestJS, dùng chung cho mọi hạ tầng.

Thư viện này vốn chỉ có một chiều: `publish` bắn tin đi rồi quên. Đây là chiều
còn lại — gửi tin rồi **chờ trả lời** — và làm đúng theo cách NestJS làm, để một
service Python viết bằng khung này nói chuyện được với một service NestJS mà
không cần lớp dịch nào ở giữa.

Ba việc, đúng như NestJS:

    broker.emit(pattern, data)      # ~ client.emit()      bắn đi, không chờ
    await broker.send(pattern, data)  # ~ client.send()    chờ trả lời
    @rabbitmq_responder(pattern)   # ~ @MessagePattern()   bên trả lời

(`publish()` cũ vẫn nguyên: nó gửi payload THÔ, khuôn riêng của thư viện này.
`emit()` gửi đúng khuôn NestJS. Xem docs/rpc.md để biết khi nào dùng cái nào.)

## Khuôn tin

Lấy từ mã nguồn `@nestjs/microservices@11.2.1`, không phải từ trí nhớ:

    gửi (send)   {"pattern": "sum", "data": [1,2,3], "id": "abc123"}
    gửi (emit)   {"pattern": "sum", "data": [1,2,3]}        <- KHÔNG có "id"
    trả lời      {"id": "abc123", "response": 6, "isDisposed": true}
    trả lỗi      {"id": "abc123", "err": "…", "isDisposed": true, "status": "error"}

Chính `id` phân biệt hai loại: NestJS coi tin **không có `id`** là sự kiện
(`@EventPattern`), có `id` là yêu cầu cần trả lời (`@MessagePattern`).

Chỗ mang cặp (địa chỉ trả lời, mã đối chiếu) thì mỗi hạ tầng một khác, và đó là
việc của từng package hạ tầng:

    RabbitMQ   thuộc tính AMQP `reply_to` + `correlation_id`, trả về hàng đợi
               `amq.rabbitmq.reply-to` (direct reply-to, không phải dọn dẹp gì)
    MQTT       topic trả lời là `<pattern>/reply`, mã nằm trong `id` của gói
    Redis      kênh trả lời là `<pattern>.reply`, mã nằm trong `id` của gói
    Kafka      topic trả lời `<pattern>.reply`, mã nằm trong HEADER
               `kafka_correlationId` — Kafka là hạ tầng DUY NHẤT mà NestJS
               không gói `{pattern,data,id}`, nó gửi thẳng data ở value.

File này KHÔNG import gì từ `infrastructure/` — bốn package hạ tầng vẫn không
biết nhau, chúng chỉ cùng biết chỗ này.

## Nghĩ kỹ trước khi dùng `send`

`send` biến hàng đợi thành lời gọi hàm qua mạng, và kéo theo đúng những thứ mà
hàng đợi vốn dựng lên để tránh:

- Bên kia chết thì bên này **treo** tới lúc hết giờ, thay vì tin nằm chờ trong
  hàng đợi cho tới khi bên kia sống lại.
- Độ trễ **cộng dồn** qua từng chặng, và một chặng chậm kéo cả chuỗi chậm theo.
- Hết giờ **không** có nghĩa là việc chưa làm. Rất có thể bên kia đã làm xong và
  chỉ câu trả lời bị lạc — nên việc bên kia làm phải **lặp lại được**.

Cần "làm rồi báo tôi sau" thì `emit`/`publish` vẫn đúng hơn. `send` dành cho lúc
người dùng đang đứng chờ kết quả: tra cứu, kiểm tra, tính toán.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi_modular.core.exceptions import AppError
from fastapi_modular.core.logging import get_logger

log = get_logger(__name__)

#: Chờ bao lâu rồi bỏ cuộc. Cố ý NGẮN: người gọi đang đứng chờ, và một lời gọi
#: treo lâu còn tệ hơn một lỗi trả về nhanh.
DEFAULT_RPC_TIMEOUT = 5.0

#: Nguyên văn của NestJS khi không có handler nào khớp pattern. Giữ đúng chữ để
#: một client NestJS gọi sang đây nhận được thông báo nó vốn đã biết cách đọc.
NO_MESSAGE_HANDLER = "There is no matching message handler defined in the remote service."

#: Hàng đợi trả lời có sẵn của RabbitMQ. NestJS dùng đúng cái này làm mặc định.
RMQ_REPLY_QUEUE = "amq.rabbitmq.reply-to"

#: Tên header của Kafka, theo `KafkaHeaders` của NestJS (vốn theo Spring Kafka).
KAFKA_CORRELATION_ID = "kafka_correlationId"
KAFKA_REPLY_TOPIC = "kafka_replyTopic"
KAFKA_REPLY_PARTITION = "kafka_replyPartition"
KAFKA_NEST_ERR = "kafka_nest-err"
KAFKA_NEST_IS_DISPOSED = "kafka_nest-is-disposed"

_MAX_DEPTH = 5
_MAX_KEYS = 20


class RpcTimeoutError(AppError):
    """Hết giờ chờ trả lời.

    KHÔNG có nghĩa là bên kia chưa làm gì — rất có thể nó đã làm xong và chỉ
    câu trả lời bị lạc. Đừng tự động thử lại một việc không lặp lại được.
    """

    status_code = 504


class RpcRemoteError(AppError):
    """Bên kia nhận được tin, chạy handler, và handler ném lỗi.

    Khác hẳn hết giờ: ở đây ta BIẾT việc đã hỏng, và biết hỏng vì cái gì.
    """

    status_code = 502


# ------------------------------------------------------------------ pattern
def normalize_pattern(pattern: Any) -> str:
    """Đổi pattern thành chuỗi ĐÚNG như NestJS làm, kể cả pattern dạng object.

    NestJS cho phép `@MessagePattern({ cmd: 'sum' })`, và chuỗi hoá nó bằng một
    hàm riêng chứ không phải `JSON.stringify`: **khoá được sắp xếp** trước khi
    ghép. Bỏ sót chi tiết đó thì `{"cmd":"sum","v":1}` và `{"v":1,"cmd":"sum"}`
    ra hai chuỗi khác nhau, và một trong hai bên sẽ không tìm thấy handler.

    Nguồn: `utils/transform-pattern.utils.js` của @nestjs/microservices.
    """
    return _to_route(pattern, 0)


def _to_route(pattern: Any, depth: int) -> str:
    if isinstance(pattern, str):
        return pattern
    if isinstance(pattern, bool):
        # Phải đứng trước `int`: trong Python bool LÀ int, mà NestJS in ra
        # "true"/"false" chứ không phải "1"/"0".
        return "true" if pattern else "false"
    if isinstance(pattern, (int, float)):
        return f"{pattern}"
    if not isinstance(pattern, dict):
        return str(pattern)
    if depth > _MAX_DEPTH:
        return "[MAX_DEPTH_REACHED]"
    if len(pattern) > _MAX_KEYS:
        return "[TOO_MANY_KEYS]"

    phan = []
    for khoa in sorted(pattern, key=lambda k: _collate(str(k))):
        gia_tri = pattern[khoa]
        ve_phai = (
            f'"{_escape(_to_route(gia_tri, depth + 1))}"'
            if isinstance(gia_tri, str)
            else _to_route(gia_tri, depth + 1)
        )
        phan.append(f'"{_escape(str(khoa))}":{ve_phai}')
    return "{" + ",".join(phan) + "}"


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


#: Thứ tự sắp xếp ký tự ASCII của ICU, lấy nguyên từ V8:
#:
#:     [...Array(95)].map((_,i)=>String.fromCharCode(i+32)).sort((a,b)=>a.localeCompare(b))
#:
#: Cần bảng này vì NestJS sắp khoá bằng `localeCompare` chứ không phải so mã ký
#: tự. Hai luật khác hẳn nhau: `localeCompare` xếp "a" TRƯỚC "M", còn so mã ký
#: tự xếp "M" trước "a" (77 < 97). Dùng nhầm luật thì `{"z":1,"a":2,"M":3}` ra
#: hai chuỗi khác nhau ở hai bên, và bên NestJS đơn giản là không tìm thấy
#: handler — không có lỗi nào được ném ra để mà lần.
_ICU_ASCII = (
    " _-,;:!?.'\"()[]{}@*/\\&#%`^+<=>|~$0123456789"
    "aAbBcCdDeEfFgGhHiIjJkKlLmMnNoOpPqQrRsStTuUvVwWxXyYzZ"
)

#: ký tự -> (trọng số CHỮ, trọng số HOA/THƯỜNG). "a" và "A" cùng trọng số chữ.
_TRONG_SO: dict[str, tuple[int, int]] = {}
for _i, _ch in enumerate(_ICU_ASCII):
    if _ch.isalpha():
        # Cặp aA, bB... nằm liền nhau: cùng bậc chữ, khác bậc hoa/thường.
        _bac = len(_ICU_ASCII) + (ord(_ch.lower()) - ord("a"))
        _TRONG_SO[_ch] = (_bac, 1 if _ch.isupper() else 0)
    else:
        _TRONG_SO[_ch] = (_i, 0)
del _i, _ch


def _collate(s: str) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Khoá sắp xếp mô phỏng `String.prototype.localeCompare` cho khoá ASCII.

    So theo TẦNG như ICU chứ không so từng ký tự một: hết tầng chữ rồi mới tới
    tầng hoa/thường. Khác biệt lộ ra ở những cặp như "Ab" và "aC" — so từng ký
    tự sẽ xếp "aC" trước, ICU xếp "Ab" trước vì b < c ở tầng chữ.

    Ký tự ngoài ASCII in được thì xếp sau cùng theo mã — chỗ này KHÔNG khớp
    ICU. Khoá pattern của NestJS trên thực tế là tên thuộc tính JavaScript nên
    gần như luôn thuần ASCII; muốn khớp tuyệt đối cho mọi chữ Unicode thì phải
    kéo cả bảng đối chiếu ICU vào, cái giá không đáng cho phần được thêm.
    """
    ngoai_bang = len(_ICU_ASCII) * 4
    chu: list[int] = []
    hoa: list[int] = []
    for ch in s:
        bac = _TRONG_SO.get(ch)
        if bac is None:
            chu.append(ngoai_bang + ord(ch))
            hoa.append(0)
        else:
            chu.append(bac[0])
            hoa.append(bac[1])
    return tuple(chu), tuple(hoa)


# ------------------------------------------------------------------ gói tin
def request_packet(pattern: Any, data: Any, correlation_id: str) -> dict[str, Any]:
    """Gói `send` — có `id`, nên bên kia biết là phải trả lời."""
    return {"pattern": normalize_pattern(pattern), "data": data, "id": correlation_id}


def event_packet(pattern: Any, data: Any) -> dict[str, Any]:
    """Gói `emit` — KHÔNG có `id`, nên bên kia biết là không phải trả lời."""
    return {"pattern": normalize_pattern(pattern), "data": data}


def read_packet(raw: Any) -> tuple[str, Any, str | None] | None:
    """Đọc gói NestJS. Trả `(pattern, data, id | None)`, hoặc None nếu không phải.

    Phép thử cố ý CHẶT: đúng khoá `pattern` + `data` (+ `id`), không thừa khoá
    nào. Payload nghiệp vụ vô tình có hai trường tên `pattern` và `data` là
    chuyện hiếm nhưng có thật, và đoán sai ở đây thì handler nhận nhầm nửa dữ
    liệu mà không ai báo gì.
    """
    if not isinstance(raw, dict) or "pattern" not in raw or "data" not in raw:
        return None
    if not set(raw).issubset({"pattern", "data", "id"}):
        return None
    pattern = raw["pattern"]
    if not isinstance(pattern, (str, dict, int, float)):
        return None
    ma = raw.get("id")
    if ma is not None and not isinstance(ma, str):
        return None
    return normalize_pattern(pattern), raw["data"], ma


def ok_packet(correlation_id: str | None, data: Any) -> dict[str, Any]:
    """Gói trả lời thành công.

    Gộp `response` và `isDisposed` vào MỘT gói, đúng như NestJS làm: hàm
    `send()` ở `server/server.js` gộp cờ kết thúc vào gói dữ liệu cuối cùng
    thay vì gửi thêm một gói rỗng.
    """
    goi: dict[str, Any] = {"response": data, "isDisposed": True}
    if correlation_id is not None:
        goi["id"] = correlation_id
    return goi


def error_packet(correlation_id: str | None, loi: BaseException | str) -> dict[str, Any]:
    """Gói trả lời lỗi.

    Gửi lỗi về chứ không im lặng: im lặng thì người gọi phải đợi hết `timeout`
    rồi nhận một thông báo hết giờ vô nghĩa, trong khi ta đã biết chính xác
    hỏng cái gì ngay từ giây đầu.

    Chỉ gửi thông điệp, KHÔNG gửi traceback — traceback thuộc về log của bên xử
    lý, không phải thứ để đẩy qua mạng cho bên gọi đọc.
    """
    if isinstance(loi, str):
        mo_ta = loi
    else:
        mo_ta = f"{type(loi).__name__}: {loi}" if str(loi) else type(loi).__name__
    goi: dict[str, Any] = {"err": mo_ta, "isDisposed": True, "status": "error"}
    if correlation_id is not None:
        goi["id"] = correlation_id
    return goi


def read_reply(raw: Any, *, nguon: str) -> Any:
    """Mở gói trả lời. Ném RpcRemoteError nếu bên kia báo hỏng.

    Gói không mang khoá nào của NestJS (`err`/`response`/`isDisposed`) thì coi
    NGUYÊN gói là câu trả lời. NestJS cũng xử đúng như vậy
    (`IncomingResponseDeserializer.isExternal`), nhờ đó gọi sang được cả những
    dịch vụ không dùng NestJS lẫn khung này.
    """
    if not isinstance(raw, dict):
        return raw
    if not ({"err", "response", "isDisposed"} & set(raw)):
        return raw
    if raw.get("err"):
        raise RpcRemoteError(f"'{nguon}' báo lỗi: {_mo_ta_loi(raw['err'])}")
    return raw.get("response")


def _mo_ta_loi(err: Any) -> str:
    """NestJS gửi `err` khi thì là chuỗi, khi thì là object.

    Handler ném `RpcException` thì `err` là nguyên đối tượng lỗi
    (`{"status": "error", "message": "..."}`); lỗi không bắt được thì NestJS
    thay bằng "Internal server error". In thẳng cái dict ra là ném nguyên cấu
    trúc JSON vào mặt người đọc log, nên lấy phần đọc được nếu có.
    """
    if isinstance(err, dict):
        for khoa in ("message", "error", "err"):
            gia_tri = err.get(khoa)
            if isinstance(gia_tri, str) and gia_tri:
                return gia_tri
        return json.dumps(err, ensure_ascii=False)
    return str(err)


def encode(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, default=str).encode()


def decode(raw: Any) -> Any:
    """Đọc JSON, chấp nhận cả chuỗi thuần (thiết bị MQTT hay gửi "ON", "23.5")."""
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def new_correlation_id() -> str:
    return uuid.uuid4().hex


# ------------------------------------------------------------------ sổ chờ
class PendingReplies:
    """Sổ chờ: mã đối chiếu -> chỗ ngồi đợi câu trả lời.

    Mỗi hạ tầng giữ một cuốn. Vòng đọc của hạ tầng nhặt được tin trả lời thì gọi
    `deliver()`; người đang treo ở `wait()` tỉnh dậy.
    """

    __slots__ = ("_cho", "_ten")

    def __init__(self, ten: str) -> None:
        self._ten = ten
        self._cho: dict[str, asyncio.Future[Any]] = {}

    def __len__(self) -> int:
        return len(self._cho)

    def open(self) -> tuple[str, asyncio.Future[Any]]:
        """Giữ chỗ TRƯỚC khi gửi tin đi.

        Thứ tự này quan trọng: bên kia có thể trả lời xong trước khi lệnh gửi
        của ta kịp trả về. Giữ chỗ sau khi gửi là tự tạo một cuộc đua mà phần
        thua là câu trả lời rơi vào hư không, và triệu chứng thì trông hệt như
        "bên kia không trả lời".
        """
        ma = new_correlation_id()
        self._cho[ma] = asyncio.get_running_loop().create_future()
        return ma, self._cho[ma]

    def deliver(self, ma: str, goi: Any) -> bool:
        """Giao câu trả lời. False nếu không ai đợi mã này."""
        future = self._cho.pop(ma, None)
        if future is None or future.done():
            # Tới muộn sau khi người gọi đã bỏ cuộc, hoặc là câu trả lời dành
            # cho tiến trình khác (Kafka phát mọi câu trả lời cho mọi instance).
            return False
        future.set_result(goi)
        return True

    async def wait(self, ma: str, future: asyncio.Future[Any], timeout: float, *, dich: str) -> Any:
        """Treo tới khi có trả lời, hoặc ném RpcTimeoutError."""
        try:
            goi = await asyncio.wait_for(future, timeout)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise RpcTimeoutError(
                f"{self._ten}: chờ {timeout}s mà '{dich}' không trả lời. Bên kia có thể "
                "đang chết, đang chậm, hoặc không có ai nghe địa chỉ này. Lưu ý: hết giờ "
                "KHÔNG bảo đảm bên kia chưa làm gì."
            ) from exc
        finally:
            # Dọn cả khi hết giờ lẫn khi bị huỷ: sổ chờ không được phình lên
            # theo số lời gọi hỏng.
            self._cho.pop(ma, None)
        return read_reply(goi, nguon=dich)

    def fail_all(self, ly_do: str) -> None:
        """Đứt kết nối: đánh thức mọi người đang đợi thay vì để họ treo hết giờ.

        Không có bước này thì mỗi lần rớt mạng là một loạt lời gọi đứng đủ
        `timeout` giây — dù ta đã biết chắc câu trả lời không bao giờ tới.
        """
        cho, self._cho = self._cho, {}
        for future in cho.values():
            if not future.done():
                future.set_exception(
                    RpcTimeoutError(f"{self._ten}: mất kết nối khi đang chờ trả lời ({ly_do})")
                )

    def cancel_all(self) -> None:
        """Tắt app: huỷ lặng lẽ, không dựng thêm lỗi cho ai phải đọc."""
        cho, self._cho = self._cho, {}
        for future in cho.values():
            if not future.done():
                future.cancel()


async def send_reply(gui: Any, *, correlation_id: str | None, dia_chi: str, nhan: str,
                     ket_qua: Any = None, loi: BaseException | str | None = None) -> None:
    """Gửi câu trả lời về, và NUỐT mọi lỗi phát sinh khi gửi.

    `gui` là một coroutine function `(dia_chi, correlation_id, goi)`.

    Vì sao nuốt: tới đây handler đã chạy xong và việc đã làm xong. Ném tiếp sẽ
    khiến hạ tầng coi như tin xử lý hỏng rồi **thử lại cả việc** — làm hai lần
    một việc chỉ vì đường về bị nghẽn.
    """
    goi = error_packet(correlation_id, loi) if loi is not None else ok_packet(correlation_id, ket_qua)
    try:
        await gui(dia_chi, correlation_id, goi)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - việc đã xong, đường về hỏng không đáng làm lại
        log.warning(
            "rpc.reply_failed",
            handler=nhan,
            reply_to=dia_chi,
            error=f"{type(exc).__name__}: {exc}",
        )
