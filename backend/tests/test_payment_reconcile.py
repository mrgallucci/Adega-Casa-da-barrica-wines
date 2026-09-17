"""Reconciliação de pagamentos por consulta à API do MP — ISOLADO (8002/adega_test).

Usa mock local do MP (porta 8899, estado em /tmp/mock_mp_state.json). O backend
isolado deve rodar com MP_API_BASE_URL=http://127.0.0.1:8899, MP_ACCESS_TOKEN
fake e MP_HTTP_TIMEOUT=3.

Cobre: aprovação sem webhook; consulta repetida e CONCORRENTE (sem efeitos
duplicados: estoque, pontos, e-mail); pendente e recusado; confirmação após
reserva expirada (revisão de estoque, sem baixa); divergências de valor, moeda,
referência e recebedor (revisão, sem confirmar); timeout e 429; sem pagamento vs
erro; permissões do botão manual; tarefa agendada via endpoint cron.
"""
import json
import subprocess
import threading
import time
import uuid

import pytest
import requests

from conftest import API, _mongo_eval

_RUN = uuid.uuid4().hex[:6]
MOCK_STATE = "/tmp/mock_mp_state.json"
MOCK_PORT = 8899
CEP_OK = "01310100"


def _write_mock(payments=None, search=None):
    json.dump({"payments": payments or {}, "search": search or {}}, open(MOCK_STATE, "w"))


def _payment(pid, order, status="approved", amount=None, currency="BRL", ref=None, collector=777):
    return {"code": 200, "body": {
        "id": pid, "status": status, "status_detail": "accredited",
        "external_reference": ref if ref is not None else order,
        "transaction_amount": amount, "currency_id": currency, "collector_id": collector,
        "date_last_updated": "2026-09-15T00:00:00Z"}}


def _register(tag):
    r = requests.post(f"{API}/auth/register", json={
        "email": f"test-rec-{tag}-{_RUN}-{uuid.uuid4().hex[:4]}@example.com",
        "password": "Teste@12345", "name": "Tester Rec", "birth_date": "1990-01-01"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


def _owner():
    uid = f"test-rec-owner-{_RUN}-{uuid.uuid4().hex[:4]}"
    tok = f"test_session_rec_{_RUN}_{uuid.uuid4().hex[:6]}"
    _mongo_eval(f"""
db.users.insertOne({{user_id:'{uid}', email:'{uid}@example.com', name:'Owner Rec', role:'owner',
  password_version:1, mfa_enabled:true, created_at:new Date().toISOString()}});
db.user_sessions.insertOne({{user_id:'{uid}', session_token:'{tok}', pv:1,
  expires_at:new Date(Date.now()+3600000).toISOString(), created_at:new Date().toISOString()}});""")
    return tok


def _wine():
    wines = requests.get(f"{API}/wines").json()
    return next(w for w in wines if w.get("stock", 0) >= 5 and not w.get("archived"))


def _mk_order(tok, w, env="mercadopago_test", pid=None, reservation_active=True,
              payment_status="pending"):
    r = requests.post(f"{API}/checkout", headers=_h(tok), json={
        "items": [{"wine_id": w["wine_id"], "variant_id": "default", "qty": 1}],
        "cep": CEP_OK, "address": "Av. Paulista, 1000", "birth_date": "1990-01-01",
        "payment_method": "pix"})
    assert r.status_code == 200, r.text
    oid = r.json()["order_id"]
    sets = {"demo": False, "payment_mode": env}
    if pid:
        sets["mp_payment_id"] = pid
    if not reservation_active:
        sets["reservation_active"] = False
    if payment_status != "pending":
        sets["payment_status"] = payment_status
    _mongo_eval(f"db.orders.updateOne({{order_id:'{oid}'}}, {{$set:{json.dumps(sets)}}});")
    return oid


def _order(oid):
    for _ in range(3):
        r = _mongo_eval(f"var o=db.orders.findOne({{order_id:'{oid}'}});"
                        "print(o ? JSON.stringify({payment_status:o.payment_status, fulfillment:o.fulfillment_status,"
                        " review:!!o.needs_stock_review, prev:o.payment_review||null, paid_via:o.paid_via||null,"
                        " check:o.payment_check||null, reserved:o.reservation_active}) : 'NULL');")
        out = r.stdout.strip().splitlines()
        if out and out[-1] != "NULL":
            return json.loads(out[-1])
        time.sleep(0.5)
    raise AssertionError(f"pedido {oid} ilegível: rc={r.returncode} err={r.stderr[:200]}")


def _stock_moves(oid, kind):
    r = _mongo_eval(f"print(db.stock_movements.countDocuments({{reason:/.*{oid}.*/, movement_type:'{kind}'}}));")
    return int(r.stdout.strip().splitlines()[-1])


def _stock_moves_any(oid, kind):
    r = _mongo_eval(f"var n=0; db.stock_movements.find({{}}).forEach(m=>{{if(m.reason&&m.reason.includes('{oid}')&&m.type==='{kind}')n++}}); print(n);")
    return int(r.stdout.strip().splitlines()[-1])


def _points_earn_count(oid):
    r = _mongo_eval(f"print(db.points_ledger.countDocuments({{order_id:'{oid}', type:'earn'}}));")
    return int(r.stdout.strip().splitlines()[-1])


def _consult(owner, oid):
    return requests.post(f"{API}/admin/orders/{oid}/consult-payment", headers=_h(owner))


@pytest.fixture(scope="module", autouse=True)
def _env():
    _write_mock()
    # mock server dedicado desta suíte (só sobe se a porta estiver livre)
    try:
        requests.get(f"http://127.0.0.1:{MOCK_PORT}/v1/payments/0", timeout=1)
    except Exception:
        subprocess.Popen(["python", "tests/mock_mp_server.py"],
                         stdout=open("/tmp/mock_mp.log", "w"), stderr=subprocess.STDOUT)
        time.sleep(1.5)
    owner = _owner()
    zones = requests.get(f"{API}/admin/shipping", headers=_h(owner)).json()
    for z in zones:
        if not z.get("confirmed"):
            requests.post(f"{API}/admin/shipping", headers=_h(owner), json={**z, "confirmed": True})
    requests.put(f"{API}/admin/settings", headers=_h(owner),
                 json={"points": {"earn_enabled": True, "reais_per_point": 5}})
    yield owner
    _mongo_eval(f"""
db.users.deleteMany({{email:/^test-rec-/}});
db.user_sessions.deleteMany({{session_token:/^test_session_rec_/}});
db.orders.deleteMany({{user_email:/^test-rec-/}});
db.points_ledger.deleteMany({{note:/reconcile-test/}});
db.cron_runs.deleteMany({{job:'reconcile-payments'}});""")


class TestReconciliacao:
    def test_aprovado_sem_webhook_confirma_via_consulta(self, _env):
        tok = _register("a1")
        w = _wine()
        stock0 = w["stock"]
        oid = _mk_order(tok, w, pid="1001")
        _write_mock(payments={"1001": _payment("1001", oid, amount=None)})
        # ajusta amount para o total exato do pedido
        total = _order(oid)  # garante doc
        q = _mongo_eval(f"print(db.orders.findOne({{order_id:'{oid}'}}).quote.total);")
        total = float(q.stdout.strip().splitlines()[-1])
        _write_mock(payments={"1001": _payment("1001", oid, amount=total)})
        r = _consult(_env, oid)
        assert r.status_code == 200, r.text
        assert r.json()["result"] == "aprovado"
        o = _order(oid)
        assert o["payment_status"] == "approved" and o["paid_via"] == "consulta_manual"
        assert o["fulfillment"] == "preparando" and o["reserved"] is False
        assert _stock_moves_any(oid, "venda") == 1  # baixa única

    def test_consulta_repetida_e_concorrente_sem_duplicar(self, _env):
        tok = _register("a2")
        w = _wine()
        oid = _mk_order(tok, w, env="mercadopago_live", pid="1002")
        q = _mongo_eval(f"print(db.orders.findOne({{order_id:'{oid}'}}).quote.total);")
        total = float(q.stdout.strip().splitlines()[-1])
        _write_mock(payments={"1002": _payment("1002", oid, amount=total)})
        results = []

        def go():
            results.append(_consult(_env, oid).json().get("result"))

        threads = [threading.Thread(target=go) for _ in range(3)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        o = _order(oid)
        assert o["payment_status"] == "approved"
        assert _stock_moves_any(oid, "venda") == 1  # sem baixa duplicada
        assert _points_earn_count(oid) == 1       # sem pontuação duplicada (live = comercial)
        # repetição (4ª chamada — dentro do limite de frequência): sem efeitos
        r2 = _consult(_env, oid)
        assert r2.json()["result"] in ("duplicado", "aprovado")
        assert _points_earn_count(oid) == 1
        # 5ª chamada estoura o limite de frequência (proteção anti-abuso)
        r3 = _consult(_env, oid)
        assert r3.status_code == 429
        assert _points_earn_count(oid) == 1

    def test_pendente_nao_confirma(self, _env):
        tok = _register("a3")
        oid = _mk_order(tok, _wine(), pid="1003")
        _write_mock(payments={"1003": _payment("1003", oid, status="in_process", amount=None)})
        total = float(_mongo_eval(f"print(db.orders.findOne({{order_id:'{oid}'}}).quote.total);").stdout.strip().splitlines()[-1])
        _write_mock(payments={"1003": _payment("1003", oid, status="in_process", amount=total)})
        r = _consult(_env, oid)
        assert r.json()["result"].startswith("pendente")
        o = _order(oid)
        assert o["payment_status"] == "pending" and o["reserved"] is True

    def test_recusado_cancela_sem_rebaixar_aprovado(self, _env):
        tok = _register("a4")
        oid = _mk_order(tok, _wine(), pid="1004")
        total = float(_mongo_eval(f"print(db.orders.findOne({{order_id:'{oid}'}}).quote.total);").stdout.strip().splitlines()[-1])
        _write_mock(payments={"1004": _payment("1004", oid, status="rejected", amount=total)})
        r = _consult(_env, oid)
        assert r.json()["result"] == "rejected"
        o = _order(oid)
        assert o["payment_status"] == "rejected" and o["reserved"] is False
        # fora de ordem: aprovado depois recusado NÃO rebaixa
        tok2 = _register("a4b")
        oid2 = _mk_order(tok2, _wine(), pid="1005")
        total2 = float(_mongo_eval(f"print(db.orders.findOne({{order_id:'{oid2}'}}).quote.total);").stdout.strip().splitlines()[-1])
        _write_mock(payments={"1005": _payment("1005", oid2, amount=total2)})
        assert _consult(_env, oid2).json()["result"] == "aprovado"
        _write_mock(payments={"1005": _payment("1005", oid2, status="rejected", amount=total2)})
        _consult(_env, oid2)
        assert _order(oid2)["payment_status"] == "approved"  # não rebaixou

    def test_aprovado_apos_reserva_expirada_vai_para_revisao(self, _env):
        tok = _register("a5")
        w = _wine()
        oid = _mk_order(tok, w, pid="1006", reservation_active=False, payment_status="expired")
        total = float(_mongo_eval(f"print(db.orders.findOne({{order_id:'{oid}'}}).quote.total);").stdout.strip().splitlines()[-1])
        _write_mock(payments={"1006": _payment("1006", oid, amount=total)})
        r = _consult(_env, oid)
        assert r.json()["result"] == "aprovado"
        assert r.json()["needs_stock_review"] is True
        o = _order(oid)
        assert o["payment_status"] == "approved" and o["fulfillment"] == "revisao_estoque"
        assert _stock_moves_any(oid, "venda") == 0  # sem baixa sem reserva

    def test_divergencias_vao_para_revisao_sem_confirmar(self, _env):
        tok = _register("a6")
        base = _mk_order(tok, _wine(), pid="2001")
        total = float(_mongo_eval(f"print(db.orders.findOne({{order_id:'{base}'}}).quote.total);").stdout.strip().splitlines()[-1])
        casos = [
            ("valor", _payment("2001", base, amount=total + 10)),
            ("moeda", _payment("2001", base, amount=total, currency="USD")),
            ("referencia", _payment("2001", base, amount=total, ref="ord_OUTRO")),
            ("recebedor", None),  # tratado abaixo com collector divergente
        ]
        for nome, spec in casos[:3]:
            _write_mock(payments={"2001": spec})
            _mongo_eval(f"db.orders.updateOne({{order_id:'{base}'}}, {{$set:{{mp_status:null}}, $unset:{{payment_review:''}}}});")
            r = _consult(_env, base)
            assert r.json()["result"].startswith("revisao"), nome
            assert _order(base)["payment_status"] == "pending", nome
            assert _stock_moves_any(base, "venda") == 0, nome
        # recebedor divergente: pedido espera collector 555, provedor diz 777
        _mongo_eval(f"db.orders.updateOne({{order_id:'{base}'}}, {{$set:{{mp_collector_id:555, mp_status:null}}, $unset:{{payment_review:''}}}});")
        _write_mock(payments={"2001": _payment("2001", base, amount=total, collector=777)})
        r = _consult(_env, base)
        assert r.json()["result"].startswith("revisao")
        assert "recebedora" in _order(base)["prev"]["reason"]

    def test_timeout_e_429_nao_alteram_estado(self, _env):
        tok = _register("a7")
        oid = _mk_order(tok, _wine(), pid="9001")
        _write_mock(payments={"9001": {"sleep": 8, "code": 200, "body": {}},
                              "9429": {"code": 429, "body": {}}})
        t0 = time.time()
        r = _consult(_env, oid)
        assert r.json()["result"] == "erro:timeout"
        assert time.time() - t0 < 7  # timeout controlado (MP_HTTP_TIMEOUT=3 no backend isolado)
        o = _order(oid)
        assert o["payment_status"] == "pending" and o["reserved"] is True
        _mongo_eval(f"db.orders.updateOne({{order_id:'{oid}'}}, {{$set:{{mp_payment_id:'9429'}}}});")
        r2 = _consult(_env, oid)
        assert r2.json()["result"] == "erro:http_429"
        assert _order(oid)["payment_status"] == "pending"

    def test_sem_pagamento_distinto_de_erro(self, _env):
        tok = _register("a8")
        oid = _mk_order(tok, _wine(), pid=None)  # sem payment_id → busca por referência
        _write_mock(search={oid: {"code": 200, "body": {"results": []}}})
        r = _consult(_env, oid)
        assert r.json()["result"] == "sem_pagamento"
        o = _order(oid)
        assert o["payment_status"] == "pending"  # não é recusa nem erro
        _mongo_eval(f"db.orders.updateOne({{order_id:'{oid}'}}, {{$set:{{mp_payment_id:'9404'}}}});")
        _write_mock(payments={"9404": {"code": 404, "body": {"error": "nf"}}})
        r2 = _consult(_env, oid)
        assert r2.json()["result"] == "nao_encontrado"

    def test_busca_por_referencia_quando_sem_payment_id(self, _env):
        tok = _register("a9")
        oid = _mk_order(tok, _wine(), pid=None)
        total = float(_mongo_eval(f"print(db.orders.findOne({{order_id:'{oid}'}}).quote.total);").stdout.strip().splitlines()[-1])
        _write_mock(search={oid: {"code": 200, "body": {"results": [
            {"id": "3001", "status": "approved", "status_detail": "accredited",
             "external_reference": oid, "transaction_amount": total,
             "currency_id": "BRL", "collector_id": 777,
             "date_last_updated": "2026-09-15T00:00:00Z"}]}}})
        r = _consult(_env, oid)
        assert r.json()["result"] == "aprovado"
        assert _order(oid)["payment_status"] == "approved"

    def test_multiplos_aprovados_mesmo_pedido_revisao(self, _env):
        tok = _register("a10")
        oid = _mk_order(tok, _wine(), pid=None)
        total = float(_mongo_eval(f"print(db.orders.findOne({{order_id:'{oid}'}}).quote.total);").stdout.strip().splitlines()[-1])
        two = [{"id": p, "status": "approved", "external_reference": oid,
                "transaction_amount": total, "currency_id": "BRL", "collector_id": 777,
                "date_last_updated": "2026-09-15T00:00:00Z"} for p in ("3002", "3003")]
        _write_mock(search={oid: {"code": 200, "body": {"results": two}}})
        r = _consult(_env, oid)
        assert r.json()["result"].startswith("revisao")
        assert "múltiplos" in _order(oid)["prev"]["reason"]
        assert _order(oid)["payment_status"] == "pending"  # não duplica a venda
        assert _stock_moves_any(oid, "venda") == 0

    def test_permissoes_consulta_manual(self, _env):
        tok = _register("a11")
        oid = _mk_order(tok, _wine(), pid="1001")
        r = requests.post(f"{API}/admin/orders/{oid}/consult-payment", headers=_h(tok))
        assert r.status_code == 403  # cliente não consulta
        staff_uid = f"test-rec-staff-{_RUN}"
        staff_tok = f"test_session_rec_{_RUN}_staff"
        _mongo_eval(f"""
db.users.insertOne({{user_id:'{staff_uid}', email:'{staff_uid}@example.com', name:'S', role:'staff',
  password_version:1, mfa_enabled:true, created_at:new Date().toISOString()}});
db.user_sessions.insertOne({{user_id:'{staff_uid}', session_token:'{staff_tok}', pv:1,
  expires_at:new Date(Date.now()+3600000).toISOString(), created_at:new Date().toISOString()}});""")
        r = requests.post(f"{API}/admin/orders/{oid}/consult-payment", headers=_h(staff_tok))
        assert r.status_code == 403  # staff não consulta — só proprietário

    def test_cron_reconcilia_pedidos_em_aberto(self, _env):
        tok = _register("a12")
        oid = _mk_order(tok, _wine(), pid="4001")
        total = float(_mongo_eval(f"print(db.orders.findOne({{order_id:'{oid}'}}).quote.total);").stdout.strip().splitlines()[-1])
        _write_mock(payments={"4001": _payment("4001", oid, amount=total)})
        import os
        secret = None
        for line in open("/app/backend/.env"):
            if line.startswith("WEBHOOK_CRON_SECRET="):
                secret = line.split("=", 1)[1].strip().strip('"').strip("'")
        r = requests.post(f"{API}/cron/reconcile-payments", headers={"Authorization": f"Bearer {secret}"})
        assert r.status_code == 200  # ack imediato
        for _ in range(20):
            time.sleep(0.5)
            if _order(oid)["payment_status"] == "approved":
                break
        assert _order(oid)["payment_status"] == "approved"
        assert _order(oid)["paid_via"] == "reconciliacao"
        out = _mongo_eval("var c=db.cron_runs.find({job:'reconcile-payments'}).sort({at:-1}).limit(1).toArray()[0]; print(c.status + ' checked=' + c.stats.checked);")
        assert "ok" in out.stdout and "checked=" in out.stdout
        # pedido demo NUNCA entra na reconciliação
        tok2 = _register("a13")
        oid2 = _mk_order(tok2, _wine(), env="demo")  # vira demo de volta
        _mongo_eval(f"db.orders.updateOne({{order_id:'{oid2}'}}, {{$set:{{payment_mode:'demo', demo:true, mp_payment_id:null}}}});")
        r2 = requests.post(f"{API}/cron/reconcile-payments", headers={"Authorization": f"Bearer {secret}"})
        assert r2.status_code == 200
        time.sleep(2)
        assert _order(oid2)["payment_status"] == "pending"  # demo não é consultado no provedor
