"""Conteúdo editável via painel — roda APENAS no backend isolado (porta 8002, banco adega_test).

Cobre: defaults públicos, salvar+recarregar como owner, bloqueio para staff,
validação de destinos de botões, campos desconhecidos e e-mail de contato.
"""
import uuid

import pytest
import requests

from conftest import API, _mongo_eval

_CREATED = []


def _session(role: str) -> str:
    uid = f"test-content-{uuid.uuid4().hex[:8]}"
    tok = f"test_session_{uuid.uuid4().hex[:12]}"
    _CREATED.append(uid)
    r = _mongo_eval(f"""
db.users.insertOne({{user_id:'{uid}', email:'{uid}@example.com', name:'Tester', role:'{role}',
  password_version:1, mfa_enabled:true, marketing_opt_in:false, created_at:new Date().toISOString()}});
db.user_sessions.insertOne({{user_id:'{uid}', session_token:'{tok}', pv:1,
  expires_at:new Date(Date.now()+3600000).toISOString(), created_at:new Date().toISOString()}});
""")
    assert r.returncode == 0, r.stderr
    return tok


@pytest.fixture(scope="module", autouse=True)
def _cleanup():
    yield
    _mongo_eval("""db.users.deleteMany({user_id:/^test-content-/});
db.user_sessions.deleteMany({session_token:/^test_session_/});
db.store_settings.updateOne({key:'store'}, {$unset:{home:'', footer:''}});""")


def test_public_settings_expose_defaults():
    r = requests.get(f"{API}/settings", timeout=5)
    assert r.status_code == 200
    d = r.json()
    assert d["home"]["hero_title_highlight"] == "uma história"
    assert d["home"]["hero_primary_href"] == "/catalogo"
    assert d["footer"]["about"]


def test_owner_saves_and_reloads_content():
    tok = _session("owner")
    h = {"Authorization": f"Bearer {tok}"}
    r = requests.put(f"{API}/admin/settings", headers=h, timeout=5, json={
        "home": {"hero_title_start": "TESTE Garrafa rara,", "cta_button_href": "/conta"},
        "footer": {"email": "adega@example.com"}})
    assert r.status_code == 200, r.text
    # recarrega via API pública — merge preserva campos não enviados
    d = requests.get(f"{API}/settings", timeout=5).json()
    assert d["home"]["hero_title_start"] == "TESTE Garrafa rara,"
    assert d["home"]["cta_button_href"] == "/conta"
    assert d["home"]["hero_title_highlight"] == "uma história"
    assert d["footer"]["email"] == "adega@example.com"


def test_staff_cannot_edit_settings():
    tok = _session("staff")
    r = requests.put(f"{API}/admin/settings", headers={"Authorization": f"Bearer {tok}"},
                     timeout=5, json={"store_name": "Invadido"})
    assert r.status_code == 403


def test_invalid_link_rejected():
    tok = _session("owner")
    h = {"Authorization": f"Bearer {tok}"}
    for bad in ("https://evil.com", "javascript:alert(1)", "//evil.com"):
        r = requests.put(f"{API}/admin/settings", headers=h, timeout=5,
                         json={"home": {"hero_primary_href": bad}})
        assert r.status_code == 400, bad


def test_unknown_content_field_rejected():
    tok = _session("owner")
    r = requests.put(f"{API}/admin/settings", headers={"Authorization": f"Bearer {tok}"},
                     timeout=5, json={"home": {"inject": "x"}})
    assert r.status_code == 400


def test_invalid_footer_email_rejected():
    tok = _session("owner")
    r = requests.put(f"{API}/admin/settings", headers={"Authorization": f"Bearer {tok}"},
                     timeout=5, json={"footer": {"email": "nao-e-email"}})
    assert r.status_code == 400
