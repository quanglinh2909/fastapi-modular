"""Đọc biểu thức cron 5 trường và tính lần chạy kế tiếp.

Tự viết thay vì kéo `croniter` vào: năm trường của cron là một đặc tả đóng và
nhỏ, còn thêm một phụ thuộc bắt buộc vào một khung web thì không.

    ┌─ phút (0-59)
    │ ┌─ giờ (0-23)
    │ │ ┌─ ngày trong tháng (1-31)
    │ │ │ ┌─ tháng (1-12)
    │ │ │ │ ┌─ thứ (0-6, 0 = Chủ nhật; 7 cũng là Chủ nhật)
    │ │ │ │ │
    * * * * *

Mỗi trường nhận: `*`, một số, `a-b`, `a,b,c`, `*/n`, `a-b/n`. Có thêm mấy lối
tắt quen thuộc: `@hourly`, `@daily`, `@weekly`, `@monthly`, `@yearly`.

## Hai chỗ cron hay làm người ta ngã

**Ngày-trong-tháng và THỨ có khi nối bằng HOẶC, có khi bằng VÀ.** Luật của cron
gốc, và nó phản trực giác: `0 0 1 * 1` chạy vào **ngày 1 hàng tháng HOẶC mọi thứ
Hai**, chứ không phải "ngày 1 nếu hôm đó là thứ Hai". Chọn HOẶC hay VÀ tuỳ vào
có trường nào **bắt đầu bằng `*`** hay không — xem `_day_matches`.

**Múi giờ.** `0 3 * * *` là 3 giờ sáng — nhưng ở múi nào? Mặc định ở đây là
UTC, tức 10 giờ sáng giờ Việt Nam. Truyền `timezone="Asia/Ho_Chi_Minh"` nếu ý
bạn là 3 giờ sáng giờ ta. Khung in ra lần chạy kế tiếp ở CẢ HAI múi lúc khởi
động, để sai lệch lộ ra ngay chứ không đợi tới lúc nửa đêm.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi_modular.core.exceptions import BadRequestError

SHORTCUTS = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}

_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_FIELD_NAMES = ("phút", "giờ", "ngày", "tháng", "thứ")

#: Trần khi dò lần chạy kế tiếp. Một biểu thức hợp lệ về cú pháp vẫn có thể
#: không bao giờ xảy ra (`0 0 30 2 *` — 30 tháng Hai). Không có trần thì vòng
#: dò chạy mãi và treo cả tiến trình lúc khởi động.
MAX_LOOKAHEAD_YEARS = 5


@dataclass(slots=True, frozen=True)
class CronExpression:
    """Một biểu thức cron đã phân tích xong, dùng lại được nhiều lần."""

    source: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    day_star: bool
    weekday_star: bool

    def matches(self, moment: datetime) -> bool:
        if moment.minute not in self.minutes or moment.hour not in self.hours:
            return False
        if moment.month not in self.months:
            return False
        return self._day_matches(moment)

    def _day_matches(self, moment: datetime) -> bool:
        """Luật ngày của cron gốc, chỗ phản trực giác nhất trong cả đặc tả.

        CẢ HAI tập (ngày-trong-tháng và thứ) luôn được kiểm. Thứ duy nhất mà
        ký tự `*` ở đầu trường quyết định là **nối chúng bằng VÀ hay HOẶC**:

            có ít nhất một trường bắt đầu bằng '*'  ->  VÀ
            không trường nào bắt đầu bằng '*'       ->  HOẶC

        Nhờ vậy `0 0 1 * 1` chạy ngày 1 hàng tháng HOẶC mọi thứ Hai (không
        trường nào có '*'), còn `0 0 */7 * 1` chạy các ngày 1,8,15,22,29 VÀ
        phải đúng thứ Hai. Nguồn: `cron.c` của Vixie cron.
        """
        # `weekday()` của Python: thứ Hai = 0. Cron: Chủ nhật = 0. Lệch một
        # nhịp ở đây là mọi lịch "chạy thứ Hai" âm thầm chạy vào Chủ nhật.
        cron_weekday = (moment.weekday() + 1) % 7
        by_day = moment.day in self.days
        by_weekday = cron_weekday in self.weekdays
        if self.day_star or self.weekday_star:
            return by_day and by_weekday
        return by_day or by_weekday

    def next_after(self, moment: datetime) -> datetime:
        """Thời điểm khớp gần nhất SAU `moment` (không tính chính nó).

        Nhảy theo bậc — sai tháng thì nhảy hẳn sang tháng sau, sai ngày thì
        nhảy sang ngày sau — chứ không dò từng phút. Dò từng phút thì một biểu
        thức kiểu `0 0 29 2 *` phải duyệt hai triệu vòng.
        """
        limit = moment.replace(microsecond=0) + timedelta(days=366 * MAX_LOOKAHEAD_YEARS)
        current = moment.replace(second=0, microsecond=0) + timedelta(minutes=1)

        while current <= limit:
            if current.month not in self.months:
                current = _start_of_next_month(current)
                continue
            if not self._day_matches(current):
                current = _start_of_next_day(current)
                continue
            if current.hour not in self.hours:
                current = (current + timedelta(hours=1)).replace(minute=0)
                continue
            if current.minute not in self.minutes:
                current += timedelta(minutes=1)
                continue
            return current

        raise BadRequestError(
            f"Biểu thức cron {self.source!r} không có lần chạy nào trong "
            f"{MAX_LOOKAHEAD_YEARS} năm tới. Kiểm lại ngày và tháng — "
            "'0 0 30 2 *' (30 tháng Hai) là kiểu lỗi hay gặp nhất."
        )


def _start_of_next_month(moment: datetime) -> datetime:
    year, month = (moment.year + 1, 1) if moment.month == 12 else (moment.year, moment.month + 1)
    return moment.replace(year=year, month=month, day=1, hour=0, minute=0)


def _start_of_next_day(moment: datetime) -> datetime:
    last = calendar.monthrange(moment.year, moment.month)[1]
    if moment.day >= last:
        return _start_of_next_month(moment)
    return moment.replace(day=moment.day + 1, hour=0, minute=0)


def parse_cron(expression: str) -> CronExpression:
    """Phân tích biểu thức cron. Ném BadRequestError kèm chỗ sai nếu không hợp lệ."""
    raw = expression.strip()
    if not raw:
        raise BadRequestError("Biểu thức cron không được để trống")

    resolved = SHORTCUTS.get(raw.lower(), raw)
    fields = resolved.split()
    if len(fields) != 5:
        raise BadRequestError(
            f"Biểu thức cron {expression!r} có {len(fields)} trường, cần đúng 5: "
            "phút giờ ngày tháng thứ (ví dụ '0 3 * * *'). "
            f"Lối tắt dùng được: {', '.join(sorted(SHORTCUTS))}."
        )

    values = [
        _parse_field(text, low, high, name)
        for text, (low, high), name in zip(fields, _RANGES, _FIELD_NAMES, strict=True)
    ]
    weekdays = {0 if v == 7 else v for v in values[4]}      # 7 và 0 đều là Chủ nhật
    return CronExpression(
        source=expression,
        minutes=frozenset(values[0]),
        hours=frozenset(values[1]),
        days=frozenset(values[2]),
        months=frozenset(values[3]),
        weekdays=frozenset(weekdays),
        # Xét đúng KÝ TỰ ĐẦU, như cron gốc: `*/7` vẫn tính là "có sao", nên
        # nó nối bằng VÀ chứ không phải HOẶC. Xem `_day_matches`.
        day_star=fields[2].startswith("*"),
        weekday_star=fields[4].startswith("*"),
    )


def _parse_field(text: str, low: int, high: int, name: str) -> set[int]:
    found: set[int] = set()
    for part in text.split(","):
        found |= _parse_part(part, low, high, name, text)
    if not found:
        raise BadRequestError(f"Trường {name} ({text!r}) không nhận giá trị nào")
    return found


def _parse_part(part: str, low: int, high: int, name: str, whole: str) -> set[int]:
    step = 1
    body = part
    if "/" in part:
        body, _, step_text = part.partition("/")
        if not step_text.isdigit() or int(step_text) < 1:
            raise BadRequestError(
                f"Trường {name} ({whole!r}): bước nhảy sau '/' phải là số nguyên dương, "
                f"đang là {step_text!r}"
            )
        step = int(step_text)

    if body == "*":
        start, stop = low, high
    elif "-" in body.lstrip("-"):
        start_text, _, stop_text = body.partition("-")
        start, stop = _number(start_text, low, high, name, whole), _number(
            stop_text, low, high, name, whole
        )
        if start > stop:
            raise BadRequestError(
                f"Trường {name} ({whole!r}): khoảng {body!r} có đầu lớn hơn cuối"
            )
    else:
        start = stop = _number(body, low, high, name, whole)

    return set(range(start, stop + 1, step))


def _number(text: str, low: int, high: int, name: str, whole: str) -> int:
    value = text.strip()
    if not value.isdigit():
        raise BadRequestError(
            f"Trường {name} ({whole!r}): {text!r} không phải số. "
            "Chỉ dùng số, '*', '-', ',' và '/'."
        )
    number = int(value)
    if not low <= number <= high:
        raise BadRequestError(
            f"Trường {name} ({whole!r}): {number} nằm ngoài khoảng cho phép {low}-{high}"
        )
    return number
