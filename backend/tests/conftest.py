"""Ambiente ISOLADO de testes — Minha Adega.

Regras duras:
- Testes SÓ rodam contra um backend isolado (porta 8002) com banco exclusivo `adega_test`.
- TRAVA: se o alvo for o banco da aplicação, a suíte aborta antes de qualquer escrita.
- Usuário Mongo dedicado `adega_tester` com readWrite APENAS em `adega_test`
  (o mongod local não tem auth habilitada; a credencial com escopo passa a valer
  quando a autenticação estiver ativa, ex.: MongoDB Atlas).
- Nenhuma conta, pedido, estoque ou segredo da loja real é tocado.
"""
import os
import subprocess
import sys
import time
import uuid
import requests
import pytest

APP_DB = "test_database"  # banco da aplicação — PROIBIDO para testes
TEST_DB = os.environ.get("ADEGA_TEST_DB", "adega_test")
MONGO_URL = os.environ.get("ADEGA_TEST_MONGO_URL", "mongodb://adega_tester:adega_tester_dev_pwd@localhost:27017")
MONGO_URL_LOCAL = "mongodb://localhost:27017"  # fallback quando auth está desabilitada no mongod local
MONGO_URI = f"{MONGO_URL.rstrip('/')}/{TEST_DB}?authSource=admin"
TEST_PORT = 8002
TEST_BASE_URL = os.environ.get("ADEGA_TEST_BASE_URL", f"http://127.0.0.1:{TEST_PORT}")
API = f"{TEST_BASE_URL}/api"
_PIDFILE = "/tmp/adega_test_backend.pid"


def _mongo_eval(script: str):
    """Helper único para acesso direto ao banco — passa pela TRAVA."""
    assert TEST_DB != APP_DB and TEST_DB.endswith("_test"), "TRAVA: alvo não é banco de testes"
    r = subprocess.run(["mongosh", "--quiet", MONGO_URI, "--eval", script],
                       capture_output=True, text=True)
    if r.returncode != 0:
        # mongod local sem auth: credenciais na URI podem ser rejeitadas em alguns setups
        r = subprocess.run(["mongosh", "--quiet", f"{MONGO_URL_LOCAL}/{TEST_DB}", "--eval", script],
                           capture_output=True, text=True)
    return r


def _healthy() -> bool:
    try:
        return requests.get(f"{API}/", timeout=2).status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="session", autouse=True)
def isolated_backend():
    """Sobe backend isolado (8002 + adega_test) se não estiver no ar; derruba ao final
    apenas se foi este processo que subiu."""
    assert TEST_DB != APP_DB and TEST_DB.endswith("_test"), \
        f"TRAVA DE SEGURANÇA: TEST_DB='{TEST_DB}' não é um banco de testes isolado"
    started = False
    if not _healthy():
        env = {**os.environ, "DB_NAME": TEST_DB}
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(TEST_PORT)],
            cwd="/app/backend", env=env,
            stdout=open("/tmp/adega_test_backend.log", "ab"), stderr=subprocess.STDOUT)
        with open(_PIDFILE, "w") as f:
            f.write(f"{proc.pid}:{uuid.uuid4().hex[:8]}")
        started = True
        for _ in range(60):
            if _healthy():
                break
            time.sleep(1)
        assert _healthy(), "Backend isolado de testes não subiu (ver /tmp/adega_test_backend.log)"
    yield
    if started:
        try:
            pid = int(open(_PIDFILE).read().split(":")[0])
            os.kill(pid, 15)
        except Exception:
            pass
