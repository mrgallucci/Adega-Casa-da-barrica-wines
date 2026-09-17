"""Clube Casa da Barrica + Pontos + Analytics — roda APENAS no backend isolado (porta 8002, banco adega_test).

Regras cobertas:
- Clube: adesão opcional com CPF/telefone/endereço NÃO obrigatórios, aceites de termos
  (com versão registrada) e LGPD obrigatórios, opt-ins de e-mail/WhatsApp separados.
- Pontos: demo/homologação nunca geram saldo; base = produtos após descontos, sem frete,
  floor; idempotência; estorno total e parcial; resgate com reserva atômica, proteção
  contra uso simultâneo, liberação na expiração e devolução em reembolso.
- Analytics: pageview/add_to_cart do navegador; signup/checkout_start/purchase do servidor;
  exclusão de admins; respeito a DNT/GPC; retenção TTL de 180 dias; funil com cadastros
  como indicador separado.
"""
import random
import time
import uuid

import pytest
import requests

from conftest import API, _mongo_eval

_RUN = uuid.uuid4().hex[:6]
CEP_OK = "01310100"  # prefixo 01 = zona SP capital (confirmada pela fixture)


def _email(tag):
    return f"test-cpa-{tag}-{_RUN}-{uuid.uuid4().hex[:4]}@example.com"


def _valid_cpf():
    base = [random.randint(0, 9) for _ in range(9)]
    d1 = (sum(b * (10 - i) for i, b in enumerate(base)) * 10 % 11) % 10
    d2 = (sum((base + [d1])[i] * (11 - i) for i in range(10)) * 10 % 11) % 10
    return "".join(map(str, base + [d1, d2]))


def _register(tag, club=None):
    r = requests.post(f"{API}/auth/register", json={
        "email": _email(tag), "password": "Teste@12345", "name": "Tester CPA",
        "birth_date": "1990-01-01", **({"club": club} if club else {})})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _register_club(tag):
    return _register(tag, club={"cpf": _valid_cpf(), "optin_email": True,
                                "accepted_terms": True, "accepted_privacy": True})


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


def _me(tok):
    r = requests.get(f"{API}/auth/me", headers=_h(tok))
    assert r.status_code == 200, r.text
    return r.json()


def _session(role):
    uid = f"test-cpa-{role}-{_RUN}-{uuid.uuid4().hex[:4]}"
    tok = f"test_session_cpa_{_RUN}_{uuid.uuid4().hex[:6]}"
    r = _mongo_eval(f"""
db.users.insertOne({{user_id:'{uid}', email:'{uid}@example.com', name:'Tester {role}', role:'{role}',
  password_version:1, mfa_enabled:true, marketing_opt_in:false, created_at:new Date().toISOString()}});
db.user_sessions.insertOne({{user_id:'{uid}', session_token:'{tok}', pv:1,
  expires_at:new Date(Date.now()+3600000).toISOString(), created_at:new Date().toISOString()}});""")
    assert r.returncode == 0, r.stderr
    return tok


def _wine():
    wines = requests.get(f"{API}/wines").json()
    w = next(w for w in wines if w.get("stock", 0) >= 5 and not w.get("archived"))
    return w


def _checkout(tok, wine, qty=1, redeem=None):
    body = {"items": [{"wine_id": wine["wine_id"], "variant_id": "default", "qty": qty}],
            "cep": CEP_OK, "address": "Av. Paulista, 1000", "birth_date": "1990-01-01",
            "payment_method": "pix", **({"redeem_points": redeem} if redeem else {})}
    return requests.post(f"{API}/checkout", headers=_h(tok), json=body)


def _quote(tok, wine, qty=1, redeem=None):
    body = {"items": [{"wine_id": wine["wine_id"], "variant_id": "default", "qty": qty}],
            "cep": CEP_OK, "address": "Av. Paulista, 1000", "birth_date": "1990-01-01",
            "payment_method": "pix", **({"redeem_points": redeem} if redeem else {})}
    return requests.post(f"{API}/checkout/quote", headers=_h(tok), json=body)


def _make_real(order_id):
    """Transforma o pedido em compra REAL de produção (só no banco de testes)."""
    r = _mongo_eval(f"db.orders.updateOne({{order_id:'{order_id}'}}, "
                    f"{{$set:{{demo:false, payment_mode:'mercadopago_live'}}}});")
    assert r.returncode == 0, r.stderr


def _confirm_demo(tok, order_id):
    return requests.post(f"{API}/orders/{order_id}/confirm-demo", headers=_h(tok))


def _points(tok):
    r = requests.get(f"{API}/account/points", headers=_h(tok))
    assert r.status_code == 200, r.text
    return r.json()


def _count(query):
    r = _mongo_eval(f"print(db.analytics_events.countDocuments({query}));")
    assert r.returncode == 0, r.stderr
    return int(r.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def owner():
    return _session("owner")


@pytest.fixture(scope="module")
def staff():
    return _session("staff")


@pytest.fixture(scope="module")
def zone_confirmed(owner):
    zones = requests.get(f"{API}/admin/shipping", headers=_h(owner)).json()
    for z in zones:
        if not z.get("confirmed"):
            r = requests.post(f"{API}/admin/shipping", headers=_h(owner), json={**z, "confirmed": True})
            assert r.status_code == 200, r.text
    return True


@pytest.fixture(scope="module")
def points_cfg(owner):
    r = requests.put(f"{API}/admin/settings", headers=_h(owner),
                     json={"points": {"earn_enabled": True, "reais_per_point": 5,
                                      "redeem_enabled": False, "point_value_brl": 0,
                                      "max_redeem_percent": 50}})
    assert r.status_code == 200, r.text
    return True


def _cron_secret():
    for line in open("/app/backend/.env"):
        if line.startswith("WEBHOOK_CRON_SECRET="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise AssertionError("WEBHOOK_CRON_SECRET ausente no backend/.env")


@pytest.fixture(scope="module", autouse=True)
def _cleanup():
    yield
    # Escopo por RUN: workers xdist rodam classes em paralelo no mesmo banco —
    # a limpeza não pode tocar nos dados do outro worker.
    # 1) Expira minhas reservas ativas e deixa o PRÓPRIO backend liberá-las
    #    (estoque + pontos + sync de exibição), sem vazar 'reserved' nas variantes.
    _mongo_eval(f"""
var re = new RegExp('^test-cpa-.*-{_RUN}-');
db.orders.updateMany({{user_email: re, reservation_active: true}},
  {{$set: {{reservation_expires_at: '2020-01-01T00:00:00+00:00'}}}});""")
    requests.post(f"{API}/cron/release-reservations",
                  headers={"Authorization": f"Bearer {_cron_secret()}"})
    time.sleep(2)  # cron faz ack imediato e processa em background
    # 2) Remove meus dados
    _mongo_eval(f"""
var re = new RegExp('^test-cpa-.*-{_RUN}-');
var uids = db.users.find({{email: re}}, {{user_id: 1}}).toArray().map(u => u.user_id);
db.points_ledger.deleteMany({{user_id: {{$in: uids}}}});
db.users.deleteMany({{email: re}});
db.user_sessions.deleteMany({{session_token: new RegExp('^test_session_cpa_{_RUN}_')}});
db.orders.deleteMany({{user_email: re}});""")


# ------------------- CLUBE -------------------
class TestClub:
    def test_register_sem_clube_inalterado(self):
        me = _me(_register("plain"))
        assert me["club_member"] is False
        assert me["optin_email"] is False and me["optin_whatsapp"] is False
        assert me["points_balance"] == 0

    def test_club_exige_aceites(self):
        r = requests.post(f"{API}/auth/register", json={
            "email": _email("noterms"), "password": "Teste@12345", "name": "T",
            "birth_date": "1990-01-01",
            "club": {"accepted_terms": False, "accepted_privacy": True}})
        assert r.status_code == 400
        r = requests.post(f"{API}/auth/register", json={
            "email": _email("noterms"), "password": "Teste@12345", "name": "T",
            "birth_date": "1990-01-01",
            "club": {"accepted_terms": True, "accepted_privacy": False}})
        assert r.status_code == 400

    def test_club_cpf_invalido(self):
        r = requests.post(f"{API}/auth/register", json={
            "email": _email("badcpf"), "password": "Teste@12345", "name": "T",
            "birth_date": "1990-01-01",
            "club": {"cpf": "12345678901", "accepted_terms": True, "accepted_privacy": True}})
        assert r.status_code == 400

    def test_club_join_completo_opcionais(self):
        cpf = _valid_cpf()
        tok = _register("full", club={
            "cpf": cpf, "phone": "11999990000",
            "address": {"cep": "01310-100", "street": "Av. Paulista", "number": "1000",
                        "city": "São Paulo", "state": "SP"},
            "optin_email": True, "optin_whatsapp": False,
            "accepted_terms": True, "accepted_privacy": True})
        me = _me(tok)
        assert me["club_member"] is True
        assert me["optin_email"] is True and me["optin_whatsapp"] is False
        data = requests.get(f"{API}/account/club", headers=_h(tok)).json()
        assert data["cpf"] == cpf
        assert data["terms_version"] == "1.0"
        assert data["address"]["cep"] == "01310100"

    def test_adesao_posterior_e_optins_separados(self):
        tok = _register("later")
        h = _h(tok)
        r = requests.patch(f"{API}/account/club", headers=h, json={"join": True})
        assert r.status_code == 400  # sem aceites
        r = requests.patch(f"{API}/account/club", headers=h,
                           json={"join": True, "accepted_terms": True, "accepted_privacy": True,
                                 "optin_whatsapp": True})
        assert r.status_code == 200 and r.json()["club_member"] is True
        me = _me(tok)
        assert me["club_member"] is True and me["optin_whatsapp"] is True and me["optin_email"] is False
        r = requests.patch(f"{API}/account/club", headers=h, json={"cpf": "11111111111"})
        assert r.status_code == 400  # CPF inválido mesmo na edição
        r = requests.patch(f"{API}/account/club", headers=h, json={"cpf": _valid_cpf()})
        assert r.status_code == 200


# ------------------- PONTOS -------------------
class TestPoints:
    def test_staff_nao_configura_pontos(self, staff):
        r = requests.put(f"{API}/admin/settings", headers=_h(staff), json={"points": {"reais_per_point": 10}})
        assert r.status_code == 403

    def test_resgate_exige_valor_do_ponto(self, owner, points_cfg):
        r = requests.put(f"{API}/admin/settings", headers=_h(owner), json={"points": {"redeem_enabled": True}})
        assert r.status_code == 400

    def test_demo_nao_gera_pontos(self, zone_confirmed, points_cfg):
        tok = _register_club("demo")
        r = _checkout(tok, _wine())
        assert r.status_code == 200, r.text
        assert _confirm_demo(tok, r.json()["order_id"]).status_code == 200
        pts = _points(tok)
        assert pts["balance"] == 0
        assert not any(e["type"] == "earn" for e in pts["history"])

    def test_compra_real_gera_pontos_floor_sem_frete(self, zone_confirmed, points_cfg):
        tok = _register_club("earn")
        w = _wine()
        q = _quote(tok, w).json()
        r = _checkout(tok, w)
        assert r.status_code == 200, r.text
        oid = r.json()["order_id"]
        _make_real(oid)
        assert _confirm_demo(tok, oid).status_code == 200
        expected = int((q["subtotal"] - q["discount"]) // 5)  # frete fora da base; floor
        assert expected > 0
        assert expected < int(q["total"] // 5)  # prova que o frete NÃO entrou
        pts = _points(tok)
        assert pts["balance"] == expected
        assert any(e["type"] == "earn" and e["order_id"] == oid for e in pts["history"])
        # idempotência: confirmar de novo não duplica
        assert _confirm_demo(tok, oid).status_code == 200
        assert _points(tok)["balance"] == expected

    def test_reembolso_total_estorna_pontos(self, owner, zone_confirmed, points_cfg):
        tok = _register_club("refund")
        r = _checkout(tok, _wine())
        oid = r.json()["order_id"]
        _make_real(oid)
        _confirm_demo(tok, oid)
        assert _points(tok)["balance"] > 0
        r = requests.post(f"{API}/admin/orders/{oid}/resolve-stock", headers=_h(owner),
                          json={"action": "refund", "reason": "teste de estorno total"})
        assert r.status_code == 200, r.text
        r = requests.post(f"{API}/admin/orders/{oid}/refund/confirm", headers=_h(owner))
        assert r.status_code == 200, r.text
        pts = _points(tok)
        assert pts["balance"] == 0
        assert any(e["type"] == "refund_revoke" and e["order_id"] == oid for e in pts["history"])

    def test_reembolso_parcial_proporcional(self, owner, zone_confirmed, points_cfg):
        tok = _register_club("partial")
        w = _wine()
        q = _quote(tok, w).json()
        r = _checkout(tok, w)
        oid = r.json()["order_id"]
        _make_real(oid)
        _confirm_demo(tok, oid)
        earned = _points(tok)["balance"]
        half = round(q["total"] / 2, 2)
        r = requests.post(f"{API}/admin/orders/{oid}/resolve-stock", headers=_h(owner),
                          json={"action": "refund", "reason": "teste parcial", "amount": half})
        assert r.status_code == 200, r.text
        r = requests.post(f"{API}/admin/orders/{oid}/refund/confirm", headers=_h(owner))
        assert r.status_code == 200, r.text
        expected_revoke = int(earned * (half / q["total"]))
        assert _points(tok)["balance"] == earned - expected_revoke

    def _enable_redeem(self, owner):
        r = requests.put(f"{API}/admin/settings", headers=_h(owner),
                         json={"points": {"redeem_enabled": True, "point_value_brl": 0.5,
                                          "max_redeem_percent": 50}})
        assert r.status_code == 200, r.text

    def _set_balance(self, tok, amount):
        uid = _me(tok)["user_id"]
        r = _mongo_eval(f"db.users.updateOne({{user_id:'{uid}'}}, {{$set:{{points_balance:{amount}}}}});")
        assert r.returncode == 0, r.stderr

    def test_resgate_desativado_rejeita(self, zone_confirmed, points_cfg):
        tok = _register_club("off")
        r = _quote(tok, _wine(), redeem=10)
        assert r.status_code == 400

    def test_resgate_fluxo_completo_e_uso_simultaneo(self, owner, zone_confirmed, points_cfg):
        self._enable_redeem(owner)
        tok = _register_club("redeem")
        self._set_balance(tok, 100)
        w = _wine()
        q = _quote(tok, w, redeem=60).json()
        assert q["points_used"] == 60 and q["points_discount"] == 30.0
        assert q["total"] == round(q["subtotal"] + q["shipping"] - q["discount"] - 30.0, 2)
        r = _checkout(tok, w, redeem=60)
        assert r.status_code == 200, r.text
        oid = r.json()["order_id"]
        assert _points(tok)["balance"] == 40  # reserva debitou
        # os mesmos pontos NUNCA podem ser usados em dois pedidos: o 2º checkout
        # só consegue resgatar o que restou (40), limitado pelo débito atômico
        r2 = _checkout(tok, w, redeem=50)
        assert r2.status_code == 200, r2.text
        assert r2.json()["quote"]["points_used"] == 40
        assert _points(tok)["balance"] == 0
        # com saldo zerado, qualquer novo resgate é rejeitado
        r3 = _checkout(tok, w, redeem=10)
        assert r3.status_code == 400
        # pagamento: resgate vira definitivo e o crédito usa a base APÓS o desconto de pontos
        _make_real(oid)
        _confirm_demo(tok, oid)
        expected_earn = int((q["subtotal"] - 30.0) // 5)
        pts = _points(tok)
        assert pts["balance"] == expected_earn
        types = [e["type"] for e in pts["history"]]
        assert "redeem_reserve" in types and "redeem" in types and "earn" in types
        order = next(o for o in requests.get(f"{API}/orders", headers=_h(tok)).json() if o["order_id"] == oid)
        assert order["points"]["status"] == "used"

    def test_resgate_liberado_na_expiracao(self, owner, zone_confirmed, points_cfg):
        self._enable_redeem(owner)
        tok = _register_club("expire")
        self._set_balance(tok, 50)
        w = _wine()
        r = _checkout(tok, w, redeem=50)
        assert r.status_code == 200, r.text
        oid = r.json()["order_id"]
        assert _points(tok)["balance"] == 0
        _mongo_eval(f"db.orders.updateOne({{order_id:'{oid}'}}, "
                    f"{{$set:{{reservation_expires_at:'2020-01-01T00:00:00+00:00'}}}});")
        # gatilho da liberação preguiçosa: outro cliente reserva o mesmo vinho
        tok2 = _register_club("trigger")
        r2 = _checkout(tok2, w)
        assert r2.status_code == 200, r2.text
        pts = _points(tok)
        assert pts["balance"] == 50  # pontos devolvidos
        assert any(e["type"] == "redeem_release" and e["order_id"] == oid for e in pts["history"])

    def test_resgate_reembolso_devolve_pontos(self, owner, zone_confirmed, points_cfg):
        self._enable_redeem(owner)
        tok = _register_club("rref")
        self._set_balance(tok, 80)
        w = _wine()
        q = _quote(tok, w, redeem=80).json()
        r = _checkout(tok, w, redeem=80)
        oid = r.json()["order_id"]
        _make_real(oid)
        _confirm_demo(tok, oid)
        earned = int((q["subtotal"] - q["points_discount"]) // 5)
        assert _points(tok)["balance"] == earned
        requests.post(f"{API}/admin/orders/{oid}/resolve-stock", headers=_h(owner),
                      json={"action": "refund", "reason": "teste reembolso com resgate"})
        r = requests.post(f"{API}/admin/orders/{oid}/refund/confirm", headers=_h(owner))
        assert r.status_code == 200, r.text
        pts = _points(tok)
        assert pts["balance"] == 80  # resgatados devolvidos, crédito estornado
        types = [e["type"] for e in pts["history"]]
        assert "redeem_restore" in types and "refund_revoke" in types


# ------------------- ANALYTICS -------------------
class TestAnalytics:
    def test_track_pageview_e_carrinho(self, owner):
        sid = f"sess-{uuid.uuid4().hex}"
        path = f"/teste-cpa-{uuid.uuid4().hex[:6]}"
        r = requests.post(f"{API}/analytics/track",
                          json={"type": "pageview", "path": path, "session_id": sid})
        assert r.status_code == 200 and r.json()["stored"] is True
        r = requests.post(f"{API}/analytics/track",
                          json={"type": "add_to_cart", "path": path, "session_id": sid, "wine_id": "wine_x"})
        assert r.json()["stored"] is True
        assert _count(f"{{path:'{path}'}}") == 2

    def test_dnt_e_gpc_respeitados(self):
        sid = f"sess-{uuid.uuid4().hex}"
        path = f"/teste-cpa-{uuid.uuid4().hex[:6]}"
        r = requests.post(f"{API}/analytics/track", headers={"DNT": "1"},
                          json={"type": "pageview", "path": path, "session_id": sid})
        assert r.json()["stored"] is False
        r = requests.post(f"{API}/analytics/track", headers={"Sec-GPC": "1"},
                          json={"type": "pageview", "path": path, "session_id": sid})
        assert r.json()["stored"] is False
        assert _count(f"{{path:'{path}'}}") == 0

    def test_admin_excluido(self, owner):
        sid = f"sess-{uuid.uuid4().hex}"
        path = f"/teste-cpa-{uuid.uuid4().hex[:6]}"
        r = requests.post(f"{API}/analytics/track", headers=_h(owner),
                          json={"type": "pageview", "path": path, "session_id": sid})
        assert r.json()["stored"] is False
        assert _count(f"{{path:'{path}'}}") == 0

    def test_path_invalido_rejeitado(self):
        sid = f"sess-{uuid.uuid4().hex}"
        for bad in ("https://evil.com", "//evil.com"):
            r = requests.post(f"{API}/analytics/track",
                              json={"type": "pageview", "path": bad, "session_id": sid})
            assert r.status_code in (400, 422)

    def test_eventos_confiaveis_vem_do_servidor(self, owner, zone_confirmed):
        before_signups = _count("{type:'signup'}")
        tok = _register("srv")
        assert _count("{type:'signup'}") >= before_signups + 1  # workers paralelos podem somar outros signups
        r = _checkout(tok, _wine())
        oid = r.json()["order_id"]
        assert _count(f"{{type:'checkout_start', order_id:'{oid}'}}") == 1
        _confirm_demo(tok, oid)
        assert _count(f"{{type:'purchase', order_id:'{oid}', demo:true}}") == 1
        an = requests.get(f"{API}/admin/analytics?days=30", headers=_h(owner)).json()
        assert an["signups"] >= 1
        assert an["purchases_demo"] >= 1
        assert "funnel" in an and "checkout_para_compra" in an["funnel"]

    def test_admin_analytics_acesso_e_forma(self, owner):
        r = requests.get(f"{API}/admin/analytics")
        assert r.status_code == 401
        tok = _register("cust")
        r = requests.get(f"{API}/admin/analytics", headers=_h(tok))
        assert r.status_code == 403
        r = requests.get(f"{API}/admin/analytics?days=7", headers=_h(owner))
        assert r.status_code == 200
        an = r.json()
        for k in ("pageviews", "unique_sessions", "add_to_cart", "checkout_starts",
                  "purchases", "signups", "top_pages", "top_wines", "funnel"):
            assert k in an
        assert an["retention_days"] == 180

    def test_retencao_ttl_180_dias(self):
        r = _mongo_eval("var i=db.analytics_events.getIndexes().find(x=>x.name==='created_dt_1');"
                        "print(i ? i.expireAfterSeconds : 'MISSING');")
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip().splitlines()[-1] == str(180 * 86400)
