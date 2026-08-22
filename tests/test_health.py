from __future__ import annotations


def test_liveness(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["env"] == "local"


def test_readiness_co_ping_database(client):
    body = client.get("/api/health/ready").json()
    assert body["status"] == "ready"
    assert body["database"] is True
    assert body["driver"] == "memory"
