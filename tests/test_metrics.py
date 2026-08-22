"""Test số đo Prometheus và trace context."""

from __future__ import annotations

import pytest

from pymodular.core.context import new_trace_id, parse_traceparent
from pymodular.core.metrics import Counter, Gauge, Histogram, Registry


def test_counter_gom_theo_nhan():
    counter = Counter("thu_total", "thử")
    counter.inc(method="GET", status=200)
    counter.inc(method="GET", status=200)
    counter.inc(method="POST", status=201)

    rendered = "\n".join(counter.render())
    assert 'thu_total{method="GET",status="200"} 2' in rendered
    assert 'thu_total{method="POST",status="201"} 1' in rendered


def test_histogram_dem_dung_moc():
    histogram = Histogram("do_tre_seconds", "thử", buckets=(0.1, 1.0))
    for value in (0.05, 0.5, 5.0):
        histogram.observe(value)

    rendered = "\n".join(histogram.render())
    assert 'do_tre_seconds_bucket{le="0.1"} 1' in rendered
    assert 'do_tre_seconds_bucket{le="1.0"} 2' in rendered
    assert 'do_tre_seconds_bucket{le="+Inf"} 3' in rendered
    assert "do_tre_seconds_count 3" in rendered
    assert "do_tre_seconds_sum 5.55" in rendered


def test_gauge_len_xuong():
    gauge = Gauge("dang_chay", "thử")
    gauge.inc_gauge(1)
    gauge.inc_gauge(1)
    gauge.inc_gauge(-1)
    assert "dang_chay 1" in "\n".join(gauge.render())


def test_nhan_co_dau_nhay_duoc_thoat():
    counter = Counter("thu_total", "thử")
    counter.inc(path='/a"b')
    assert r'path="/a\"b"' in "\n".join(counter.render())


def test_registry_goi_callback_truoc_khi_xuat():
    registry = Registry()
    gauge = registry.register(Gauge("tam_thoi", "thử"))
    registry.on_scrape(lambda: gauge.set(42))
    assert "tam_thoi 42" in registry.render()


# ------------------------------------------------------------------ trace context
@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
         "4bf92f3577b34da6a3ce929d0e0e4736"),
        ("sai-khuon", None),
        (None, None),
        ("", None),
        ("00-" + "0" * 32 + "-00f067aa0ba902b7-01", None),      # trace id toàn số 0 là không hợp lệ
        ("00-KHONGPHAIHEX3577b34da6a3ce929d0e0-00f067aa0ba902b7-01", None),
    ],
)
def test_doc_traceparent(header, expected):
    assert parse_traceparent(header) == expected


def test_trace_id_moi_dung_khuon():
    trace_id = new_trace_id()
    assert len(trace_id) == 32
    assert all(c in "0123456789abcdef" for c in trace_id)


# ------------------------------------------------------------------ qua HTTP thật
def test_nhan_path_dung_khuon_khong_phai_duong_dan_that(client, user):
    """Nếu lấy đường dẫn thật làm nhãn, mỗi user sẽ tạo một chuỗi số đo mới."""
    client.get(f"/api/users/{user['id']}")
    client.get("/api/users/mot-id-khac-han")

    body = client.get("/api/metrics").text
    assert 'path="/api/users/{user_id}"' in body
    assert user["id"] not in body, "id thật không được lọt vào nhãn"


def test_duong_dan_khong_khop_gom_vao_mot_nhan(client):
    client.get("/khong-co-duong-nay")
    client.get("/cung-khong-co")
    body = client.get("/api/metrics").text
    assert 'path="unmatched"' in body


def test_metrics_co_du_loai_so_do(client, user):
    body = client.get("/api/metrics").text
    for name in (
        "http_requests_total",
        "http_request_duration_seconds_bucket",
        "http_requests_in_flight",
        "app_info",
    ):
        assert name in body, name


def test_trace_id_di_xuyen_qua_dich_vu(client):
    incoming = "4bf92f3577b34da6a3ce929d0e0e4736"
    response = client.get(
        "/api/users", headers={"traceparent": f"00-{incoming}-00f067aa0ba902b7-01"}
    )
    assert response.headers["x-trace-id"] == incoming


def test_khong_co_traceparent_thi_sinh_moi(client):
    response = client.get("/api/users")
    trace_id = response.headers["x-trace-id"]
    assert len(trace_id) == 32
    khac = client.get("/api/users").headers["x-trace-id"]
    assert khac != trace_id, "mỗi hành trình một trace id"


def test_loi_tra_kem_ca_request_id_va_trace_id(client):
    body = client.get("/api/users/khong-co").json()
    assert len(body["request_id"]) == 32
    assert len(body["trace_id"]) == 32
    assert body["request_id"] != body["trace_id"]
