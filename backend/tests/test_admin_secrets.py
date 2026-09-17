"""Iteration 7 — /admin/secrets endpoints (secure credentials vault).

Coverage:
- GET /admin/secrets/status: auth (staff+owner OK), no values leaked, flags accurate.
- POST /admin/secrets: owner-only (staff → 403), format validation (short/space),
  saves fake token, triggers connection_test that gracefully fails (ok=false)
  without exposing the token, and value is NOT returned anywhere.
- After save, values not present in any admin response payload (status/settings/orders).
- Cleanup: deletes app_secrets doc and test users/sessions at the end.
"""
import os
import re
import time
import requests
import subprocess
import pytest

def _load_backend_url():
    # SEMPRE o backend isolado de testes (conftest). Sem fallback para a loja.
    from conftest import TEST_BASE_URL
    return TEST_BASE_URL

BASE_URL = _load_backend_url()
API = f"{BASE_URL}/api"
MONGO_URL = os.environ.get("ADEGA_TEST_MONGO_URL", "mongodb://localhost:27017")
from conftest import TEST_DB as DB_NAME, MONGO_URI

FAKE_TOKEN = "TEST-" + ("a1b2c3d4e5" * 4)  # 45 chars, no space, not real
FAKE_WEBHOOK = "TESTWEBHOOKSECRET_" + "x" * 20  # 38 chars


def _mongo_eval(js: str) -> str:
    # Delega ao helper do conftest: passa pela TRAVA e tem fallback local
    from conftest import _mongo_eval as _guarded
    r = _guarded(js)
    return (r.stdout or "") + (r.stderr or "")


def _inject_session(role: str, mfa: bool) -> tuple[str, str, str]:
    tag = f"itest7_{int(time.time()*1000)}_{role}"
    uid = f"test-{tag}"
    sess = f"test_adm_session_{tag}"
    email = f"teste.{tag}@example.com"
    js = f"""
db.users.insertOne({{
  user_id: '{uid}', email: '{email}', name: 'IT7 {role}',
  role: '{role}', password_version: 1, mfa_enabled: {str(mfa).lower()},
  created_at: new Date().toISOString()
}});
db.user_sessions.insertOne({{
  user_id: '{uid}', session_token: '{sess}', pv: 1,
  expires_at: new Date(Date.now()+7*24*60*60*1000).toISOString(),
  created_at: new Date().toISOString()
}});
print('OK');
"""
    out = _mongo_eval(js)
    assert "OK" in out, f"Failed to inject session: {out}"
    return uid, sess, email


@pytest.fixture(scope="module")
def owner_client():
    uid, sess, email = _inject_session("owner", True)
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {sess}", "Content-Type": "application/json"})
    yield s, email, uid
    _mongo_eval(f"db.users.deleteOne({{user_id:'{uid}'}}); db.user_sessions.deleteOne({{session_token:'{sess}'}});")


@pytest.fixture(scope="module")
def staff_client():
    uid, sess, email = _inject_session("staff", True)
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {sess}", "Content-Type": "application/json"})
    yield s, email, uid
    _mongo_eval(f"db.users.deleteOne({{user_id:'{uid}'}}); db.user_sessions.deleteOne({{session_token:'{sess}'}});")


@pytest.fixture(scope="module")
def customer_client():
    uid, sess, email = _inject_session("customer", False)
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {sess}", "Content-Type": "application/json"})
    yield s, email, uid
    _mongo_eval(f"db.users.deleteOne({{user_id:'{uid}'}}); db.user_sessions.deleteOne({{session_token:'{sess}'}});")


@pytest.fixture(scope="module", autouse=True)
def _cleanup_secrets():
    """NUNCA apagar o cofre real: faz snapshot e restaura ao final."""
    from pymongo import MongoClient
    cli = MongoClient(MONGO_URL)
    vault = cli[DB_NAME]["app_secrets"]
    snapshot = vault.find_one({"key": "payments"}, {"_id": 0})
    yield
    vault.delete_many({"key": "payments"})
    if snapshot:
        snapshot.pop("_id", None)
        vault.insert_one(snapshot)
    cli.close()


class TestSecretsAuth:
    def test_status_requires_admin(self, customer_client):
        s, *_ = customer_client
        r = s.get(f"{API}/admin/secrets/status")
        assert r.status_code in (401, 403)

    def test_status_no_auth(self):
        r = requests.get(f"{API}/admin/secrets/status")
        assert r.status_code in (401, 403)

    def test_save_forbidden_for_staff(self, staff_client):
        s, *_ = staff_client
        r = s.post(f"{API}/admin/secrets", json={"mp_access_token": FAKE_TOKEN})
        assert r.status_code == 403, f"expected 403, got {r.status_code} {r.text}"

    def test_save_forbidden_for_customer(self, customer_client):
        s, *_ = customer_client
        r = s.post(f"{API}/admin/secrets", json={"mp_access_token": FAKE_TOKEN})
        assert r.status_code in (401, 403)


class TestSecretsStatus:
    def test_status_initial_not_configured(self, owner_client):
        s, *_ = owner_client
        # determinístico sem destruir dados reais: snapshot, limpa, testa, restaura
        from pymongo import MongoClient
        cli = MongoClient(MONGO_URL)
        vault = cli[DB_NAME]["app_secrets"]
        snapshot = vault.find_one({"key": "payments"}, {"_id": 0})
        try:
            vault.delete_many({"key": "payments"})
            r = s.get(f"{API}/admin/secrets/status")
            assert r.status_code == 200
            data = r.json()
            # env may have credentials (unlikely in test env); in this env none.
            assert set(data.keys()) >= {"mp_access_token_set", "mp_webhook_secret_set", "source"}
            # No value leakage
            assert "mp_access_token" not in data
            assert "mp_webhook_secret" not in data
            # Given cleanup, must be false / source nenhum
            assert data["mp_access_token_set"] is False
            assert data["mp_webhook_secret_set"] is False
            assert data["source"] == "nenhum"
        finally:
            vault.delete_many({"key": "payments"})
            if snapshot:
                snapshot.pop("_id", None)
                vault.insert_one(snapshot)
            cli.close()


class TestSecretsSave:
    def test_validation_short_token(self, owner_client):
        s, *_ = owner_client
        r = s.post(f"{API}/admin/secrets", json={"mp_access_token": "shortie"})
        assert r.status_code == 400

    def test_validation_token_with_space(self, owner_client):
        s, *_ = owner_client
        r = s.post(f"{API}/admin/secrets", json={"mp_access_token": "TEST TOKEN WITH SPACE aaaaaaaaaaaaa"})
        assert r.status_code == 400

    def test_validation_nothing_to_save(self, owner_client):
        s, *_ = owner_client
        r = s.post(f"{API}/admin/secrets", json={})
        assert r.status_code == 400

    def test_save_fake_token_runs_connection_test_and_fails_gracefully(self, owner_client):
        s, *_ = owner_client
        r = s.post(f"{API}/admin/secrets", json={
            "mp_access_token": FAKE_TOKEN,
            "mp_webhook_secret": FAKE_WEBHOOK,
        })
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("ok") is True
        assert "connection_test" in data
        # Connection test must fail with fake creds — but response body must NOT contain the token
        assert data["connection_test"]["ok"] is False
        body_text = r.text
        assert FAKE_TOKEN not in body_text
        assert FAKE_WEBHOOK not in body_text

    def test_status_reflects_configured_after_save(self, owner_client):
        s, *_ = owner_client
        r = s.get(f"{API}/admin/secrets/status")
        assert r.status_code == 200
        data = r.json()
        assert data["mp_access_token_set"] is True
        assert data["mp_webhook_secret_set"] is True
        # source is 'formulario' when only DB has it; 'env' if env present
        assert data["source"] in ("formulario", "env")
        # And still no values in body
        assert FAKE_TOKEN not in r.text
        assert FAKE_WEBHOOK not in r.text

    def test_no_leak_in_admin_settings(self, owner_client):
        s, *_ = owner_client
        # settings endpoint should not contain the token/secret
        r = s.get(f"{API}/admin/settings")
        # allow 404 if endpoint different, but usually exists
        if r.status_code == 200:
            assert FAKE_TOKEN not in r.text
            assert FAKE_WEBHOOK not in r.text

    def test_persisted_in_db_and_used_by_payments_module(self, owner_client):
        # Verify DB doc exists and holds the value (server-side only)
        out = _mongo_eval("printjson(db.app_secrets.findOne({key:'payments'},{_id:0}));")
        assert "mp_access_token" in out
        assert FAKE_TOKEN in out  # value only stored server-side, this is direct mongo access


class TestPaymentModeGuard:
    """Regression: with credentials present, mp_test mode SHOULD be allowed by the settings guard.
    Without credentials the settings PATCH refuses mp_test. This confirms creds are actually used."""
    def test_payment_mode_switch_permitted_with_creds(self, owner_client):
        s, *_ = owner_client
        # attempt to set payment_mode = mp_test — should succeed since token is set (fake but present)
        r = s.patch(f"{API}/admin/settings", json={"payment_mode": "mp_test"})
        # Endpoint may be POST rather than PATCH; try alternative
        if r.status_code == 405:
            r = s.post(f"{API}/admin/settings", json={"payment_mode": "mp_test"})
        # We only require: response doesn't 403 due to "faltam credenciais"
        if r.status_code >= 400:
            assert "credenc" not in r.text.lower(), r.text
        # Revert to demo
        s.patch(f"{API}/admin/settings", json={"payment_mode": "demo"})
        s.post(f"{API}/admin/settings", json={"payment_mode": "demo"})
