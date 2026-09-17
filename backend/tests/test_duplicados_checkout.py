"""Prevenção de cadastros duplicados + checkout com conta ativa — ISOLADO (8002/adega_test).

Cobre: CPF único com/sem pontuação; e-mail único com variações de caixa/espaços;
condição de corrida (duas solicitações simultâneas); edição mantendo os próprios
dados; bloqueio de dados de outra conta sem alterar pontos/pedidos; várias contas
sem CPF; cliente existente aderindo ao Clube sem duplicar cadastro; já-no-Clube;
login legítimo preservado; índices únicos (email total, club.cpf parcial);
checkout exigindo sessão válida e conta ativa; carrinho da conta (PUT/GET).
"""
import threading
import uuid

import pytest
import requests

from conftest import API, _mongo_eval

_RUN = uuid.uuid4().hex[:6]
MSG_DUP = "Já existe um cadastro com os dados informados. Entre na sua conta ou utilize 'Esqueci minha senha'."


def _email(tag):
    return f"test-dup-{tag}-{_RUN}-{uuid.uuid4().hex[:4]}@example.com"


def _cpf_valido(seed):
    base = [int(d) for d in f"{seed:09d}"]
    d1 = (sum(b * (10 - i) for i, b in enumerate(base)) * 10 % 11) % 10
    d2 = (sum((base + [d1])[i] * (11 - i) for i in range(10)) * 10 % 11) % 10
    return "".join(map(str, base + [d1, d2]))


def _cpf_fmt(d):
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"


def _register(tag, club=None, email=None):
    r = requests.post(f"{API}/auth/register", json={
        "email": email or _email(tag), "password": "Teste@12345", "name": "Tester Dup",
        "birth_date": "1990-01-01", **({"club": club} if club else {})})
    return r


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


def _wine():
    wines = requests.get(f"{API}/wines").json()
    return next(w for w in wines if w.get("stock", 0) >= 5 and not w.get("archived"))


@pytest.fixture(scope="module", autouse=True)
def _zone():
    uid = f"test-dup-owner-{_RUN}"
    tok = f"test_session_dup_{_RUN}"
    _mongo_eval(f"""
db.users.insertOne({{user_id:'{uid}', email:'{uid}@example.com', name:'Owner Dup', role:'owner',
  password_version:1, mfa_enabled:true, created_at:new Date().toISOString()}});
db.user_sessions.insertOne({{user_id:'{uid}', session_token:'{tok}', pv:1,
  expires_at:new Date(Date.now()+3600000).toISOString(), created_at:new Date().toISOString()}});""")
    zones = requests.get(f"{API}/admin/shipping", headers=_h(tok)).json()
    for z in zones:
        if not z.get("confirmed"):
            requests.post(f"{API}/admin/shipping", headers=_h(tok), json={**z, "confirmed": True})
    yield
    _mongo_eval(f"""
var re = new RegExp('^test-dup-.*{_RUN}');
db.users.deleteMany({{email: re}});
db.user_sessions.deleteMany({{session_token: new RegExp('^test_session_dup_{_RUN}')}});
db.orders.deleteMany({{user_email: re}});""")


class TestDuplicados:
    def test_cpf_duplicado_com_e_sem_pontuacao(self):
        cpf = _cpf_valido(111222333)
        club = {"cpf": _cpf_fmt(cpf), "accepted_terms": True, "accepted_privacy": True}
        r1 = _register("cpf1", club)
        assert r1.status_code == 200, r1.text
        r2 = _register("cpf2", {"cpf": cpf, "accepted_terms": True, "accepted_privacy": True})
        assert r2.status_code == 400
        assert r2.json()["detail"] == MSG_DUP  # sem pontuação = mesmo CPF
        # a mensagem não revela qual dado conflita nem dados da conta existente
        assert "cpf" not in r2.json()["detail"].lower() and "@" not in r2.json()["detail"]

    def test_email_duplicado_variacoes(self):
        email = _email("case")
        r1 = _register("e1", email=email)
        assert r1.status_code == 200, r1.text
        r2 = _register("e2", email="  " + email.upper() + " ")
        assert r2.status_code == 400 and r2.json()["detail"] == MSG_DUP

    def test_registros_simultaneos_mesmos_dados(self):
        email = _email("race")
        results = []

        def go():
            results.append(_register("race", email=email).status_code)

        threads = [threading.Thread(target=go) for _ in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert sorted(results) == [200, 400]

    def test_edicao_propria_sem_falsa_duplicidade(self):
        cpf = _cpf_valido(555666777)
        r = _register("own", {"cpf": cpf, "accepted_terms": True, "accepted_privacy": True})
        tok = r.json()["token"]
        # salvar o PRÓPRIO CPF novamente não pode acusar duplicidade
        r2 = requests.patch(f"{API}/account/club", headers=_h(tok), json={"cpf": _cpf_fmt(cpf)})
        assert r2.status_code == 200, r2.text

    def test_editar_para_cpf_de_outra_conta_bloqueado(self):
        cpf_a = _cpf_valido(999888777)
        ra = _register("ownera", {"cpf": cpf_a, "accepted_terms": True, "accepted_privacy": True})
        rb = _register("ownerb", {"accepted_terms": True, "accepted_privacy": True})
        tok_b = rb.json()["token"]
        _mongo_eval(f"db.users.updateOne({{email:'{rb.json()['user']['email']}'}}, {{$set:{{points_balance: 77}}}});")
        r = requests.patch(f"{API}/account/club", headers=_h(tok_b), json={"cpf": cpf_a})
        assert r.status_code == 400 and r.json()["detail"] == MSG_DUP
        # pontos e dados da conta B intactos
        pts = requests.get(f"{API}/account/points", headers=_h(tok_b)).json()
        assert pts["balance"] == 77

    def test_varios_clientes_sem_cpf(self):
        for i in range(3):
            r = _register(f"nocpf{i}", {"accepted_terms": True, "accepted_privacy": True})
            assert r.status_code == 200, r.text
            assert r.json()["user"]["email"]

    def test_cliente_existente_adere_sem_duplicar(self):
        email = _email("join")
        r = _register("join", email=email)
        tok = r.json()["token"]
        r2 = requests.patch(f"{API}/account/club", headers=_h(tok),
                            json={"join": True, "accepted_terms": True, "accepted_privacy": True})
        assert r2.status_code == 200 and r2.json()["club_member"] is True
        out = _mongo_eval(f"print(db.users.countDocuments({{email:'{email}'}}));")
        assert out.stdout.strip().splitlines()[-1] == "1"  # nenhuma conta paralela

    def test_ja_no_clube_mensagem(self):
        r = _register("member", {"accepted_terms": True, "accepted_privacy": True})
        tok = r.json()["token"]
        r2 = requests.patch(f"{API}/account/club", headers=_h(tok),
                            json={"join": True, "accepted_terms": True, "accepted_privacy": True})
        assert r2.status_code == 400
        assert r2.json()["detail"] == "Você já faz parte do Clube Casa da Barrica Wines."

    def test_login_legitimo_preservado(self):
        email = _email("login")
        _register("login", email=email)
        r = requests.post(f"{API}/auth/login", json={"email": " " + email.upper(), "password": "Teste@12345"})
        assert r.status_code == 200 and r.json()["token"]

    def test_indices_unicos_no_banco(self):
        out = _mongo_eval("""
var idx = db.users.getIndexes();
var em = idx.find(i => i.unique && i.key && i.key.email === 1);
var cp = idx.find(i => i.unique && i.key && i.key['club.cpf'] === 1);
print('email_unique=' + !!em);
print('cpf_unique=' + !!cp);
print('cpf_partial=' + (cp && cp.partialFilterExpression ? 'yes' : 'no'));""")
        lines = out.stdout.strip().splitlines()
        assert "email_unique=true" in lines
        assert "cpf_unique=true" in lines
        assert "cpf_partial=yes" in lines
        # prova de corrida no nível do banco: insert direto com CPF duplicado falha
        cpf = _cpf_valido(444555666)
        _mongo_eval(f"""db.users.insertOne({{user_id:'dup-db-{_RUN}', email:'{_email("db1")}',
          club:{{cpf:'{cpf}'}}}});""")
        out2 = _mongo_eval(f"""try {{ db.users.insertOne({{user_id:'dup-db2-{_RUN}',
          email:'{_email("db2")}', club:{{cpf:'{cpf}'}}}}); print('NAO_FALHOU'); }}
          catch(e) {{ print('DUPLICATE_KEY'); }}""")
        assert "DUPLICATE_KEY" in out2.stdout
        _mongo_eval(f"db.users.deleteMany({{user_id:/^dup-db/}});")


class TestCheckoutComConta:
    def _payload(self, w):
        return {"items": [{"wine_id": w["wine_id"], "variant_id": "default", "qty": 1}],
                "cep": "01310100", "address": "Av. Paulista, 1000", "birth_date": "1990-01-01",
                "payment_method": "pix"}

    def test_checkout_sem_autenticacao_rejeitado(self):
        r = requests.post(f"{API}/checkout", json=self._payload(_wine()))
        assert r.status_code == 401

    def test_checkout_token_invalido_rejeitado(self):
        r = requests.post(f"{API}/checkout", headers={"Authorization": "Bearer token-falso"},
                          json=self._payload(_wine()))
        assert r.status_code == 401

    def test_conta_bloqueada_nao_finaliza(self):
        r = _register("blocked")
        tok = r.json()["token"]
        email = r.json()["user"]["email"]
        _mongo_eval(f"db.users.updateOne({{email:'{email}'}}, {{$set:{{blocked:true}}}});")
        r2 = requests.post(f"{API}/checkout", headers=_h(tok), json=self._payload(_wine()))
        assert r2.status_code == 403
        assert "desativada" in r2.json()["detail"] or "bloqueada" in r2.json()["detail"]

    def test_conta_ativa_consegue_cotacao_e_pedido_demo(self):
        r = _register("active")
        tok = r.json()["token"]
        w = _wine()
        rq = requests.post(f"{API}/checkout/quote", headers=_h(tok), json=self._payload(w))
        assert rq.status_code == 200 and rq.json()["total"] > 0  # recálculo server-side
        r2 = requests.post(f"{API}/checkout", headers=_h(tok), json=self._payload(w))
        assert r2.status_code == 200, r2.text
        oid = r2.json()["order_id"]
        requests.post(f"{API}/orders/{oid}/confirm-demo", headers=_h(tok))
        _mongo_eval(f"db.orders.deleteMany({{order_id:'{oid}'}});")

    def test_carrinho_da_conta(self):
        r = _register("cart")
        tok = r.json()["token"]
        assert requests.get(f"{API}/account/cart").status_code == 401
        w = _wine()
        items = [{"wine_id": w["wine_id"], "variant_id": "default", "qty": 2, "name": w["name"]}]
        r2 = requests.put(f"{API}/account/cart", headers=_h(tok), json={"items": items})
        assert r2.status_code == 200, r2.text
        r3 = requests.get(f"{API}/account/cart", headers=_h(tok))
        assert r3.json()["items"][0]["qty"] == 2
        # payload inválido rejeitado
        r4 = requests.put(f"{API}/account/cart", headers=_h(tok), json={"items": [{"wine_id": "x"}]})
        assert r4.status_code == 400
