"""Sommelier Virtual (atendimento), WhatsApp/Instagram e migração de textos — ISOLADO (8002/adega_test).

Cobre: IA desativada por padrão com encaminhamento humano; validação de WhatsApp e
Instagram (HTTPS instagram.com; vazio oculta); edição apenas pelo proprietário;
limite de mensagens por conversa com encaminhamento humano; retenção TTL 90 dias;
pedidos somente do cliente autenticado (anon não recebe dados de pedidos);
resposta baseada no catálogo (chamada real ao modelo, asserções tolerantes);
migração de textos "IA" → "Sommelier" preservando personalizações.
NOTA: usa uma única classe para serializar o estado global de config (xdist-safe).
"""
import uuid

import pytest
import requests

from conftest import API, _mongo_eval

_RUN = uuid.uuid4().hex[:6]


def _session(role):
    uid = f"test-chat-{role}-{_RUN}-{uuid.uuid4().hex[:4]}"
    tok = f"test_session_chat_{_RUN}_{uuid.uuid4().hex[:6]}"
    r = _mongo_eval(f"""
db.users.insertOne({{user_id:'{uid}', email:'{uid}@example.com', name:'Tester Chat', role:'{role}',
  password_version:1, mfa_enabled:true, marketing_opt_in:false, created_at:new Date().toISOString()}});
db.user_sessions.insertOne({{user_id:'{uid}', session_token:'{tok}', pv:1,
  expires_at:new Date(Date.now()+3600000).toISOString(), created_at:new Date().toISOString()}});""")
    assert r.returncode == 0, r.stderr
    return tok


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


def _chat(msg, sid, tok=None):
    return requests.post(f"{API}/chat", json={"session_id": sid, "message": msg},
                         headers=_h(tok) if tok else {})


def _put_chat(owner, **kw):
    return requests.put(f"{API}/admin/settings", headers=_h(owner), json={"chat": kw})


@pytest.fixture(scope="module")
def owner():
    return _session("owner")


@pytest.fixture(scope="module", autouse=True)
def _cleanup():
    yield
    _mongo_eval(f"""
db.users.deleteMany({{email:/^test-chat-/}});
db.user_sessions.deleteMany({{session_token:/^test_session_chat_/}});
db.chat_messages.deleteMany({{session_id:/^tchat-{_RUN}/}});
db.store_settings.updateOne({{key:'store'}}, {{$unset:{{chat:''}}}});""")


class TestAtendimento:
    # ---- configuração e validações ----
    def test_chat_desativado_por_padrao_com_encaminhamento(self):
        r = _chat("Olá", f"tchat-{_RUN}-a")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["disabled"] is True
        assert "wa.me/5524981293634" in data["human_link"]
        assert "Ol%C3%A1%21" in data["human_link"] or "Preciso%20de%20ajuda" in data["human_link"]

    def test_whatsapp_invalido_rejeitado(self, owner):
        assert _put_chat(owner, whatsapp_number="abc123").status_code == 400
        assert _put_chat(owner, whatsapp_number="123").status_code == 400
        r = _put_chat(owner, whatsapp_number="(24) 98129-3634")
        assert r.status_code == 200  # normaliza para dígitos
        s = requests.get(f"{API}/settings").json()
        assert s["chat"]["whatsapp_number"] == "24981293634"

    def test_instagram_validacao(self, owner):
        assert _put_chat(owner, instagram_url="http://instagram.com/loja").status_code == 400
        assert _put_chat(owner, instagram_url="https://evil.com/@loja").status_code == 400
        assert _put_chat(owner, instagram_url="instagram.com/loja").status_code == 400
        r = _put_chat(owner, instagram_url="https://instagram.com/casa.da.barrica")
        assert r.status_code == 200
        s = requests.get(f"{API}/settings").json()
        assert s["chat"]["instagram_url"] == "https://instagram.com/casa.da.barrica"
        r = _put_chat(owner, instagram_url="")
        assert r.status_code == 200  # vazio = link oculto
        assert requests.get(f"{API}/settings").json()["chat"]["instagram_url"] == ""

    def test_staff_nao_edita_atendimento(self):
        staff = _session("staff")
        r = requests.put(f"{API}/admin/settings", headers=_h(staff), json={"chat": {"enabled": True}})
        assert r.status_code == 403

    def test_retencao_ttl_90_dias(self):
        r = _mongo_eval("var i=db.chat_messages.getIndexes().find(x=>x.name==='created_dt_1');"
                        "print(i ? i.expireAfterSeconds : 'MISSING');")
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip().splitlines()[-1] == str(90 * 86400)

    def test_migracao_textos_sommelier(self):
        s = requests.get(f"{API}/settings").json()
        home_footer = str(s["home"]) + str(s["footer"])
        for frase_velha in ("Sommelier com IA", "Harmonizar com IA", "harmonização por IA",
                            "harmonização inteligente com IA"):
            assert frase_velha not in home_footer
        assert s["home"]["card_text"] == "Sommelier Virtual"
        assert "Sommelier Virtual" in s["footer"]["about"]

    # ---- comportamento com IA ativada (chamadas reais, tolerantes) ----
    def test_limite_de_mensagens_com_encaminhamento(self, owner):
        r = _put_chat(owner, enabled=True, max_messages_per_session=1)
        assert r.status_code == 200, r.text
        sid = f"tchat-{_RUN}-limit"
        r1 = _chat("Olá, tudo bem?", sid)
        assert r1.status_code == 200 and not r1.json().get("limit_reached")
        r2 = _chat("Segunda mensagem", sid)
        assert r2.json()["limit_reached"] is True
        assert "wa.me/" in r2.json()["human_link"]
        _put_chat(owner, max_messages_per_session=20)

    def test_resposta_baseada_no_catalogo(self, owner):
        sid = f"tchat-{_RUN}-cat"
        wines = requests.get(f"{API}/wines").json()
        names = [w["name"] for w in wines]
        r = _chat("Quais vinhos vocês têm disponíveis? Me cite um com preço.", sid)
        assert r.status_code == 200, r.text
        data = r.json()
        reply = data["reply"]
        assert len(reply) > 20
        assert "wa.me/" in data["human_link"]
        # tolerante: cita um vinho real do catálogo OU encaminha humano (nunca inventa)
        assert any(n.split()[0] in reply for n in names) or "WhatsApp" in reply or "pessoa" in reply.lower()

    def test_pedidos_so_do_cliente_autenticado(self, owner):
        # cliente com pedido demo
        email = f"test-chat-buyer-{_RUN}@example.com"
        reg = requests.post(f"{API}/auth/register", json={
            "email": email, "password": "Teste@12345", "name": "Comprador Chat", "birth_date": "1990-01-01"})
        assert reg.status_code == 200, reg.text
        tok = reg.json()["token"]
        wines = requests.get(f"{API}/wines").json()
        w = next(w for w in wines if w.get("stock", 0) >= 2 and not w.get("archived"))
        ck = requests.post(f"{API}/checkout", headers=_h(tok), json={
            "items": [{"wine_id": w["wine_id"], "variant_id": "default", "qty": 1}],
            "cep": "01310100", "address": "Av. Paulista, 1000", "birth_date": "1990-01-01",
            "payment_method": "pix"})
        assert ck.status_code == 200, ck.text
        oid = ck.json()["order_id"]
        # anônimo NÃO recebe dados de pedidos
        r = _chat("Qual o status do meu pedido?", f"tchat-{_RUN}-anon")
        assert oid not in r.json()["reply"]
        assert "ord_" not in r.json()["reply"]
        # autenticado recebe resposta (contexto inclui SÓ os pedidos dele)
        r2 = _chat("Qual o status do meu último pedido?", f"tchat-{_RUN}-auth", tok=tok)
        assert r2.status_code == 200 and len(r2.json()["reply"]) > 10
        # mensagens gravadas com o user_id correto (auditoria)
        out = _mongo_eval(f"print(db.chat_messages.countDocuments({{session_id:'tchat-{_RUN}-auth', user_id:null}}));")
        assert out.stdout.strip().splitlines()[-1] == "0"
        _mongo_eval(f"db.orders.deleteMany({{user_email:'{email}'}}); db.users.deleteMany({{email:'{email}'}});")

    def test_mensagem_sem_resposta_do_modelo(self, owner):
        # desativar novamente deve continuar oferecendo humano (indisponibilidade)
        _put_chat(owner, enabled=False)
        r = _chat("Teste", f"tchat-{_RUN}-off")
        assert r.json()["disabled"] is True and "wa.me/" in r.json()["human_link"]
