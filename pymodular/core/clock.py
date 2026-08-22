"""Nguồn thời gian dùng chung.

Tách riêng để (a) không module nào phải import module khác chỉ vì cần lấy giờ,
(b) test có thể monkeypatch một chỗ duy nhất khi cần đóng băng thời gian.
"""

from __future__ import annotations

from datetime import datetime

from pymodular.core.compat import UTC


def utcnow() -> datetime:
    return datetime.now(UTC)
