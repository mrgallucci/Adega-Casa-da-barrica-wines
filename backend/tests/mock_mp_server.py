"""Mock do Mercado Pago para testes ISOLADOS — nunca chama a API real.
Lê o estado de /tmp/mock_mp_state.json a cada requisição, permitindo que cada
teste programe respostas (aprovado, pendente, recusado, timeout, 429, 500, 404).

Rotas avulsas: GET /v1/payments/{id} e GET /v1/payments/search?external_reference=...
Rotas de assinatura:
  POST /preapproval                      → cria (state["preapproval_create"])
  GET  /preapproval/{id}                 → state["preapprovals"][id]
  PUT  /preapproval/{id}                 → aplica {"status": ...} no state e devolve
  GET  /authorized_payments/search?preapproval_id=... → state["invoices"][preapproval_id]

Formato do estado:
{
  "payments": {"1001": {"code": 200, "body": {...}}, "9001": {"sleep": 6}},
  "search":   {"ord_x": {"code": 200, "body": {"results": [...]}}},
  "preapproval_create": {"code": 201, "body": {"status": "authorized"}},
  "preapprovals": {"pre_1": {"code": 200, "body": {"id": "pre_1", "status": "authorized"}}},
  "invoices": {"pre_1": {"code": 200, "body": {"results": [...], "paging": {"total": 1}}}}
}
"""
import json
import re
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

STATE = "/tmp/mock_mp_state.json"


def _load():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def _save(state):
    json.dump(state, open(STATE, "w"))


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, body):
        b = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _respond(self, spec):
        if spec.get("sleep"):
            time.sleep(spec["sleep"])
        self._json(spec.get("code", 200), spec.get("body", {}))

    def do_GET(self):
        state = _load()
        u = urlparse(self.path)
        if u.path.startswith("/v1/payments/search"):
            ref = parse_qs(u.query).get("external_reference", [""])[0]
            return self._respond(state.get("search", {}).get(ref, {"code": 200, "body": {"results": []}}))
        if u.path.startswith("/v1/payments/"):
            pid = u.path.rsplit("/", 1)[-1]
            return self._respond(state.get("payments", {}).get(pid, {"code": 404, "body": {"error": "not_found"}}))
        if u.path.startswith("/authorized_payments/search"):
            pid = parse_qs(u.query).get("preapproval_id", [""])[0]
            return self._respond(state.get("invoices", {}).get(pid, {"code": 200, "body": {"results": [], "paging": {"total": 0}}}))
        m = re.match(r"^/preapproval/([^/]+)$", u.path)
        if m:
            pid = m.group(1)
            return self._respond(state.get("preapprovals", {}).get(pid, {"code": 404, "body": {"error": "not_found"}}))
        return self._json(404, {"error": "not_found"})

    def do_POST(self):
        state = _load()
        u = urlparse(self.path)
        if u.path == "/preapproval":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            spec = state.get("preapproval_create", {"code": 201, "body": {}})
            if spec.get("code", 201) >= 400:
                return self._respond(spec)
            pid = f"pre_{uuid.uuid4().hex[:12]}"
            status = (spec.get("body") or {}).get("status") or body.get("status") or "pending"
            pre = {"id": pid, "status": status,
                   "external_reference": body.get("external_reference"),
                   "payer_email": body.get("payer_email"),
                   "init_point": f"https://mock.mp/assinaturas/{pid}",
                   "auto_recurring": body.get("auto_recurring")}
            state.setdefault("preapprovals", {})[pid] = {"code": 200, "body": pre}
            _save(state)
            return self._json(201, pre)
        return self._json(404, {"error": "not_found"})

    def do_PUT(self):
        state = _load()
        u = urlparse(self.path)
        m = re.match(r"^/preapproval/([^/]+)$", u.path)
        if m:
            pid = m.group(1)
            entry = state.get("preapprovals", {}).get(pid)
            if not entry:
                return self._json(404, {"error": "not_found"})
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            entry["body"].update(body)
            state["preapprovals"][pid] = entry
            _save(state)
            return self._json(200, entry["body"])
        return self._json(404, {"error": "not_found"})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8899), H).serve_forever()
