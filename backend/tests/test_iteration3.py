"""Backend tests for iteration 3: late-payment bug fix, shipping zones (CEP validation),
cron-runs monitoring, MFA recovery codes (checkout flow contract), admin stock resolution.

Late payment bug: when payment is confirmed AFTER reservation expired, the order must be
marked approved, fulfillment=revisao_estoque, needs_stock_review=true. Admin can then
"fulfill" (baixa estoque, ou 409 se insuficiente) ou "refund" (marca refunded/cancelado).
"""
import os
import time
import uuid
import subprocess
import pytest
import requests

from conftest import TEST_BASE_URL as BASE_URL, API, MONGO_URI, TEST_DB
TS = int(time.time())
BIRTH_ADULT = "1990-01-01"


def _mongosh(script: str):
    r = subprocess.run(["mongosh", "--quiet", MONGO_URI, "--eval", script],
                       capture_output=True, text=True)
    return r.stdout.strip()


@pytest.fixture(scope="module")
def s():
    return requests.Session()


@pytest.fixture(scope="module")
def admin_session(s):
    uid = f"iter3-admin-{TS}-{uuid.uuid4().hex[:4]}"
    tok = f"iter3_adm_{TS}_{uuid.uuid4().hex[:6]}"
    _mongosh(f"""
db.users.insertOne({{user_id: '{uid}', email: '{uid}@example.com', name: 'Admin Iter3',
  role: 'owner', password_version: 1, mfa_enabled: true, marketing_opt_in: false,
  created_at: new Date().toISOString()}});
db.user_sessions.insertOne({{user_id: '{uid}', session_token: '{tok}', pv: 1,
  expires_at: new Date(Date.now() + 7*24*60*60*1000).toISOString(),
  created_at: new Date().toISOString()}});
""")
    yield tok
    _mongosh(f"db.users.deleteOne({{user_id: '{uid}'}}); db.user_sessions.deleteOne({{session_token: '{tok}'}});")


@pytest.fixture(scope="module")
def customer_token(s):
    email = f"iter3.cli.{TS}.{uuid.uuid4().hex[:6]}@example.com"
    r = s.post(f"{API}/auth/register", json={
        "email": email, "password": "Teste@12345",
        "name": "Cliente Iter3", "birth_date": BIRTH_ADULT})
    assert r.status_code == 200, r.text
    return r.json()["token"]


# ---------- SHIPPING ZONES ----------
class TestShippingZones:
    def test_public_zones_list(self, s):
        r = s.get(f"{API}/shipping/zones")
        assert r.status_code == 200
        zones = r.json()
        assert isinstance(zones, list) and len(zones) >= 1
        for z in zones:
            assert set(["name", "price", "deadline", "cep_prefixes"]).issubset(z.keys())

    def test_quote_sao_paulo_ok(self, s):
        wid = s.get(f"{API}/wines").json()[0]["wine_id"]
        r = s.post(f"{API}/checkout/quote", json={
            "items": [{"wine_id": wid, "qty": 1}], "cep": "01310100",
            "address": "Av. Paulista", "birth_date": BIRTH_ADULT, "payment_method": "pix"})
        assert r.status_code == 200
        d = r.json()
        assert d["shipping"] == 29.90
        assert "shipping_deadline" in d and d["shipping_deadline"]

    def test_quote_uncovered_cep_blocked(self, s):
        wid = s.get(f"{API}/wines").json()[0]["wine_id"]
        r = s.post(f"{API}/checkout/quote", json={
            "items": [{"wine_id": wid, "qty": 1}], "cep": "69000000",
            "address": "Manaus", "birth_date": BIRTH_ADULT, "payment_method": "pix"})
        assert r.status_code == 400
        assert "regi" in r.text.lower() or "entrega" in r.text.lower()

    def test_admin_zones_crud(self, s, admin_session):
        h = {"Authorization": f"Bearer {admin_session}"}
        # list
        r = s.get(f"{API}/admin/shipping", headers=h)
        assert r.status_code == 200
        # create
        payload = {"name": f"Zona Teste {TS}", "cep_prefixes": ["99"],
                   "price": 55.50, "deadline": "5 a 7 dias úteis", "active": True}
        r = s.post(f"{API}/admin/shipping", headers=h, json=payload)
        assert r.status_code == 200, r.text
        zid = r.json()["zone_id"]
        # delete
        r = s.delete(f"{API}/admin/shipping/{zid}", headers=h)
        assert r.status_code == 200

    def test_admin_zones_customer_forbidden(self, s, customer_token):
        h = {"Authorization": f"Bearer {customer_token}"}
        assert s.get(f"{API}/admin/shipping", headers=h).status_code == 403


# ---------- LATE PAYMENT BUG FIX ----------
class TestLatePaymentBugFix:
    def _make_order(self, s, customer_token):
        wid = s.get(f"{API}/wines").json()[0]["wine_id"]
        h = {"Authorization": f"Bearer {customer_token}"}
        r = s.post(f"{API}/checkout", json={
            "items": [{"wine_id": wid, "qty": 1}], "cep": "01310100",
            "address": "Av. Paulista, 1000", "birth_date": BIRTH_ADULT,
            "payment_method": "pix"}, headers=h)
        assert r.status_code == 200, r.text
        return r.json()["order_id"], h, wid

    def test_late_payment_marks_review(self, s, customer_token, admin_session):
        order_id, h, _wid = self._make_order(s, customer_token)
        # Force reservation expiry
        _mongosh(f"""db.orders.updateOne({{order_id: '{order_id}'}},
          {{$set: {{reservation_expires_at: new Date(Date.now() - 60000).toISOString()}}}});""")
        # Trigger lazy release by another action, or call cron via secret if any; use another checkout on same wine
        # Simpler: directly release via mongosh (simulate what cron does)
        _mongosh(f"""db.orders.updateOne({{order_id: '{order_id}'}},
          {{$set: {{reservation_active: false, payment_status: 'expired'}}}});""")
        # Now simulate MP approving late by resetting payment_status back to pending and calling confirm-demo
        _mongosh(f"""db.orders.updateOne({{order_id: '{order_id}'}},
          {{$set: {{payment_status: 'pending'}}}});""")
        r = s.post(f"{API}/orders/{order_id}/confirm-demo", headers=h)
        assert r.status_code == 200, r.text
        # Fetch as admin
        ah = {"Authorization": f"Bearer {admin_session}"}
        orders = s.get(f"{API}/admin/orders", headers=ah).json()
        found = [o for o in orders if o["order_id"] == order_id]
        assert len(found) == 1
        o = found[0]
        assert o["payment_status"] == "approved"
        assert o.get("needs_stock_review") is True
        assert o.get("fulfillment_status") == "revisao_estoque"

    def test_resolve_stock_fulfill_success(self, s, customer_token, admin_session):
        order_id, h, wid = self._make_order(s, customer_token)
        _mongosh(f"""db.orders.updateOne({{order_id: '{order_id}'}},
          {{$set: {{reservation_active: false, reservation_expires_at: new Date(Date.now()-60000).toISOString()}}}});""")
        r = s.post(f"{API}/orders/{order_id}/confirm-demo", headers=h)
        assert r.status_code == 200
        ah = {"Authorization": f"Bearer {admin_session}"}
        r = s.post(f"{API}/admin/orders/{order_id}/resolve-stock", headers=ah,
                   json={"action": "fulfill", "reason": "Estoque conferido"})
        assert r.status_code == 200, r.text
        assert r.json()["resolution"] == "fulfill"
        # verify persisted
        orders = s.get(f"{API}/admin/orders", headers=ah).json()
        o = [x for x in orders if x["order_id"] == order_id][0]
        assert o["fulfillment_status"] == "preparando"
        assert o.get("needs_stock_review") in (False, None)

    def test_resolve_stock_fulfill_409_when_insufficient(self, s, customer_token, admin_session):
        order_id, h, wid = self._make_order(s, customer_token)
        _mongosh(f"""db.orders.updateOne({{order_id: '{order_id}'}},
          {{$set: {{reservation_active: false, reservation_expires_at: new Date(Date.now()-60000).toISOString()}}}});""")
        # Zero out variant stock so fulfill fails
        _mongosh(f"""db.wines.updateOne({{wine_id: '{wid}'}},
          {{$set: {{'variants.0.stock': 0, 'variants.0.reserved': 0}}}});""")
        r = s.post(f"{API}/orders/{order_id}/confirm-demo", headers=h)
        assert r.status_code == 200
        ah = {"Authorization": f"Bearer {admin_session}"}
        r = s.post(f"{API}/admin/orders/{order_id}/resolve-stock", headers=ah,
                   json={"action": "fulfill", "reason": "Tentativa"})
        assert r.status_code == 409, f"Expected 409 got {r.status_code}: {r.text}"
        # restore stock (so other tests keep working)
        _mongosh(f"""db.wines.updateOne({{wine_id: '{wid}'}},
          {{$set: {{'variants.0.stock': 20, 'variants.0.reserved': 0}}}});""")

    def test_resolve_stock_refund_flow(self, s, customer_token, admin_session):
        order_id, h, _wid = self._make_order(s, customer_token)
        _mongosh(f"""db.orders.updateOne({{order_id: '{order_id}'}},
          {{$set: {{reservation_active: false, reservation_expires_at: new Date(Date.now()-60000).toISOString()}}}});""")
        r = s.post(f"{API}/orders/{order_id}/confirm-demo", headers=h)
        assert r.status_code == 200
        ah = {"Authorization": f"Bearer {admin_session}"}
        # solicitação NÃO marca como reembolsado
        r = s.post(f"{API}/admin/orders/{order_id}/resolve-stock", headers=ah,
                   json={"action": "refund", "reason": "Cliente aceitou estorno"})
        assert r.status_code == 200
        assert r.json()["resolution"] == "refund"
        orders = s.get(f"{API}/admin/orders", headers=ah).json()
        o = [x for x in orders if x["order_id"] == order_id][0]
        assert o["payment_status"] == "refund_requested"
        assert o["fulfillment_status"] == "cancelado"
        # repetição é idempotente (não duplica estorno)
        r = s.post(f"{API}/admin/orders/{order_id}/resolve-stock", headers=ah,
                   json={"action": "refund", "reason": "retry"})
        assert r.status_code == 200 and "já está em andamento" in r.json()["note"]
        # confirmação (modo demo) finaliza; segunda confirmação é bloqueada
        r = s.post(f"{API}/admin/orders/{order_id}/refund/confirm", headers=ah)
        assert r.status_code == 200
        r = s.post(f"{API}/admin/orders/{order_id}/refund/confirm", headers=ah)
        assert r.status_code == 400
        o = [x for x in s.get(f"{API}/admin/orders", headers=ah).json() if x["order_id"] == order_id][0]
        assert o["payment_status"] == "refunded"
        assert o["refund"]["status"] == "confirmed"


# ---------- CRON RUNS ----------
class TestCronRuns:
    def test_cron_runs_admin_only(self, s):
        assert s.get(f"{API}/admin/cron-runs").status_code == 401

    def test_cron_runs_owner(self, s, admin_session):
        r = s.get(f"{API}/admin/cron-runs", headers={"Authorization": f"Bearer {admin_session}"})
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_cron_backup_requires_secret(self, s):
        assert s.post(f"{API}/cron/backup").status_code == 401
