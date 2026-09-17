"""Regressão direcionada — gate de MFA do painel administrativo.

Reproduz o bloqueio relatado pelo proprietário: após login + TOTP válido,
a resposta de /auth/mfa/verify deve trazer user.mfa_enabled=True para o
frontend liberar o painel (AccountPage.jsx usa esse indicador).

Ambiente ISOLADO (porta 8002, banco adega_test). Nunca toca a conta real.
"""
import time
import secrets as py_secrets
from datetime import datetime, timezone

import pyotp
import pytest
import requests
from pymongo import MongoClient

from conftest import API, TEST_DB as DB_NAME

MONGO_URL = "mongodb://localhost:27017"
OWNER_EMAIL = "mgrodrigues920@gmail.com"
MFA_SECRET = "JBSWY3DPEHPK3PXP"
TEST_TAG = f"testmfagate{int(time.time())}"


@pytest.fixture(scope="module")
def mdb():
    client = MongoClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


@pytest.fixture(scope="module", autouse=True)
def cleanup(mdb):
    yield
    mdb.users.delete_many({"email": {"$regex": f"^{TEST_TAG}"}})


def _mk_admin(mdb, mfa_enabled=True):
    from argon2 import PasswordHasher
    uid = f"{TEST_TAG}-{py_secrets.token_hex(4)}"
    email = f"{TEST_TAG}.{py_secrets.token_hex(3)}@example.com"
    doc = {
        "user_id": uid, "email": email, "name": "Admin Fictício",
        "role": "staff", "password_hash": PasswordHasher().hash("Password123!"),
        "password_version": 1, "mfa_enabled": mfa_enabled,
        "marketing_opt_in": False, "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if mfa_enabled:
        doc["mfa_secret"] = MFA_SECRET
        doc["mfa_recovery_codes"] = []
    mdb.users.insert_one(doc)
    return doc


class TestAdminMfaGate:
    def test_login_requires_mfa_no_token(self, mdb):
        u = _mk_admin(mdb, mfa_enabled=True)
        r = requests.post(f"{API}/auth/login", json={"email": u["email"], "password": "Password123!"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("mfa_required") is True and "token" not in body

    def test_mfa_verify_returns_mfa_enabled_in_user(self, mdb):
        """REPRODUÇÃO do bug: user.mfa_enabled deve vir True na resposta."""
        u = _mk_admin(mdb, mfa_enabled=True)
        r = requests.post(f"{API}/auth/login", json={"email": u["email"], "password": "Password123!"})
        mfa_token = r.json()["mfa_token"]
        code = pyotp.TOTP(MFA_SECRET).now()
        r2 = requests.post(f"{API}/auth/mfa/verify", json={"mfa_token": mfa_token, "code": code})
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert "token" in body
        assert body["user"].get("mfa_enabled") is True, \
            f"BUG: /auth/mfa/verify não retornou mfa_enabled no user: {body['user']}"

    def test_session_token_auth_me_and_admin_access(self, mdb):
        """Sessão emitida no verify autentica /auth/me e libera endpoint admin."""
        u = _mk_admin(mdb, mfa_enabled=True)
        r = requests.post(f"{API}/auth/login", json={"email": u["email"], "password": "Password123!"})
        mfa_token = r.json()["mfa_token"]
        code = pyotp.TOTP(MFA_SECRET).now()
        r2 = requests.post(f"{API}/auth/mfa/verify", json={"mfa_token": mfa_token, "code": code})
        token = r2.json()["token"]
        h = {"Authorization": f"Bearer {token}"}
        me = requests.get(f"{API}/auth/me", headers=h)
        assert me.status_code == 200 and me.json().get("mfa_enabled") is True
        adm = requests.get(f"{API}/admin/users", headers=h)
        assert adm.status_code == 200, f"require_admin recusou sessão pós-MFA: {adm.status_code} {adm.text}"

    def test_mfa_setup_flow_returns_mfa_enabled(self, mdb):
        """Admin novo (mfa_setup) também deve receber mfa_enabled=True ao concluir."""
        u = _mk_admin(mdb, mfa_enabled=False)
        r = requests.post(f"{API}/auth/login", json={"email": u["email"], "password": "Password123!"})
        body = r.json()
        assert body.get("mfa_setup_required") is True
        mfa_token = body["mfa_token"]
        setup = requests.post(f"{API}/auth/mfa/setup", json={"mfa_token": mfa_token})
        assert setup.status_code == 200, setup.text
        secret = setup.json()["secret"]
        code = pyotp.TOTP(secret).now()
        r2 = requests.post(f"{API}/auth/mfa/verify", json={"mfa_token": mfa_token, "code": code})
        assert r2.status_code == 200, r2.text
        body2 = r2.json()
        assert body2["user"].get("mfa_enabled") is True
        assert body2.get("recovery_codes") and len(body2["recovery_codes"]) == 8


@pytest.fixture(scope="module")
def owner_snapshot(mdb):
    """Snapshot do owner seed do banco ISOLADO antes dos testes (comparação antes/depois)."""
    return mdb.users.find_one({"email": OWNER_EMAIL}, {"_id": 0, "password_hash": 0, "mfa_secret": 0})


class TestOwnerUntouched:
    def test_owner_state_unchanged(self, mdb, owner_snapshot):
        if owner_snapshot is None:
            pytest.skip("Owner seed ausente no banco de testes")
        cur = mdb.users.find_one({"email": OWNER_EMAIL}, {"_id": 0, "password_hash": 0, "mfa_secret": 0})
        assert cur is not None
        assert cur.get("role") == owner_snapshot.get("role")
        assert cur.get("mfa_enabled") == owner_snapshot.get("mfa_enabled")
        assert cur.get("user_id") == owner_snapshot.get("user_id")
