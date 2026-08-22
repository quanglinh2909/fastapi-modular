"""Số đo dạng Prometheus, viết tay để không phải thêm thư viện.

Chỉ có ba loại cần dùng: Counter (chỉ tăng), Gauge (lên xuống), Histogram
(phân bố độ trễ). Đủ để trả lời bốn câu hỏi vận hành quan trọng nhất: bao nhiêu
request, bao nhiêu lỗi, chậm cỡ nào, và tài nguyên còn bao nhiêu.

Một cạm bẫy phải tránh: KHÔNG lấy đường dẫn thật làm nhãn. `/api/users/abc123`
và `/api/users/def456` là hai nhãn khác nhau, mỗi user tạo một chuỗi số đo mới
và làm nổ bộ nhớ Prometheus. Phải dùng khuôn đường dẫn `/api/users/{user_id}`.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any

Labels = tuple[tuple[str, str], ...]

# Mốc chia histogram, tính bằng giây. Chọn quanh ngưỡng người dùng cảm nhận
# được: dưới 100ms là nhanh, trên 1s là chậm, trên 5s coi như hỏng.
DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


def _labels(**kwargs: Any) -> Labels:
    return tuple(sorted((k, str(v)) for k, v in kwargs.items()))


def _render_labels(labels: Labels) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{k}="{_escape(v)}"' for k, v in labels)
    return "{" + inner + "}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class Counter:
    def __init__(self, name: str, help_text: str) -> None:
        self.name = name
        self.help = help_text
        self._values: dict[Labels, float] = defaultdict(float)
        self._lock = threading.Lock()

    def inc(self, amount: float = 1.0, **labels: Any) -> None:
        with self._lock:
            self._values[_labels(**labels)] += amount

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} counter"]
        with self._lock:
            for labels, value in sorted(self._values.items()):
                lines.append(f"{self.name}{_render_labels(labels)} {value:g}")
        return lines


class Gauge:
    def __init__(self, name: str, help_text: str) -> None:
        self.name = name
        self.help = help_text
        self._values: dict[Labels, float] = defaultdict(float)
        self._lock = threading.Lock()

    def set(self, value: float, **labels: Any) -> None:
        with self._lock:
            self._values[_labels(**labels)] = value

    def inc_gauge(self, amount: float = 1.0, **labels: Any) -> None:
        with self._lock:
            self._values[_labels(**labels)] += amount

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} gauge"]
        with self._lock:
            for labels, value in sorted(self._values.items()):
                lines.append(f"{self.name}{_render_labels(labels)} {value:g}")
        return lines


class Histogram:
    def __init__(
        self, name: str, help_text: str, buckets: tuple[float, ...] = DEFAULT_BUCKETS
    ) -> None:
        self.name = name
        self.help = help_text
        self.buckets = buckets
        self._counts: dict[Labels, list[int]] = defaultdict(lambda: [0] * len(buckets))
        self._sums: dict[Labels, float] = defaultdict(float)
        self._totals: dict[Labels, int] = defaultdict(int)
        self._lock = threading.Lock()

    def observe(self, value: float, **labels: Any) -> None:
        key = _labels(**labels)
        with self._lock:
            counts = self._counts[key]
            for index, edge in enumerate(self.buckets):
                if value <= edge:
                    counts[index] += 1
            self._sums[key] += value
            self._totals[key] += 1

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} histogram"]
        with self._lock:
            for key in sorted(self._totals):
                counts = self._counts[key]
                for edge, count in zip(self.buckets, counts, strict=True):
                    labels = _render_labels((*key, ("le", str(edge))))
                    lines.append(f"{self.name}_bucket{labels} {count}")
                total = self._totals[key]
                lines.append(f"{self.name}_bucket{_render_labels((*key, ('le', '+Inf')))} {total}")
                lines.append(f"{self.name}_sum{_render_labels(key)} {self._sums[key]:g}")
                lines.append(f"{self.name}_count{_render_labels(key)} {total}")
        return lines


class Registry:
    def __init__(self) -> None:
        self._metrics: list[Counter | Gauge | Histogram] = []
        self._callbacks: list[Any] = []

    def register(self, metric: Counter | Gauge | Histogram):
        self._metrics.append(metric)
        return metric

    def on_scrape(self, callback: Any) -> None:
        """Hàm được gọi ngay trước khi xuất số đo, để cập nhật gauge tức thời."""
        self._callbacks.append(callback)

    def render(self) -> str:
        for callback in self._callbacks:
            callback()
        lines: list[str] = []
        for metric in self._metrics:
            lines.extend(metric.render())
        return "\n".join(lines) + "\n"


registry = Registry()

http_requests = registry.register(
    Counter("http_requests_total", "Số HTTP request đã xử lý")
)
http_duration = registry.register(
    Histogram("http_request_duration_seconds", "Thời gian xử lý HTTP request")
)
http_in_flight = registry.register(
    Gauge("http_requests_in_flight", "Số request đang xử lý")
)
app_info = registry.register(Gauge("app_info", "Thông tin phiên bản ứng dụng"))
db_circuit_state = registry.register(
    Gauge("db_circuit_state", "Trạng thái ngắt mạch database (0=đóng 1=nửa mở 2=ngắt)")
)

# ---- WebSocket ----------------------------------------------------------
# Nhãn chỉ có `namespace` và `event`, cả hai đều là tập hữu hạn do code khai
# báo. Tuyệt đối không lấy socket_id hay user_id làm nhãn: mỗi người dùng sẽ
# đẻ ra một chuỗi số đo mới và làm nổ Prometheus.
ws_connections = registry.register(
    Gauge("ws_connections", "Số kết nối WebSocket đang mở")
)
ws_connections_total = registry.register(
    Counter("ws_connections_total", "Tổng số kết nối WebSocket đã mở")
)
ws_messages_in = registry.register(
    Counter("ws_messages_in_total", "Số khung tin nhận từ client")
)
ws_messages_out = registry.register(
    Counter("ws_messages_out_total", "Số khung tin đã xếp hàng gửi cho client")
)
ws_send_dropped = registry.register(
    Counter("ws_send_dropped_total", "Số khung tin bị bỏ vì hàng đợi gửi đầy")
)


class Timer:
    """Đo thời gian một khối lệnh bằng đồng hồ đơn điệu."""

    __slots__ = ("_started", "elapsed")

    def __enter__(self) -> Timer:
        self._started = time.perf_counter()
        self.elapsed = 0.0
        return self

    def __exit__(self, *exc: object) -> None:
        self.elapsed = time.perf_counter() - self._started
