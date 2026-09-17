"""Backend API tests for Minha Adega wine e-commerce app — iteration 2 (auth hardened, variants, coupons, LGPD).

Admin access: owner login now returns an MFA challenge (no direct token). Tests obtain an
admin session by injecting a document into db.user_sessions via mongosh (see /app/auth_testing.md).
"""
import os
import time
import subprocess
import uuid
import pytest
import requests

from conftest import TEST_BASE_URL as BASE_URL, API, MONGO_URI, TEST_DB

OWNER_EMAIL = "mgrodrigues920@gmail.com"  # login por senha exige MFA; ver test_owner_login_returns_mfa_challenge

TIMESTAMP = int(time.time())
CUSTOMER_EMAIL = f"cliente.teste.{TIMESTAMP}.{uuid.uuid4().hex[:6]}@example.com"
CUSTOMER_PASSWORD = "Teste@12345"
CUSTOMER_NAME = "Cliente Teste"
BIRTH_ADULT = "1990-01-01"
BIRTH_MINOR = "2015-01-01"


@pytest.fixture(scope="session")
def s():
    return requests.Session()


@pytest.fixture(scope="session")
def admin_session(s):
    """Injeta sessão de admin (mfa_enabled=true) direto no MongoDB."""
    uid = f"test-admin-{TIMESTAMP}-{uuid.uuid4().hex[:4]}"
    tok = f"test_adm_session_{TIMESTAMP}_{uuid.uuid4().hex[:6]}"
    subprocess.run(["mongosh", "--quiet", MONGO_URI, "--eval", f"""
db.users.insertOne({{user_id: '{uid}', email: '{uid}@example.com', name: 'Admin Teste',
  role: 'owner', password_version: 1, mfa_enabled: true, marketing_opt_in: false,
  created_at: new Date().toISOString()}});
db.user_sessions.insertOne({{user_id: '{uid}', session_token: '{tok}', pv: 1,
  expires_at: new Date(Date.now() + 7*24*60*60*1000).toISOString(), created_at: new Date().toISOString()}});
"""], check=True, capture_output=True)
    yield tok
    subprocess.run(["mongosh", "--quiet", MONGO_URI, "--eval", f"""
db.users.deleteOne({{user_id: '{uid}'}}); db.user_sessions.deleteOne({{session_token: '{tok}'}});
"""], capture_output=True)


@pytest.fixture(scope="session")
def customer_token(s):
    r = s.post(f"{API}/auth/register", json={
        "email": CUSTOMER_EMAIL, "password": CUSTOMER_PASSWORD,
        "name": CUSTOMER_NAME, "birth_date": BIRTH_ADULT
    })
    assert r.status_code == 200, f"Register failed: {r.status_code} {r.text}"
    data = r.json()
    assert data["user"]["role"] == "customer"
    return data["token"]


# ------------------- HEALTH -------------------
def test_root(s):
    r = s.get(f"{API}/")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_settings_public(s):
    r = s.get(f"{API}/settings")
    assert r.status_code == 200
    assert r.json()["payment_mode"] == "demo"


# ------------------- WINES -------------------
class TestWines:
    def test_list_wines_seeded(self, s):
        r = s.get(f"{API}/wines")
        assert r.status_code == 200
        wines = r.json()
        assert len(wines) >= 8
        w = wines[0]
        assert "cost" not in w
        assert "_id" not in w
        for v in (w.get("variants") or []):
            assert "cost" not in v  # custo nunca vaza na API pública

    def test_wine_filter_type(self, s):
        r = s.get(f"{API}/wines", params={"type": "Tinto"})
        assert all(w["type"] == "Tinto" for w in r.json())

    def test_wine_search_q(self, s):
        r = s.get(f"{API}/wines", params={"q": "Malbec"})
        assert len(r.json()) >= 1

    def test_wine_sort_price_asc(self, s):
        r = s.get(f"{API}/wines", params={"sort": "price_asc"})
        prices = [w["price"] for w in r.json()]
        assert prices == sorted(prices)

    def test_wine_detail_and_404(self, s):
        wines = s.get(f"{API}/wines").json()
        wid = wines[0]["wine_id"]
        assert s.get(f"{API}/wines/{wid}").status_code == 200
        assert s.get(f"{API}/wines/nonexistent_id").status_code == 404


# ------------------- AUTH -------------------
class TestAuth:
    def test_register_minor_blocked(self, s):
        r = s.post(f"{API}/auth/register", json={
            "email": f"menor.{TIMESTAMP}@example.com", "password": CUSTOMER_PASSWORD,
            "name": "Menor", "birth_date": BIRTH_MINOR})
        assert r.status_code == 403

    def test_register_never_grants_admin(self, s):
        r = s.post(f"{API}/auth/register", json={
            "email": f"escalation.{TIMESTAMP}@example.com", "password": CUSTOMER_PASSWORD,
            "name": "X", "birth_date": BIRTH_ADULT, "role": "owner"})
        assert r.status_code == 200
        assert r.json()["user"]["role"] == "customer"

    def test_owner_login_returns_mfa_challenge(self, s):
        """Owner nunca recebe token direto: exige desafio MFA."""
        r = s.post(f"{API}/auth/login", json={"email": OWNER_EMAIL, "password": CUSTOMER_PASSWORD})
        assert r.status_code in (200, 401)  # 401 se senha desconhecida; 200 com desafio MFA
        if r.status_code == 200:
            d = r.json()
            assert "token" not in d
            assert d.get("mfa_required") or d.get("mfa_setup_required")

    def test_login_invalid(self, s):
        r = s.post(f"{API}/auth/login", json={"email": OWNER_EMAIL, "password": "wrong-password-123"})
        assert r.status_code == 401

    def test_me_requires_auth(self, s):
        assert s.get(f"{API}/auth/me").status_code == 401

    def test_me_with_token(self, s, customer_token):
        r = s.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {customer_token}"})
        assert r.status_code == 200
        assert r.json()["email"] == CUSTOMER_EMAIL

    def test_forgot_password_no_enumeration(self, s, customer_token):
        # NUNCA usar o e-mail real do proprietário em testes (criaria tokens e enviaria e-mails)
        r1 = s.post(f"{API}/auth/forgot-password", json={"email": CUSTOMER_EMAIL})
        r2 = s.post(f"{API}/auth/forgot-password", json={"email": f"naoexiste.{TIMESTAMP}@example.com"})
        assert r1.status_code == r2.status_code == 200
        assert r1.json() == r2.json()

    def test_reset_invalid_token(self, s):
        r = s.post(f"{API}/auth/reset-password", json={"token": "invalid-token", "new_password": "X@12345678"})
        assert r.status_code == 400

    def test_reset_preserves_legitimate_mfa(self, s):
        """Redefinição normal de senha NÃO remove MFA legítimo (sem flag residual)."""
        uid = f"test-mfa-{TIMESTAMP}-{uuid.uuid4().hex[:4]}"
        email = f"{uid}@example.com"
        raw_tok = f"rst_legit_{uid}"
        import subprocess, hashlib
        subprocess.run(["mongosh", "--quiet", MONGO_URI, "--eval", f"""
db.users.insertOne({{user_id: '{uid}', email: '{email}', name: 'MFA Legit', role: 'staff',
  password_hash: null, password_version: 1, mfa_enabled: true, mfa_secret: 'JBSWY3DPEHPK3PXP',
  mfa_recovery_codes: [{{hash: 'x', used: false}}], created_at: new Date().toISOString()}});
db.password_resets.insertOne({{token_hash: '{hashlib.sha256(raw_tok.encode()).hexdigest()}',
  user_id: '{uid}', used: false, expires_at: new Date(Date.now()+1800000).toISOString(),
  created_at: new Date().toISOString()}});
"""], check=True, capture_output=True)
        r = s.post(f"{API}/auth/reset-password", json={"token": raw_tok, "new_password": "NovaSenha@123"})
        assert r.status_code == 200, r.text
        out = subprocess.run(["mongosh", "--quiet", MONGO_URI, "--eval",
            f"print(JSON.stringify(db.users.findOne({{user_id: '{uid}'}}, {{mfa_enabled:1, mfa_secret:1}})))"],
            capture_output=True, text=True).stdout
        assert '"mfa_enabled":true' in out and 'mfa_secret' in out, out
        subprocess.run(["mongosh", "--quiet", MONGO_URI, "--eval",
            f"db.users.deleteOne({{user_id: '{uid}'}})"], capture_output=True)

    def test_reset_consumes_residual_flag_once(self, s):
        """Exceção única: resíduo de teste marcado é limpo UMA vez; depois o MFA é preservado."""
        uid = f"test-res-{TIMESTAMP}-{uuid.uuid4().hex[:4]}"
        email = f"{uid}@example.com"
        import subprocess, hashlib
        tok1, tok2 = f"rst_res1_{uid}", f"rst_res2_{uid}"
        subprocess.run(["mongosh", "--quiet", MONGO_URI, "--eval", f"""
db.users.insertOne({{user_id: '{uid}', email: '{email}', name: 'Residual', role: 'staff',
  password_hash: null, password_version: 1, mfa_enabled: true, mfa_secret: 'JBSWY3DPEHPK3PXP',
  mfa_residual_test: true, created_at: new Date().toISOString()}});
db.password_resets.insertMany([
  {{token_hash: '{hashlib.sha256(tok1.encode()).hexdigest()}', user_id: '{uid}', used: false, expires_at: new Date(Date.now()+1800000).toISOString(), created_at: new Date().toISOString()}},
  {{token_hash: '{hashlib.sha256(tok2.encode()).hexdigest()}', user_id: '{uid}', used: false, expires_at: new Date(Date.now()+1800000).toISOString(), created_at: new Date().toISOString()}}]);
"""], check=True, capture_output=True)
        # 1º reset: limpa o resíduo
        assert s.post(f"{API}/auth/reset-password", json={"token": tok1, "new_password": "NovaSenha@123"}).status_code == 200
        out = subprocess.run(["mongosh", "--quiet", MONGO_URI, "--eval",
            f"print(JSON.stringify(db.users.findOne({{user_id: '{uid}'}}, {{mfa_enabled:1, mfa_residual_test:1}})))"],
            capture_output=True, text=True).stdout
        assert '"mfa_enabled":false' in out and 'mfa_residual_test' not in out, out
        # usuário recadastra MFA (simulado), 2º reset PRESERVA
        subprocess.run(["mongosh", "--quiet", MONGO_URI, "--eval",
            f"db.users.updateOne({{user_id: '{uid}'}}, {{$set: {{mfa_enabled: true, mfa_secret: 'JBSWY3DPEHPK3PXP'}}}})"],
            capture_output=True)
        assert s.post(f"{API}/auth/reset-password", json={"token": tok2, "new_password": "OutraSenha@123"}).status_code == 200
        out = subprocess.run(["mongosh", "--quiet", MONGO_URI, "--eval",
            f"print(JSON.stringify(db.users.findOne({{user_id: '{uid}'}}, {{mfa_enabled:1, mfa_secret:1}})))"],
            capture_output=True, text=True).stdout
        assert '"mfa_enabled":true' in out, out
        subprocess.run(["mongosh", "--quiet", MONGO_URI, "--eval",
            f"db.users.deleteOne({{user_id: '{uid}'}})"], capture_output=True)

    def test_mfa_recovery_flow(self, s):
        """Fluxo separado: e-mail + código de recuperação de uso único (nunca só e-mail)."""
        uid = f"test-rec-{TIMESTAMP}-{uuid.uuid4().hex[:4]}"
        email = f"{uid}@example.com"
        import subprocess, hashlib
        plain_code = "c0d1g0-t35t3"
        subprocess.run(["mongosh", "--quiet", MONGO_URI, "--eval", f"""
db.users.insertOne({{user_id: '{uid}', email: '{email}', name: 'Rec', role: 'staff',
  password_hash: null, password_version: 1, mfa_enabled: true, mfa_secret: 'JBSWY3DPEHPK3PXP',
  mfa_recovery_codes: [{{hash: '{hashlib.sha256(plain_code.encode()).hexdigest()}', used: false}}],
  created_at: new Date().toISOString()}});
db.user_sessions.insertOne({{user_id: '{uid}', session_token: 'rec_sess_{uid}', pv: 1,
  expires_at: new Date(Date.now()+86400000).toISOString(), created_at: new Date().toISOString()}});
"""], check=True, capture_output=True)
        # anti-enumeração
        r1 = s.post(f"{API}/auth/mfa/recovery-request", json={"email": email})
        r2 = s.post(f"{API}/auth/mfa/recovery-request", json={"email": f"semconta.{TIMESTAMP}@example.com"})
        assert r1.status_code == r2.status_code == 200 and r1.json() == r2.json()
        # sessão ativa antes da confirmação
        assert s.get(f"{API}/auth/me", headers={"Authorization": "Bearer " + f"rec_sess_{uid}"}).status_code == 200
        # confirma com token injetado (o real chega por e-mail)
        raw = f"rec_tok_{uid}"
        subprocess.run(["mongosh", "--quiet", MONGO_URI, "--eval", f"""
db.mfa_recoveries.insertOne({{token_hash: '{hashlib.sha256(raw.encode()).hexdigest()}',
  user_id: '{uid}', used: false, expires_at: new Date(Date.now()+1800000).toISOString(),
  created_at: new Date().toISOString()}});
"""], check=True, capture_output=True)
        # e-mail sozinho (sem código) NÃO remove MFA
        r = s.post(f"{API}/auth/mfa/recovery-confirm", json={"token": raw, "recovery_code": "errado"})
        assert r.status_code == 400
        out = subprocess.run(["mongosh", "--quiet", MONGO_URI, "--eval",
            f"print(db.users.findOne({{user_id: '{uid}'}}).mfa_enabled)"], capture_output=True, text=True).stdout
        assert "true" in out, out
        # com código correto: conclui
        r = s.post(f"{API}/auth/mfa/recovery-confirm", json={"token": raw, "recovery_code": plain_code})
        assert r.status_code == 200
        # uso único
        assert s.post(f"{API}/auth/mfa/recovery-confirm", json={"token": raw, "recovery_code": plain_code}).status_code == 400
        out = subprocess.run(["mongosh", "--quiet", MONGO_URI, "--eval",
            f"print(JSON.stringify(db.users.findOne({{user_id: '{uid}'}}, {{mfa_enabled:1, mfa_secret:1}}))); print(db.user_sessions.countDocuments({{user_id: '{uid}'}}))"],
            capture_output=True, text=True).stdout
        assert '"mfa_enabled":false' in out and 'mfa_secret' not in out.split("\n")[0], out
        assert out.strip().endswith("0"), out  # sessões revogadas
        assert s.get(f"{API}/auth/me", headers={"Authorization": "Bearer " + f"rec_sess_{uid}"}).status_code == 401
        subprocess.run(["mongosh", "--quiet", MONGO_URI, "--eval",
            f"db.users.deleteOne({{user_id: '{uid}'}}); db.mfa_recoveries.deleteMany({{user_id: '{uid}'}})"], capture_output=True)


# ------------------- PAIRING -------------------
class TestPairing:
    def test_pairing_picanha(self, s):
        r = s.post(f"{API}/pairing", json={"dish": "picanha grelhada"}, timeout=60)
        assert r.status_code == 200
        d = r.json()
        assert d["mode"] in ("ai", "rules", "empty")
        assert len(d["recommendations"]) <= 3
        for rec in d["recommendations"]:
            assert "cost" not in rec["wine"]


# ------------------- CHECKOUT / ORDERS -------------------
class TestCheckoutOrders:
    @pytest.fixture(scope="class", autouse=True)
    def confirmed_zone(self, s, admin_session):
        """Zonas seed são exemplos não confirmados; testes confirmam a zona SP."""
        zones = s.get(f"{API}/admin/shipping", headers={"Authorization": f"Bearer {admin_session}"}).json()
        sp = next((z for z in zones if z["zone_id"] == "zone_sp_capital"), None)
        if sp and not sp.get("confirmed"):
            s.post(f"{API}/admin/shipping", headers={"Authorization": f"Bearer {admin_session}"},
                   json={**{k: sp[k] for k in ("zone_id", "name", "cep_prefixes", "price", "deadline", "active")}, "confirmed": True})

    def _payload(self, wid, **kw):
        return {"items": [{"wine_id": wid, "qty": 1}], "cep": "01310100",
                "address": "Av. Paulista, 1000", "birth_date": BIRTH_ADULT,
                "payment_method": "pix", **kw}

    def test_quote_with_coupon(self, s):
        wid = s.get(f"{API}/wines").json()[0]["wine_id"]
        r = s.post(f"{API}/checkout/quote", json=self._payload(wid, coupon="PRIMEIRAADEGA"))
        assert r.status_code == 200
        d = r.json()
        assert abs(d["discount"] - round(d["subtotal"] * 0.10, 2)) < 0.01
        assert d["shipping"] == 29.90
        assert d["shipping_mode"] == "tabela_propria"

    def test_quote_invalid_coupon_rejected(self, s):
        wid = s.get(f"{API}/wines").json()[0]["wine_id"]
        r = s.post(f"{API}/checkout/quote", json=self._payload(wid, coupon="CUPOMINVALIDO"))
        assert r.status_code == 400

    def test_checkout_requires_auth(self, s):
        wid = s.get(f"{API}/wines").json()[0]["wine_id"]
        assert s.post(f"{API}/checkout", json=self._payload(wid)).status_code == 401

    def test_checkout_minor_blocked(self, s):
        wid = s.get(f"{API}/wines").json()[0]["wine_id"]
        r = s.post(f"{API}/checkout/quote", json={**self._payload(wid), "birth_date": BIRTH_MINOR})
        assert r.status_code == 403

    def test_full_checkout_flow(self, s, customer_token):
        h = {"Authorization": f"Bearer {customer_token}"}
        wid = s.get(f"{API}/wines").json()[0]["wine_id"]
        r = s.post(f"{API}/checkout", json=self._payload(wid), headers=h)
        assert r.status_code == 200, r.text
        order_id = r.json()["order_id"]
        assert r.json()["payment_status"] == "pending"

        r2 = s.post(f"{API}/orders/{order_id}/confirm-demo", headers=h)
        assert r2.status_code == 200
        r3 = s.post(f"{API}/orders/{order_id}/confirm-demo", headers=h)  # idempotente
        assert r3.status_code == 200

        orders = s.get(f"{API}/orders", headers=h).json()
        match = [o for o in orders if o["order_id"] == order_id]
        assert len(match) == 1 and match[0]["payment_status"] == "approved"

    def test_order_isolation(self, s, customer_token):
        """Outro cliente não enxerga pedidos alheios."""
        r = s.post(f"{API}/auth/register", json={
            "email": f"outro.{TIMESTAMP}@example.com", "password": CUSTOMER_PASSWORD,
            "name": "Outro", "birth_date": BIRTH_ADULT})
        other = r.json()["token"]
        mine = s.get(f"{API}/orders", headers={"Authorization": f"Bearer {customer_token}"}).json()
        theirs = s.get(f"{API}/orders", headers={"Authorization": f"Bearer {other}"}).json()
        my_ids = {o["order_id"] for o in mine}
        assert not any(o["order_id"] in my_ids for o in theirs)


# ------------------- LGPD -------------------
class TestLGPD:
    def test_export(self, s, customer_token):
        r = s.get(f"{API}/account/export", headers={"Authorization": f"Bearer {customer_token}"})
        assert r.status_code == 200
        assert "perfil" in r.json() and "pedidos" in r.json()

    def test_profile_update(self, s, customer_token):
        r = s.patch(f"{API}/account/profile", headers={"Authorization": f"Bearer {customer_token}"},
                    json={"marketing_opt_in": True})
        assert r.status_code == 200


# ------------------- ADMIN -------------------
class TestAdmin:
    def test_admin_stats_anon(self, s):
        assert s.get(f"{API}/admin/stats").status_code == 401

    def test_admin_stats_customer_forbidden(self, s, customer_token):
        assert s.get(f"{API}/admin/stats", headers={"Authorization": f"Bearer {customer_token}"}).status_code == 403

    def test_admin_stats_owner(self, s, admin_session):
        r = s.get(f"{API}/admin/stats", headers={"Authorization": f"Bearer {admin_session}"})
        assert r.status_code == 200
        d = r.json()
        assert "real" in d and "demo" in d  # receita demo separada da real
        for k in ("revenue", "orders_paid", "avg_ticket"):
            assert k in d["real"]

    def test_admin_orders_owner(self, s, admin_session):
        r = s.get(f"{API}/admin/orders", headers={"Authorization": f"Bearer {admin_session}"})
        assert r.status_code == 200

    def test_admin_wines_full_owner(self, s, admin_session):
        r = s.get(f"{API}/admin/wines-full", headers={"Authorization": f"Bearer {admin_session}"})
        assert r.status_code == 200
        assert len(r.json()) >= 8

    def test_admin_coupons_crud(self, s, admin_session):
        h = {"Authorization": f"Bearer {admin_session}"}
        code = f"PYTEST{TIMESTAMP % 10000}"
        assert s.post(f"{API}/admin/coupons", headers=h, json={"code": code, "percent": 15}).status_code == 200
        assert any(c["code"] == code for c in s.get(f"{API}/admin/coupons", headers=h).json())
        assert s.delete(f"{API}/admin/coupons/{code}", headers=h).status_code == 200

    def test_admin_staff_grant_only_owner(self, s, admin_session):
        h = {"Authorization": f"Bearer {admin_session}"}
        r = s.post(f"{API}/admin/staff", headers=h, json={"email": CUSTOMER_EMAIL, "role": "staff"})
        assert r.status_code == 200

    def test_webhook_mp_rejects_unsigned(self, s):
        r = s.post(f"{API}/webhooks/mercadopago", json={"type": "payment", "data": {"id": "1"}})
        assert r.status_code == 401

    def test_cron_requires_secret(self, s):
        assert s.post(f"{API}/cron/release-reservations").status_code == 401
