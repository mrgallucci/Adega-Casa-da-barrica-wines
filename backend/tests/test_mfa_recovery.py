"""Iteration 6 — MFA recovery flow + password-reset preserving legit MFA.

Covers:
- POST /api/auth/mfa/recovery-request anti-enumeration + rate-limit
- POST /api/auth/mfa/recovery-confirm garbage token / valid injected token / single-use
- POST /api/auth/reset-password preserves MFA when residual flag not set
- Owner mgrodrigues920@gmail.com is NEVER mutated
- Cleans up ALL TEST_ users, sessions and tokens
"""
import os
import time
import hashlib
import secrets as py_secrets
from datetime import datetime, timezone, timedelta

import pytest
import requests
from pymongo import MongoClient

from conftest import TEST_BASE_URL as BASE_URL, API, MONGO_URI, TEST_DB as DB_NAME
MONGO_URL = os.environ.get("ADEGA_TEST_MONGO_URL", "mongodb://localhost:27017")
MONGOSH_DB = MONGO_URI
OWNER_EMAIL = "mgrodrigues920@gmail.com"

TEST_TAG = f"testmfarec{int(time.time())}"


@pytest.fixture(scope="module")
def mdb():
    client = MongoClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


@pytest.fixture(scope="module")
def owner_snapshot(mdb):
    """Snapshot owner doc BEFORE tests to verify it's untouched at the end."""
    snap = mdb.users.find_one({"email": OWNER_EMAIL.lower()}, {"_id": 0})
    yield snap
    # cleanup handled in dedicated test at end


@pytest.fixture(scope="module", autouse=True)
def cleanup(mdb):
    yield
    # Delete any test users / sessions / tokens created by this module
    mdb.users.delete_many({"email": {"$regex": f"^{TEST_TAG}"}})
    mdb.user_sessions.delete_many({"user_id": {"$regex": f"^{TEST_TAG}"}})
    mdb.mfa_recoveries.delete_many({"user_id": {"$regex": f"^{TEST_TAG}"}})
    mdb.password_resets.delete_many({"user_id": {"$regex": f"^{TEST_TAG}"}})


def _mk_user(mdb, mfa_enabled=False, residual=False, with_password=True, with_codes=False):
    from argon2 import PasswordHasher
    ph = PasswordHasher()
    uid = f"{TEST_TAG}-{py_secrets.token_hex(4)}"
    email = f"{TEST_TAG}.{py_secrets.token_hex(3)}@example.com"
    doc = {
        "user_id": uid, "email": email, "name": "Test User",
        "role": "staff" if mfa_enabled else "customer",
        "password_version": 1, "mfa_enabled": mfa_enabled,
        "marketing_opt_in": False, "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if with_password:
        doc["password_hash"] = ph.hash("Password123!")
    if mfa_enabled:
        doc["mfa_secret"] = "JBSWY3DPEHPK3PXP"
    if residual:
        doc["mfa_residual_test"] = True
    if with_codes:
        plain = f"codigo-teste-{py_secrets.token_hex(4)}"
        doc["mfa_recovery_codes"] = [{"hash": hashlib.sha256(plain.encode()).hexdigest(), "used": False}]
        doc["_plain_recovery_code"] = plain
    mdb.users.insert_one(doc)
    return doc


# ---------- /auth/mfa/recovery-request anti-enumeration ----------

class TestMfaRecoveryRequest:
    def test_identical_response_existing_vs_nonexistent(self, mdb):
        u = _mk_user(mdb, mfa_enabled=True)
        r1 = requests.post(f"{API}/auth/mfa/recovery-request", json={"email": u["email"]})
        r2 = requests.post(f"{API}/auth/mfa/recovery-request",
                           json={"email": f"{TEST_TAG}.nobody@example.com"})
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json() == r2.json()
        assert r1.json().get("ok") is True

    def test_recovery_row_only_for_existing_mfa_user(self, mdb):
        u = _mk_user(mdb, mfa_enabled=True)
        requests.post(f"{API}/auth/mfa/recovery-request", json={"email": u["email"]})
        # allow async insert
        time.sleep(0.3)
        count_real = mdb.mfa_recoveries.count_documents({"user_id": u["user_id"]})
        assert count_real >= 1
        # non-existent should NOT create a row
        requests.post(f"{API}/auth/mfa/recovery-request",
                      json={"email": f"{TEST_TAG}.ghost@example.com"})
        assert mdb.mfa_recoveries.count_documents({"user_id": {"$regex": "ghost"}}) == 0


# ---------- /auth/mfa/recovery-confirm ----------

class TestMfaRecoveryConfirm:
    def test_garbage_token_returns_400(self):
        r = requests.post(f"{API}/auth/mfa/recovery-confirm",
                          json={"token": "not-a-real-token-xxx", "recovery_code": "x"})
        assert r.status_code == 400

    def test_valid_token_requires_recovery_code(self, mdb):
        """E-mail sozinho NÃO remove o MFA: sem código de recuperação → 400, link não consumido."""
        u = _mk_user(mdb, mfa_enabled=True, with_codes=True)
        raw = py_secrets.token_urlsafe(32)
        mdb.mfa_recoveries.insert_one({
            "token_hash": hashlib.sha256(raw.encode()).hexdigest(),
            "user_id": u["user_id"], "used": False,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        # sem código
        r = requests.post(f"{API}/auth/mfa/recovery-confirm", json={"token": raw, "recovery_code": ""})
        assert r.status_code == 400
        # código errado
        r = requests.post(f"{API}/auth/mfa/recovery-confirm", json={"token": raw, "recovery_code": "errado-123"})
        assert r.status_code == 400
        # MFA continua ativo e link NÃO foi consumido
        after = mdb.users.find_one({"user_id": u["user_id"]}, {"_id": 0})
        assert after["mfa_enabled"] is True and "mfa_secret" in after
        rec = mdb.mfa_recoveries.find_one({"user_id": u["user_id"]})
        assert rec["used"] is False
        # código correto conclui
        r = requests.post(f"{API}/auth/mfa/recovery-confirm",
                          json={"token": raw, "recovery_code": u["_plain_recovery_code"]})
        assert r.status_code == 200, r.text
        after = mdb.users.find_one({"user_id": u["user_id"]}, {"_id": 0})
        assert after["mfa_enabled"] is False and "mfa_secret" not in after

    def test_recovery_without_available_codes_points_to_manual(self, mdb):
        """Sem códigos de recuperação → procedimento manual de verificação de identidade."""
        u = _mk_user(mdb, mfa_enabled=True, with_codes=False)
        raw = py_secrets.token_urlsafe(32)
        mdb.mfa_recoveries.insert_one({
            "token_hash": hashlib.sha256(raw.encode()).hexdigest(),
            "user_id": u["user_id"], "used": False,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        r = requests.post(f"{API}/auth/mfa/recovery-confirm",
                          json={"token": raw, "recovery_code": "qualquer-coisa"})
        assert r.status_code == 400
        assert "Procedimento manual" in r.json()["detail"]
        after = mdb.users.find_one({"user_id": u["user_id"]}, {"_id": 0})
        assert after["mfa_enabled"] is True

    def test_valid_token_clears_mfa_and_revokes_sessions_single_use(self, mdb):
        u = _mk_user(mdb, mfa_enabled=True, with_codes=True)
        # inject a session for the user
        mdb.user_sessions.insert_one({
            "user_id": u["user_id"], "session_token": f"{TEST_TAG}-sess-{py_secrets.token_hex(3)}",
            "pv": 1, "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        # inject a valid recovery token
        raw = py_secrets.token_urlsafe(32)
        mdb.mfa_recoveries.insert_one({
            "token_hash": hashlib.sha256(raw.encode()).hexdigest(),
            "user_id": u["user_id"], "used": False,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        r = requests.post(f"{API}/auth/mfa/recovery-confirm",
                          json={"token": raw, "recovery_code": u["_plain_recovery_code"]})
        assert r.status_code == 200, r.text
        # verify DB state
        after = mdb.users.find_one({"user_id": u["user_id"]}, {"_id": 0})
        assert after["mfa_enabled"] is False
        assert "mfa_secret" not in after
        assert after["password_version"] == u["password_version"] + 1
        assert mdb.user_sessions.count_documents({"user_id": u["user_id"]}) == 0
        # second confirm should now fail (single-use)
        r2 = requests.post(f"{API}/auth/mfa/recovery-confirm",
                           json={"token": raw, "recovery_code": u["_plain_recovery_code"]})
        assert r2.status_code == 400

    def test_email_chain_cannot_takeover_account(self, mdb):
        """Sequência 'redefinir senha por e-mail → recuperar MFA por e-mail' NÃO assume a conta."""
        u = _mk_user(mdb, mfa_enabled=True, with_codes=False)
        # 1. reset de senha por e-mail: preserva MFA
        raw_reset = py_secrets.token_urlsafe(32)
        mdb.password_resets.insert_one({
            "token_hash": hashlib.sha256(raw_reset.encode()).hexdigest(),
            "user_id": u["user_id"], "used": False,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        r = requests.post(f"{API}/auth/reset-password",
                          json={"token": raw_reset, "new_password": "Atacante123!"})
        assert r.status_code == 200
        after = mdb.users.find_one({"user_id": u["user_id"]}, {"_id": 0})
        assert after["mfa_enabled"] is True  # MFA preservado
        # 2. link de recuperação de MFA por e-mail, SEM código de recuperação → 400
        raw_rec = py_secrets.token_urlsafe(32)
        mdb.mfa_recoveries.insert_one({
            "token_hash": hashlib.sha256(raw_rec.encode()).hexdigest(),
            "user_id": u["user_id"], "used": False,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        r = requests.post(f"{API}/auth/mfa/recovery-confirm",
                          json={"token": raw_rec, "recovery_code": "nao-tem"})
        assert r.status_code == 400
        # 3. login com a senha redefinida continua exigindo MFA (sem token de acesso)
        r = requests.post(f"{API}/auth/login", json={"email": u["email"], "password": "Atacante123!"})
        body = r.json()
        assert "token" not in body and body.get("mfa_required") is True
        # 4. MFA intacto
        after = mdb.users.find_one({"user_id": u["user_id"]}, {"_id": 0})
        assert after["mfa_enabled"] is True and "mfa_secret" in after


# ---------- Password reset preserves MFA (no residual flag) ----------

class TestPasswordResetPreservesMfa:
    def test_reset_preserves_legit_mfa(self, mdb):
        u = _mk_user(mdb, mfa_enabled=True, residual=False)
        # forgot-password to seed a reset row
        r = requests.post(f"{API}/auth/forgot-password", json={"email": u["email"]})
        assert r.status_code == 200
        # inject our own known reset token (safer than reading async-sent email)
        raw = py_secrets.token_urlsafe(32)
        mdb.password_resets.insert_one({
            "token_hash": hashlib.sha256(raw.encode()).hexdigest(),
            "user_id": u["user_id"], "used": False,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        r2 = requests.post(f"{API}/auth/reset-password",
                           json={"token": raw, "new_password": "BrandNewPass1!"})
        assert r2.status_code == 200, r2.text
        after = mdb.users.find_one({"user_id": u["user_id"]}, {"_id": 0})
        assert after["mfa_enabled"] is True  # PRESERVED
        assert "mfa_secret" in after         # PRESERVED

    def test_reset_consumes_residual_flag(self, mdb):
        u = _mk_user(mdb, mfa_enabled=True, residual=True)
        raw = py_secrets.token_urlsafe(32)
        mdb.password_resets.insert_one({
            "token_hash": hashlib.sha256(raw.encode()).hexdigest(),
            "user_id": u["user_id"], "used": False,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        r = requests.post(f"{API}/auth/reset-password",
                         json={"token": raw, "new_password": "AnotherPass1!"})
        assert r.status_code == 200
        after = mdb.users.find_one({"user_id": u["user_id"]}, {"_id": 0})
        assert after["mfa_enabled"] is False
        assert "mfa_secret" not in after
        assert "mfa_residual_test" not in after


# ---------- Regression: customer password reset + login ----------

class TestCustomerPasswordResetRegression:
    def test_customer_reset_and_login(self, mdb):
        u = _mk_user(mdb, mfa_enabled=False)
        r = requests.post(f"{API}/auth/forgot-password", json={"email": u["email"]})
        assert r.status_code == 200
        raw = py_secrets.token_urlsafe(32)
        mdb.password_resets.insert_one({
            "token_hash": hashlib.sha256(raw.encode()).hexdigest(),
            "user_id": u["user_id"], "used": False,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        new_pw = "FreshPass1!"
        r2 = requests.post(f"{API}/auth/reset-password", json={"token": raw, "new_password": new_pw})
        assert r2.status_code == 200
        # login should now work (customer -> real JWT, no MFA)
        r3 = requests.post(f"{API}/auth/login", json={"email": u["email"], "password": new_pw})
        assert r3.status_code == 200, r3.text
        body = r3.json()
        assert "token" in body and body["user"]["email"] == u["email"]


# ---------- Owner untouched ----------

class TestOwnerUntouched:
    def test_owner_state_unchanged(self, mdb, owner_snapshot):
        if owner_snapshot is None:
            pytest.skip("Owner not present in DB")
        cur = mdb.users.find_one({"email": OWNER_EMAIL.lower()}, {"_id": 0})
        assert cur is not None
        # Owner must still exist with same email/user_id/role
        assert cur["email"] == owner_snapshot["email"]
        assert cur["user_id"] == owner_snapshot["user_id"]
        assert cur.get("role") == owner_snapshot.get("role")
        # mfa_residual_test flag: pode ter sido retirada pelo operador após a recuperação legítima
        # (o snapshot é tirado no início desta suíte; apenas verificamos que os testes não a alteraram)
        assert cur.get("mfa_residual_test") == owner_snapshot.get("mfa_residual_test")
        assert cur.get("mfa_enabled") == owner_snapshot.get("mfa_enabled")
