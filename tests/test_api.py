"""HTTP-level tests (need: pip install -r requirements-dev.txt). Run with: pytest -q"""
import os, tempfile
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "api.db"))
os.environ["ADMIN_PASSWORD"] = "admin-pass-123"; os.environ["SEED_DEMO_USERS"] = "true"; os.environ["JWT_SECRET"] = "test-secret"
os.environ["RATE_LIMIT_PER_MIN"] = "100000"; os.environ["LOGIN_RATE_LIMIT_PER_MIN"] = "100000"
import hashlib, hmac, json
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.config import settings
settings.webhook_secret = "test-webhook-secret-1234"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def hdr(client, user="admin", pw="admin-pass-123"):
    r = client.post("/api/auth/login", json={"username": user, "password": pw}); assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_requires_auth(client):
    assert client.get("/api/shipments").status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": "bad"}).status_code == 401


def test_roles(client):
    v = hdr(client, "viewer", "viewer123")
    assert client.get("/api/shipments", headers=v).status_code == 200
    assert client.post("/api/shipments", headers=v, json={"origin": "Campinas, SP", "destination": "Recife, PE", "weight_kg": 1}).status_code == 403
    assert client.delete("/api/shipments/1", headers=hdr(client, "operator", "operator123")).status_code == 403


def test_quote_depends_on_route_and_validates(client):
    h = hdr(client)
    a = client.post("/api/quotes", headers=h, json={"origin": "Sao Paulo, SP", "destination": "Rio de Janeiro", "weight_kg": 2}).json()
    b = client.post("/api/quotes", headers=h, json={"origin": "Sao Paulo, SP", "destination": "Manaus, AM", "weight_kg": 2}).json()
    assert a["recommendation"]["price"] != b["recommendation"]["price"]
    assert client.post("/api/quotes", headers=h, json={"origin": "Sao Paulo, SP", "destination": "Rio", "weight_kg": 2, "objective": "banana"}).status_code == 422
    assert client.post("/api/quotes", headers=h, json={"origin": "Sao Paulo, SP", "destination": "Rio", "weight_kg": -1}).status_code == 422


def test_lifecycle_and_state_machine(client):
    h = hdr(client)
    s = client.post("/api/shipments", headers=h, json={"origin": "Campinas, SP", "destination": "Recife, PE", "weight_kg": 2, "order_id": "API-1"}).json()
    assert client.patch(f"/api/shipments/{s['id']}", headers=h, json={"status": "banana"}).status_code == 422
    assert client.patch(f"/api/shipments/{s['id']}", headers=h, json={"status": "Delivered"}).status_code == 409
    q = client.post(f"/api/shipments/{s['id']}/quotes", headers=h, json={}).json()
    code = q["recommendation"]["carrier_code"]
    b = client.post(f"/api/shipments/{s['id']}/assign", headers=h, json={"carrier_code": code}).json()
    assert b["status"] == "Booked" and b["tracking_code"]
    body = json.dumps({"tracking_code": b["tracking_code"], "event": "delivered"}).encode()
    sig = hmac.new(settings.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    assert client.post(f"/api/webhooks/tracking/{code}", content=body, headers={"X-Signature": sig, "Content-Type": "application/json"}).status_code == 200
    assert client.get(f"/api/shipments/{s['id']}", headers=h).json()["status"] == "Delivered"
    assert client.post(f"/api/webhooks/tracking/{code}", content=body, headers={"X-Signature": "bad"}).status_code == 401


def test_pagination_kpis_softdelete(client):
    h = hdr(client)
    for i in range(3): client.post("/api/shipments", headers=h, json={"origin": "Campinas, SP", "destination": "Curitiba, PR", "weight_kg": 1, "order_id": f"PG-{i}"})
    p = client.get("/api/shipments?limit=2&offset=0", headers=h).json()
    assert len(p["items"]) == 2 and p["total"] >= 3
    total = client.get("/api/kpis", headers=h).json()["total"]
    assert client.get("/api/shipments?q=PG-1", headers=h).json()["total"] == 1 and client.get("/api/kpis", headers=h).json()["total"] == total
    sid = p["items"][0]["id"]
    assert client.delete(f"/api/shipments/{sid}", headers=h).status_code == 200 and client.get(f"/api/shipments/{sid}", headers=h).status_code == 404


def test_batch_and_whatif(client):
    h = hdr(client)
    for i in range(10): client.post("/api/shipments", headers=h, json={"origin": "Campinas, SP", "destination": "Salvador, BA", "weight_kg": 1 + i, "order_id": f"B-{i}"})
    r = client.post("/api/optimize/batch", headers=h, json={"limit": 50, "objective": "cost"}); assert r.status_code == 200 and r.json()["optimized"]["assigned"] > 0
    assert client.post("/api/simulate/what-if", headers=h, json={"limit": 50}).status_code == 200


def test_webhook_disabled_without_real_secret(client, monkeypatch):
    monkeypatch.setattr(settings, "webhook_secret", "")
    assert client.post("/api/webhooks/tracking/ALPHA", content=b"{}", headers={"X-Signature": ""}).status_code == 503
    monkeypatch.setattr(settings, "webhook_secret", "change-me-too")
    assert client.post("/api/webhooks/tracking/ALPHA", content=b"{}", headers={"X-Signature": ""}).status_code == 503


def test_batch_reports_reasons_and_like_for_like(client):
    h = hdr(client)
    mk = lambda **k: client.post("/api/shipments", headers=h, json={"weight_kg": 2, **k}).json()["id"]
    ok = mk(origin="Campinas, SP", destination="Recife, PE", order_id="B-ok")
    tight = mk(origin="Campinas, SP", destination="Manaus, AM", order_id="B-tight", deadline="2000-01-01")
    r = client.post("/api/optimize/batch", headers=h, json={"shipment_ids": [ok, tight], "objective": "cost"}).json()
    assert {a["shipment_id"] for a in r["assignments"]} == {ok}
    u = r["unassigned"][0]
    assert u["shipment_id"] == tight and u["reason_code"] == "DEADLINE_INFEASIBLE" and u["reason"]
    assert r["unassigned_summary"] == {"DEADLINE_INFEASIBLE": 1}
    assert all("vs_optimized" in b for b in r["baselines"].values())
    # relaxed mode assigns it and flags the risk; apply must work too
    r2 = client.post("/api/optimize/batch", headers=h, json={"shipment_ids": [ok, tight], "objective": "cost", "relax_deadline": True, "apply": True}).json()
    assert r2["unassigned"] == [] and r2["relaxed_assignments"] == 1 and r2["applied"] == 2
    assert client.get(f"/api/shipments/{tight}", headers=h).json()["status"] == "Booked"


def test_v3_public_tracking_is_open_and_minimal(client):
    h = hdr(client)
    s = client.post("/api/shipments", headers=h, json={"origin": "Campinas, SP", "destination": "Recife, PE", "weight_kg": 2, "order_id": "PUB-SECRET-1", "declared_value": 777}).json()
    code = client.post(f"/api/shipments/{s['id']}/quotes", headers=h, json={}).json()["recommendation"]["carrier_code"]
    b = client.post(f"/api/shipments/{s['id']}/assign", headers=h, json={"carrier_code": code}).json()
    r = client.get(f"/api/public/track/{b['tracking_code']}")           # NO Authorization header
    assert r.status_code == 200 and "PUB-SECRET-1" not in r.text and "777" not in r.text and "price" not in r.json()
    assert client.get("/api/public/track/!!!").status_code == 404 and client.get("/api/public/track/NOSUCHCODE1").status_code == 404
    assert client.get("/track").status_code == 200


def test_v3_expected_cost_objective_and_penalty(client):
    h = hdr(client); base = {"origin": "Sao Paulo, SP", "destination": "Manaus, AM", "weight_kg": 2.5, "deadline_days": 40, "objective": "expected_cost"}
    lo = client.post("/api/quotes", headers=h, json={**base, "late_penalty": 0}).json(); hi = client.post("/api/quotes", headers=h, json={**base, "late_penalty": 500}).json()
    assert all(abs(q["expected_cost"] - q["price"]) < 0.011 for q in lo["quotes"]) and any(q["expected_cost"] > q["price"] + 1 for q in hi["quotes"])
    assert client.post("/api/quotes", headers=h, json={**base, "late_penalty": -5}).status_code == 422


def test_v3_geo_meta_scorecard_timeline_flows(client):
    h = hdr(client)
    assert client.get("/api/geo/suggest?q=sao pa", headers=h).json()[0]["value"] == "Sao Paulo, SP"
    assert client.get("/api/meta", headers=h).json()["default_late_penalty_brl"] > 0
    for p in ("/api/analytics/scorecard", "/api/analytics/timeline", "/api/analytics/flows"): assert client.get(p, headers=h).status_code == 200


def test_v3_notifications_need_operator(client):
    assert client.get("/api/notifications", headers=hdr(client, "viewer", "viewer123")).status_code == 403
    assert client.get("/api/notifications", headers=hdr(client, "operator", "operator123")).status_code == 200
    assert client.post("/api/shipments", headers=hdr(client), json={"origin": "Campinas, SP", "destination": "Recife, PE", "weight_kg": 1, "notify_email": "not-an-email"}).status_code == 422
