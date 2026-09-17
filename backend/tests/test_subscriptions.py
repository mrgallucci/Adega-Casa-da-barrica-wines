"""Assinaturas de vinhos — suíte direcionada ISOLADA (8002/adega_test).

Cobre o checklist do proprietário:
- cliente novo e cliente já membro do Clube (adesão automática sem duplicar CPF,
  preservando opt-ins)
- contratação repetida sem duplicidade (idempotência por client_request_id)
- confirmação e falha de cobrança (demo e reconciliação via mock do MP)
- adesão antes/depois da data de corte (ciclo elegível)
- criação única de kit por ciclo + concorrência de estoque com a loja avulsa
- composição Premium (mínimo por nível) e brindes obrigatórios (Gran Cru/Premium)
- cancelamento sem multa, bloqueio de cobranças futuras e preservação de ciclos pagos
- sigilo dos critérios internos de Reserva nas respostas públicas
- permissões: staff não altera preço; cliente só vê a própria assinatura

Mock MP: porta 8899 (estado /tmp/mock_mp_state.json). Rodar com:
MP_API_BASE_URL=http://127.0.0.1:8899 MP_ACCESS_TOKEN=TEST-fake MP_HTTP_TIMEOUT=3
"""
import json
import os
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta

import pytest
import requests
from pymongo import MongoClient
from dotenv import dotenv_values

from conftest import API, TEST_DB as DB_NAME

_RUN = uuid.uuid4().hex[:6]
MOCK_STATE = "/tmp/mock_mp_state.json"
MOCK_PORT = 8899
CRON_SECRET = dotenv_values("/app/backend/.env").get("WEBHOOK_CRON_SECRET", "")
mdb = MongoClient("mongodb://localhost:27017")[DB_NAME]


def _write_mock(**kw):
    state = {}
    try:
        state = json.load(open(MOCK_STATE))
    except Exception:
        pass
    state.update(kw)
    json.dump(state, open(MOCK_STATE, "w"))


def _register(tag, club=False):
    body = {"email": f"test-sub-{tag}-{_RUN}-{uuid.uuid4().hex[:4]}@example.com",
            "password": "Teste@12345", "name": "Tester Sub", "birth_date": "1990-01-01"}
    if club:
        body["club"] = {"cpf": "52998224725", "phone": "11999990000",
                        "address": {"cep": "01310100", "city": "São Paulo", "state": "SP"},
                        "optin_email": True, "optin_whatsapp": False,
                        "accepted_terms": True, "accepted_privacy": True}
    r = requests.post(f"{API}/auth/register", json=body)
    assert r.status_code == 200, r.text
    return r.json()["token"], body["email"]


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


def _admin(role="owner"):
    uid = f"test-sub-{role}-{_RUN}-{uuid.uuid4().hex[:4]}"
    tok = f"test_session_sub_{_RUN}_{uuid.uuid4().hex[:6]}"
    mdb.users.insert_one({"user_id": uid, "email": f"{uid}@example.com", "name": f"{role} Sub",
                          "role": role, "password_version": 1, "mfa_enabled": True,
                          "created_at": datetime.now(timezone.utc).isoformat()})
    mdb.user_sessions.insert_one({"user_id": uid, "session_token": tok, "pv": 1,
                                  "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                                  "created_at": datetime.now(timezone.utc).isoformat()})
    return tok


@pytest.fixture(scope="module", autouse=True)
def env():
    try:
        requests.get(f"http://127.0.0.1:{MOCK_PORT}/v1/payments/0", timeout=1)
    except Exception:
        subprocess.Popen(["python", "tests/mock_mp_server.py"],
                         stdout=open("/tmp/mock_mp.log", "w"), stderr=subprocess.STDOUT)
        time.sleep(1.5)
    _write_mock(payments={}, search={}, preapproval_create={"code": 201, "body": {}},
                preapprovals={}, invoices={})
    owner = _admin("owner")
    # garante modo demo no início da suíte (settings persistem entre execuções)
    requests.put(f"{API}/admin/settings", headers=_h(owner), json={"payment_mode": "demo"})
    # frete: confirma zona do CEP de teste
    zones = requests.get(f"{API}/admin/shipping", headers=_h(owner)).json()
    for z in zones:
        if not z.get("confirmed"):
            requests.post(f"{API}/admin/shipping", headers=_h(owner), json={**z, "confirmed": True})
    # habilita assinaturas (ainda em demo, sem cobrança real)
    r = requests.put(f"{API}/admin/subscriptions/settings", headers=_h(owner),
                     json={"enabled": True, "info": "em teste"})
    assert r.status_code == 200, r.text
    yield owner
    mdb.users.delete_many({"email": {"$regex": f"^test-sub-"}})
    mdb.user_sessions.delete_many({"session_token": {"$regex": "^test_session_sub_"}})
    mdb.subscriptions.delete_many({"user_email": {"$regex": "^test-sub-"}})
    mdb.sub_charges.delete_many({})
    mdb.sub_kits.delete_many({})
    mdb.sub_kit_proposals.delete_many({})
    mdb.sub_cycles.delete_many({"key": {"$regex": "^T"}})
    mdb.sub_gifts.delete_many({"name": {"$regex": "^T-"}})
    mdb.sub_plans.delete_many({})  # recriados como rascunho no próximo startup do isolado


def _plans(owner):
    return requests.get(f"{API}/admin/subscriptions/plans", headers=_h(owner)).json()


def _publish_plan(owner, tier, price=100.0, bottles=2, wine_ids=None, kit_extra=None, **over):
    plan = next(p for p in _plans(owner) if p["tier"] == tier and p["status"] != "archived")
    kit = {"bottles": bottles, "volume_ml": 750, "levels": {}, "gifts": []}
    if kit_extra:
        kit.update(kit_extra)
    body = {"name": plan["name"], "tier": tier, "tagline": plan.get("tagline", ""),
            "description": plan.get("description", ""), "image": "", "price": price,
            "billing": {"frequency": 1, "frequency_type": "months"},
            "kit": kit, "eligibility": {"wine_ids": wine_ids or []},
            "internal": plan.get("internal", {}),
            "limits": {"max_subscribers": None},
            "regions": {"countries": ["BR"], "states": [], "international_enabled": False, "taxes_note": ""},
            "shipping": {"mode": "included", "custom_price": None, "promo_note": ""},
            "points_eligible": over.pop("points_eligible", False),
            "substitution": {"allow": False, "note": ""}, "no_repeat_window": 0}
    body.update(over)
    r = requests.put(f"{API}/admin/subscriptions/plans/{plan['plan_id']}", headers=_h(owner), json=body)
    assert r.status_code == 200, r.text
    r = requests.post(f"{API}/admin/subscriptions/plans/{plan['plan_id']}/publish", headers=_h(owner))
    assert r.status_code == 200, r.text
    return plan["plan_id"]


def _mk_cycle(owner, key, cutoff_in_hours=72):
    body = {"key": key,
            "cutoff_at": (datetime.now(timezone.utc) + timedelta(hours=cutoff_in_hours)).isoformat(),
            "prep_at": (datetime.now(timezone.utc) + timedelta(hours=cutoff_in_hours + 24)).isoformat(),
            "ship_at": (datetime.now(timezone.utc) + timedelta(hours=cutoff_in_hours + 48)).isoformat(),
            "delivery_estimate": "3 a 8 dias úteis após a postagem"}
    r = requests.post(f"{API}/admin/subscriptions/cycles", headers=_h(owner), json=body)
    assert r.status_code == 200, r.text
    return r.json()["cycle_id"]


def _wine_ids(n=4):
    wines = [w for w in requests.get(f"{API}/wines").json() if not w.get("archived") and w.get("stock", 0) >= 10]
    return [w["wine_id"] for w in wines[:n]]


def _close_open_cycles(owner):
    for c in requests.get(f"{API}/admin/subscriptions/cycles", headers=_h(owner)).json():
        if c["status"] == "open":
            requests.post(f"{API}/admin/subscriptions/cycles/{c['cycle_id']}/status",
                          headers=_h(owner), json={"status": "closed"})


ADDR = {"country": "BR", "cep": "01310100", "street": "Av. Paulista", "number": "1000",
        "complement": "", "district": "Bela Vista", "city": "São Paulo", "state": "SP"}


def _subscribe(tok, plan_id, req_id=None, card_token=None):
    body = {"plan_id": plan_id, "client_request_id": req_id or uuid.uuid4().hex,
            "birth_date": "1990-01-01", "address": ADDR, "accepted_terms": True}
    if card_token:
        body["card_token_id"] = card_token
    return requests.post(f"{API}/subscriptions/subscribe", headers=_h(tok), json=body)


def _confirm_demo(tok):
    r = requests.post(f"{API}/subscriptions/mine/confirm-demo", headers=_h(tok))
    assert r.status_code == 200, r.text


class TestPlansSeedAndDrafts:
    def test_five_draft_plans_seeded(self, env):
        plans = _plans(env)
        tiers = [p["tier"] for p in plans if p["created_by"] == "seed"]
        assert tiers == ["entrada", "reserva", "gran_reserva", "gran_cru", "premium"]
        assert all(p["status"] == "draft" for p in plans)
        assert all(p["price"] is None for p in plans)  # preços NÃO inventados

    def test_public_list_empty_until_published(self, env):
        r = requests.get(f"{API}/subscriptions/plans")
        assert r.status_code == 200
        assert r.json()["plans"] == []

    def test_publish_requires_price(self, env):
        plan = next(p for p in _plans(env) if p["tier"] == "entrada")
        r = requests.post(f"{API}/admin/subscriptions/plans/{plan['plan_id']}/publish", headers=_h(env))
        assert r.status_code == 400


class TestSubscribeAndClub:
    def test_new_customer_becomes_club_member_on_first_charge(self, env):
        pid = _publish_plan(env, "entrada", price=100.0, bottles=2, wine_ids=_wine_ids(3))
        _mk_cycle(env, "T-NEW", 72)
        tok, email = _register("new")
        r = _subscribe(tok, pid)
        assert r.status_code == 200, r.text
        assert r.json()["mode"] == "demo"
        # antes da 1ª cobrança: ainda não é membro
        assert mdb.users.find_one({"email": email}).get("club_member") is not True
        _confirm_demo(tok)
        u = mdb.users.find_one({"email": email})
        assert u.get("club_member") is True
        assert u.get("club_origin") == "assinatura"
        # demo NÃO credita pontos comerciais
        assert u.get("points_balance", 0) == 0
        sub = mdb.subscriptions.find_one({"user_email": email})
        assert sub["status"] == "active"
        assert sub["club_adhesion"]["applied"] is True
        assert sub["club_adhesion"]["terms_version"]

    def test_existing_club_member_keeps_cpf_and_optins(self, env):
        pid = _publish_plan(env, "reserva", price=150.0, bottles=2, wine_ids=_wine_ids(3))
        tok, email = _register("club", club=True)
        before = mdb.users.find_one({"email": email})
        r = _subscribe(tok, pid)
        assert r.status_code == 200, r.text
        _confirm_demo(tok)
        after = mdb.users.find_one({"email": email})
        assert after["user_id"] == before["user_id"]  # mesma conta, sem duplicar
        assert (after.get("club") or {}).get("cpf") == (before.get("club") or {}).get("cpf")
        assert after.get("optin_email") == before.get("optin_email")  # opt-ins preservados
        assert after.get("optin_whatsapp") == before.get("optin_whatsapp")

    def test_duplicate_subscribe_is_idempotent(self, env):
        pid = _publish_plan(env, "gran_reserva", price=300.0, bottles=2, wine_ids=_wine_ids(3))
        tok, _ = _register("dup")
        rid = uuid.uuid4().hex
        r1 = _subscribe(tok, pid, rid)
        r2 = _subscribe(tok, pid, rid)
        assert r1.json()["sub_id"] == r2.json()["sub_id"]
        assert r2.json().get("idempotent") is True
        # segunda adesão ao MESMO plano com outro request id → 409
        r3 = _subscribe(tok, pid)
        assert r3.status_code == 409
        assert mdb.subscriptions.count_documents({"plan_id": pid}) == 1


class TestCycleCutoff:
    def test_after_cutoff_goes_to_next_cycle(self, env):
        pid = _publish_plan(env, "entrada", price=100.0, bottles=2, wine_ids=_wine_ids(3))
        _close_open_cycles(env)
        past = _mk_cycle(env, "T-PAST", cutoff_in_hours=-1)   # corte já passou
        future = _mk_cycle(env, "T-FUT", cutoff_in_hours=96)
        tok, email = _register("cutoff")
        r = _subscribe(tok, pid)
        assert r.status_code == 200, r.text
        assert r.json()["first_cycle"]["key"] == "T-FUT"
        sub = mdb.subscriptions.find_one({"user_email": email})
        assert sub["first_cycle_id"] == future and sub["first_cycle_id"] != past


class TestKitCuration:
    def _paid_subscriber(self, env, plan_id, tag):
        tok, email = _register(tag)
        r = _subscribe(tok, plan_id)
        assert r.status_code == 200, r.text
        _confirm_demo(tok)
        return mdb.subscriptions.find_one({"user_email": email})

    def test_single_kit_per_cycle_and_stock_concurrency(self, env):
        wids = _wine_ids(3)
        pid = _publish_plan(env, "entrada", price=100.0, bottles=2, wine_ids=wids)
        _close_open_cycles(env)
        cyc = _mk_cycle(env, "T-KIT", 72)
        self._paid_subscriber(env, pid, "kit1")
        self._paid_subscriber(env, pid, "kit2")
        # proposta
        r = requests.post(f"{API}/admin/subscriptions/plans/{pid}/cycles/{cyc}/propose", headers=_h(env))
        assert r.status_code == 200, r.text
        prop = r.json()
        assert len(prop["subscribers"]) == 2
        assert not prop["issues"], prop["issues"]
        # confirma → 2 kits; reconfirmar NÃO duplica
        r = requests.post(f"{API}/admin/subscriptions/proposals/{pid}/{cyc}/confirm", headers=_h(env))
        assert r.status_code == 200 and r.json()["kits"] == 2, r.text
        r = requests.post(f"{API}/admin/subscriptions/proposals/{pid}/{cyc}/confirm", headers=_h(env))
        assert r.json().get("idempotent") is True
        assert mdb.sub_kits.count_documents({"cycle_id": cyc}) == 2
        # estoque reservado: 2 garrafas de cada rótulo da composição
        for it in prop["composition"]:
            w = mdb.wines.find_one({"wine_id": it["wine_id"]})
            assert w["variants"][0]["reserved"] >= it["qty"] * 2
        # concorrência com loja avulsa: esgota o estoque disponível de um rótulo
        w0 = prop["composition"][0]
        w = mdb.wines.find_one({"wine_id": w0["wine_id"]})
        avail = w["variants"][0]["stock"] - w["variants"][0]["reserved"]
        tok, _ = _register("avulsa")
        r = requests.post(f"{API}/checkout", headers=_h(tok), json={
            "items": [{"wine_id": w0["wine_id"], "variant_id": "default", "qty": avail}],
            "cep": "01310100", "address": "Rua X, 1", "birth_date": "1990-01-01",
            "payment_method": "pix"})
        assert r.status_code == 200, r.text  # loja avulsa consome o restante
        r = requests.post(f"{API}/checkout", headers=_h(tok), json={
            "items": [{"wine_id": w0["wine_id"], "variant_id": "default", "qty": 1}],
            "cep": "01310100", "address": "Rua X, 1", "birth_date": "1990-01-01",
            "payment_method": "pix"})
        assert r.status_code == 409  # não vende a mesma unidade duas vezes

    def test_premium_requires_levels_and_gift(self, env):
        owner = env
        # presente obrigatório: publicar Gran Cru sem brinde → 400
        plan_gc = next(p for p in _plans(owner) if p["tier"] == "gran_cru")
        body = {"name": plan_gc["name"], "tier": "gran_cru", "tagline": "", "description": "",
                "image": "", "price": 500.0, "billing": {"frequency": 1, "frequency_type": "months"},
                "kit": {"bottles": 2, "volume_ml": 750, "levels": {}, "gifts": []},
                "eligibility": {"wine_ids": _wine_ids(2)}, "internal": plan_gc.get("internal", {}),
                "limits": {"max_subscribers": None},
                "regions": {"countries": ["BR"], "states": [], "international_enabled": False, "taxes_note": ""},
                "shipping": {"mode": "included", "custom_price": None, "promo_note": ""},
                "points_eligible": False, "substitution": {"allow": False, "note": ""}, "no_repeat_window": 0}
        requests.put(f"{API}/admin/subscriptions/plans/{plan_gc['plan_id']}", headers=_h(owner), json=body)
        r = requests.post(f"{API}/admin/subscriptions/plans/{plan_gc['plan_id']}/publish", headers=_h(owner))
        assert r.status_code == 400 and "brinde" in r.json()["detail"].lower()
        # cadastra brinde e publica
        g = requests.post(f"{API}/admin/subscriptions/gifts", headers=_h(owner),
                          json={"name": "T-Abridor", "description": "", "stock": 50, "active": True})
        gift_id = g.json()["gift_id"]
        body["kit"]["gifts"] = [{"gift_id": gift_id, "qty": 1}]
        requests.put(f"{API}/admin/subscriptions/plans/{plan_gc['plan_id']}", headers=_h(owner), json=body)
        r = requests.post(f"{API}/admin/subscriptions/plans/{plan_gc['plan_id']}/publish", headers=_h(owner))
        assert r.status_code == 200, r.text
        # Premium: um por nível + brinde
        pid = _publish_plan(owner, "premium", price=900.0,
                            kit_extra={"levels": {l: 1 for l in ["entrada", "reserva", "gran_reserva", "gran_cru"]},
                                       "gifts": [{"gift_id": gift_id, "qty": 1}]},
                            wine_ids=[])
        # níveis precisam de rótulos elegíveis nos planos de cada nível
        # (Reserva com filtro de faixa DESLIGADO: os vinhos seed são acima de R$ 200)
        for t in ["entrada", "reserva", "gran_reserva"]:
            _publish_plan(owner, t, price=100.0, bottles=2, wine_ids=_wine_ids(3),
                          internal={"price_ref": "price", "apply_price_filter": False,
                                    "reserva_min": 80.0, "reserva_max": 200.0,
                                    "cost": None, "margin_note": "", "admin_notes": ""})
        _close_open_cycles(owner)
        cyc = _mk_cycle(owner, "T-PREM", 72)
        self._paid_subscriber(owner, pid, "prem")
        r = requests.post(f"{API}/admin/subscriptions/plans/{pid}/cycles/{cyc}/propose", headers=_h(owner))
        prop = r.json()
        assert not prop["issues"], prop["issues"]
        levels_in_kit = {it["level"] for it in prop["composition"]}
        assert levels_in_kit == {"entrada", "reserva", "gran_reserva", "gran_cru"}
        assert prop["gifts"] and prop["gifts"][0]["gift_id"] == gift_id
        r = requests.post(f"{API}/admin/subscriptions/proposals/{pid}/{cyc}/confirm", headers=_h(owner))
        assert r.status_code == 200, r.text
        gift = mdb.sub_gifts.find_one({"gift_id": gift_id})
        assert gift["reserved"] >= 1


class TestCancel:
    def test_cancel_demo_blocks_charges_and_preserves_history(self, env):
        pid = _publish_plan(env, "entrada", price=100.0, bottles=2, wine_ids=_wine_ids(3))
        _mk_cycle(env, "T-CANC", 72)
        tok, email = _register("cancel")
        _subscribe(tok, pid)
        _confirm_demo(tok)
        r = requests.post(f"{API}/subscriptions/mine/cancel", headers=_h(tok))
        assert r.status_code == 200 and r.json()["status"] == "canceled", r.text
        sub = mdb.subscriptions.find_one({"user_email": email})
        assert sub["charges_blocked"] is True
        # cancelar de novo é idempotente
        r = requests.post(f"{API}/subscriptions/mine/cancel", headers=_h(tok))
        assert r.json().get("idempotent") is True
        # histórico segue acessível e conta/Clube preservados
        r = requests.get(f"{API}/subscriptions/mine", headers=_h(tok))
        assert r.status_code == 200 and r.json()["subscription"]["status"] == "canceled"
        u = mdb.users.find_one({"email": email})
        assert u.get("club_member") is True and u.get("deleted") is not True


class TestMpReconciliation:
    def _mp_sub(self, env, tok, tag):
        """Assinatura com payment_mode mercadopago_test via mock."""
        requests.put(f"{API}/admin/settings", headers=_h(env), json={"payment_mode": "mercadopago_test"})
        pid = _publish_plan(env, "entrada", price=120.0, bottles=2, wine_ids=_wine_ids(3))
        _close_open_cycles(env)
        _mk_cycle(env, f"T-MP-{tag}", 72)
        r = _subscribe(tok, pid, card_token="tok_teste_ficticio")  # token fake — mock do MP
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "active"  # mock cria authorized
        pre_id = mdb.subscriptions.find_one({"sub_id": r.json()["sub_id"]})["mp_preapproval_id"]
        return r.json()["sub_id"], pre_id

    def _run_cron(self):
        r = requests.post(f"{API}/cron/reconcile-subscriptions",
                          headers={"Authorization": f"Bearer {CRON_SECRET}"})
        assert r.status_code == 200, r.text
        time.sleep(2.5)

    def test_charge_approved_via_polling_no_webhook(self, env):
        tok, email = _register("mpok")
        sub_id, pre_id = self._mp_sub(env, tok, "OK")
        _write_mock(invoices={pre_id: {"code": 200, "body": {
            "results": [{"id": "ap_1", "transaction_amount": 120.0,
                         "payment": {"status": "approved"}}], "paging": {"total": 1}}}})
        self._run_cron()
        ch = mdb.sub_charges.find_one({"sub_id": sub_id})
        assert ch["status"] == "approved"
        sub = mdb.subscriptions.find_one({"sub_id": sub_id})
        assert sub["status"] == "active"
        # reconciliação repetida NÃO duplica cobrança nem efeitos
        self._run_cron()
        assert mdb.sub_charges.count_documents({"sub_id": sub_id}) == 1
        # homologação NÃO credita pontos
        assert mdb.users.find_one({"email": email}).get("points_balance", 0) == 0
        requests.put(f"{API}/admin/settings", headers=_h(env), json={"payment_mode": "demo"})

    def test_charge_rejected_marks_delinquent(self, env):
        tok, _ = _register("mpfail")
        sub_id, pre_id = self._mp_sub(env, tok, "FAIL")
        _write_mock(invoices={pre_id: {"code": 200, "body": {
            "results": [{"id": "ap_2", "transaction_amount": 120.0,
                         "payment": {"status": "rejected"}}], "paging": {"total": 1}}}})
        self._run_cron()
        sub = mdb.subscriptions.find_one({"sub_id": sub_id})
        assert sub["status"] == "delinquent"
        # cancelamento com provedor confirmando
        r = requests.post(f"{API}/subscriptions/mine/cancel", headers=_h(tok))
        assert r.json()["status"] == "canceled", r.text
        pre = _write_mock()  # mock aplica PUT canceled no estado
        saved = json.load(open(MOCK_STATE))
        assert saved["preapprovals"][pre_id]["body"]["status"] == "canceled"
        requests.put(f"{API}/admin/settings", headers=_h(env), json={"payment_mode": "demo"})

    def test_cancel_pending_when_provider_fails(self, env):
        tok, _ = _register("mppend")
        sub_id, pre_id = self._mp_sub(env, tok, "PEND")
        mdb.subscriptions.update_one({"sub_id": sub_id}, {"$set": {"status": "active"}})
        # provedor falha no cancelamento → cancel_pending, cobranças bloqueadas
        json.dump({"payments": {}, "search": {}, "preapprovals": {},
                   "invoices": {}, "broken_cancel": True}, open(MOCK_STATE, "w"))
        r = requests.post(f"{API}/subscriptions/mine/cancel", headers=_h(tok))
        assert r.json()["status"] == "cancel_pending", r.text
        sub = mdb.subscriptions.find_one({"sub_id": sub_id})
        assert sub["charges_blocked"] is True and sub["status"] == "cancel_pending"
        requests.put(f"{API}/admin/settings", headers=_h(env), json={"payment_mode": "demo"})


class TestSecurityAndPermissions:
    def test_reserva_internal_range_never_public(self, env):
        owner = env
        plan = next(p for p in _plans(owner) if p["tier"] == "reserva")
        pid = _publish_plan(owner, "reserva", price=150.0, bottles=2, wine_ids=_wine_ids(2))
        for url in (f"{API}/subscriptions/plans", f"{API}/subscriptions/plans/{pid}"):
            r = requests.get(url)
            body = json.dumps(r.json())
            assert "reserva_min" not in body and "reserva_max" not in body
            assert "internal" not in body and "margin_note" not in body
            assert "price_ref" not in body

    def test_staff_cannot_change_commercial_fields(self, env):
        staff = _admin("staff")
        plan = next(p for p in _plans(env) if p["tier"] == "entrada")
        body = {"name": plan["name"], "tier": plan["tier"], "tagline": plan.get("tagline", ""),
                "description": "desc staff ok", "image": "", "price": 9999.0,
                "billing": plan["billing"], "kit": plan["kit"], "eligibility": plan["eligibility"],
                "internal": plan.get("internal", {}), "limits": plan.get("limits", {}),
                "regions": plan["regions"], "shipping": plan["shipping"],
                "points_eligible": plan.get("points_eligible", False),
                "substitution": plan.get("substitution", {}), "no_repeat_window": 0}
        r = requests.put(f"{API}/admin/subscriptions/plans/{plan['plan_id']}",
                         headers=_h(staff), json=body)
        assert r.status_code == 403
        # staff NÃO publica nem arquiva
        assert requests.post(f"{API}/admin/subscriptions/plans/{plan['plan_id']}/publish",
                             headers=_h(staff)).status_code == 403
        assert requests.post(f"{API}/admin/subscriptions/plans/{plan['plan_id']}/archive",
                             headers=_h(staff), json={}).status_code == 403
        # staff edita apenas campos editoriais (sem mexer no comercial)
        body["price"] = plan.get("price")
        r = requests.put(f"{API}/admin/subscriptions/plans/{plan['plan_id']}",
                         headers=_h(staff), json=body)
        assert r.status_code == 200, r.text

    def test_reserva_price_filter_applied_and_editable(self, env):
        """Faixa interna de Reserva filtra o pool administrativo e é editável."""
        owner = env
        wids = _wine_ids(3)
        pid = _publish_plan(owner, "reserva", price=150.0, bottles=2, wine_ids=wids,
                            internal={"price_ref": "price", "apply_price_filter": True,
                                      "reserva_min": 80.0, "reserva_max": 200.0,
                                      "cost": None, "margin_note": "", "admin_notes": ""})
        r = requests.get(f"{API}/admin/subscriptions/eligible-wines/{pid}", headers=_h(owner))
        assert r.status_code == 200
        assert r.json()["pool"] == []  # seeds são todos acima de R$ 200
        # desliga o filtro → pool aparece
        pid = _publish_plan(owner, "reserva", price=150.0, bottles=2, wine_ids=wids,
                            internal={"price_ref": "price", "apply_price_filter": False,
                                      "reserva_min": 80.0, "reserva_max": 200.0,
                                      "cost": None, "margin_note": "", "admin_notes": ""})
        r = requests.get(f"{API}/admin/subscriptions/eligible-wines/{pid}", headers=_h(owner))
        assert len(r.json()["pool"]) == 3

    def test_customer_sees_only_own_subscription(self, env):
        pid = _publish_plan(env, "entrada", price=100.0, bottles=2, wine_ids=_wine_ids(3))
        _mk_cycle(env, "T-PRIV", 72)
        tok_a, _ = _register("priv-a")
        tok_b, _ = _register("priv-b")
        _subscribe(tok_a, pid)
        r = requests.get(f"{API}/subscriptions/mine", headers=_h(tok_b))
        assert r.status_code == 404
        # B não confirma recebimento de kit de A
        kit = mdb.sub_kits.find_one({}) or {}
        if kit:
            r = requests.post(f"{API}/subscriptions/mine/kits/{kit['kit_id']}/confirm-received",
                              headers=_h(tok_b))
            assert r.status_code == 400
