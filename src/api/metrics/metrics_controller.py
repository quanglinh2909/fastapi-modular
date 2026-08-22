"""Endpoint /metrics cho Prometheus.

Không nằm dưới prefix /api của các module nghiệp vụ theo thói quen chung —
nhưng ở template này mọi module đều dùng chung prefix, nên đường dẫn là
`/api/metrics`. Đổi prefix ở đây nếu hệ thống giám sát của bạn yêu cầu khác.
"""

from __future__ import annotations

from fastapi import Response

from fastapi_modular.core.config import Settings
from fastapi_modular.core.controller import controller, get
from fastapi_modular.core.metrics import app_info, db_circuit_state, registry
from fastapi_modular.infrastructure.database import Database

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

_CIRCUIT_CODES = {"closed": 0, "half_open": 1, "open": 2}


@controller(prefix="/metrics", tags=["metrics"])
class MetricsController:
    def __init__(self, settings: Settings, database: Database) -> None:
        self._settings = settings
        self._database = database
        registry.on_scrape(self._refresh)

    def _refresh(self) -> None:
        """Cập nhật các gauge phản ánh trạng thái tức thời, ngay trước khi xuất."""
        app_info.set(
            1,
            service=self._settings.name,
            version=self._settings.version,
            env=self._settings.env,
            driver=self._database.driver,
        )
        stats = getattr(self._database.backend, "stats", None)
        if stats:
            db_circuit_state.set(
                _CIRCUIT_CODES.get(stats["state"], -1), backend=self._database.driver
            )

    @get("", summary="Số đo dạng Prometheus", include_in_schema=False)
    async def scrape(self) -> Response:
        return Response(content=registry.render(), media_type=PROMETHEUS_CONTENT_TYPE)
