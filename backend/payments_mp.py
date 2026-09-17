import os
import hmac
import hashlib
import time
import logging
import httpx
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / '.env')
logger = logging.getLogger(__name__)

PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL", "").rstrip("/")
MP_API_DEFAULT = "https://api.mercadopago.com"

def _mp_api() -> str:
    """Base da API MP — override por env APENAS para testes isolados (mock local)."""
    return os.environ.get("MP_API_BASE_URL", MP_API_DEFAULT)

def _timeout() -> float:
    return float(os.environ.get("MP_HTTP_TIMEOUT", "20"))

# Credenciais: variáveis de ambiente têm prioridade; fallback para cofre no banco
# (preenchido via formulário seguro do painel — valores nunca saem do servidor).
_RT_TOKEN = None
_RT_SECRET = None


def set_runtime_credentials(token: str | None, secret: str | None):
    global _RT_TOKEN, _RT_SECRET
    if token is not None:
        _RT_TOKEN = token
    if secret is not None:
        _RT_SECRET = secret


def _token() -> str:
    return os.environ.get("MP_ACCESS_TOKEN", "") or _RT_TOKEN or ""


def _secret() -> str:
    return os.environ.get("MP_WEBHOOK_SECRET", "") or _RT_SECRET or ""


def mp_configured() -> bool:
    return bool(_token())


def _token_for(order: dict | None) -> str:
    """Credencial do ambiente registrado NO PEDIDO (nunca inferida de prefixo/live_mode).
    Mudanças posteriores no modo da loja não afetam pedidos já iniciados."""
    if (order or {}).get("payment_mode") == "mercadopago_live":
        return os.environ.get("MP_ACCESS_TOKEN_LIVE", "") or _token()
    return _token()


async def create_preference(order_id: str, items: list, payer_email: str, total: float) -> dict:
    """Cria preferência Checkout Pro. Dados de cartão nunca passam por este servidor."""
    preference = {
        "items": [
            {"id": it.get("sku") or it["wine_id"], "title": it["name"], "quantity": it["qty"],
             "currency_id": "BRL", "unit_price": it["unit_price"]}
            for it in items
        ] + [{"id": "frete", "title": "Frete climatizado", "quantity": 1, "currency_id": "BRL",
              "unit_price": round(total - sum(i["unit_price"] * i["qty"] for i in items), 2)}] if total else [],
        "payer": {"email": payer_email},
        "external_reference": order_id,
        "notification_url": f"{PUBLIC_APP_URL}/api/webhooks/mercadopago",
        "back_urls": {
            "success": f"{PUBLIC_APP_URL}/conta",
            "pending": f"{PUBLIC_APP_URL}/conta",
            "failure": f"{PUBLIC_APP_URL}/carrinho",
        },
        "auto_return": "approved",
    }
    preference["items"] = [i for i in preference["items"] if i["unit_price"] > 0]
    async with httpx.AsyncClient(timeout=_timeout()) as client:
        r = await client.post(
            f"{_mp_api()}/checkout/preferences",
            headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json",
                     "X-Idempotency-Key": order_id},
            json=preference,
        )
    r.raise_for_status()
    return r.json()


def valid_webhook_signature(headers, query_params) -> bool:
    """Valida x-signature HMAC do Mercado Pago com tolerância de 5 minutos."""
    secret = _secret()
    if not secret:
        logger.error("MP_WEBHOOK_SECRET não configurado — webhook rejeitado")
        return False
    signature = headers.get("x-signature", "")
    request_id = headers.get("x-request-id", "")
    parts = dict(p.strip().split("=", 1) for p in signature.split(",") if "=" in p)
    ts, received = parts.get("ts"), parts.get("v1")
    data_id = query_params.get("data.id")
    if not ts or not received:
        return False
    manifest = ""
    if data_id:
        manifest += f"id:{str(data_id).lower()};"
    if request_id:
        manifest += f"request-id:{request_id};"
    manifest += f"ts:{ts};"
    try:
        if abs(time.time() - int(ts)) > 300:
            return False
    except ValueError:
        return False
    expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


async def get_payment(payment_id: str, token: str | None = None) -> dict | None:
    """Confirma status server-side. Nunca confiar no payload do webhook nem no navegador."""
    async with httpx.AsyncClient(timeout=_timeout()) as client:
        r = await client.get(
            f"{_mp_api()}/v1/payments/{payment_id}",
            headers={"Authorization": f"Bearer {token or _token()}"},
        )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


async def search_payments(external_reference: str, token: str | None = None) -> list:
    """Localiza pagamentos pela referência do pedido (reconciliação quando ainda
    não há payment_id). Lista vazia = nenhum pagamento encontrado (não é erro)."""
    async with httpx.AsyncClient(timeout=_timeout()) as client:
        r = await client.get(
            f"{_mp_api()}/v1/payments/search",
            params={"external_reference": external_reference},
            headers={"Authorization": f"Bearer {token or _token()}"},
        )
    r.raise_for_status()
    return r.json().get("results", [])


async def refund_payment(payment_id: str, amount: float | None = None) -> dict:
    """Estorno via API do Mercado Pago. X-Idempotency-Key estável evita estorno duplicado em retries."""
    body = {} if amount is None else {"amount": amount}
    async with httpx.AsyncClient(timeout=_timeout()) as client:
        r = await client.post(
            f"{_mp_api()}/v1/payments/{payment_id}/refunds",
            headers={"Authorization": f"Bearer {_token()}",
                     "X-Idempotency-Key": f"refund-{payment_id}-{amount or 'full'}"},
            json=body,
        )
    r.raise_for_status()
    return r.json()


async def test_connection() -> tuple[bool, str]:
    """Valida a credencial criando uma preferência MÍNIMA de homologação (R$ 1).
    /users/me não aceita credenciais de teste (403), por isso a verificação usa
    a API de Preferences — que é o fluxo real da loja.
    ATENÇÃO: isto prova apenas 'credencial aceita para criar preferência'.
    NÃO comprova pagamentos homologados (aprovado/recusado/webhook etc.)."""
    import uuid as _uuid
    try:
        pref = await create_preference(
            f"conn-check-{_uuid.uuid4().hex[:8]}",
            [{"wine_id": "check", "sku": "CHECK", "name": "Verificacao de credencial (nao pagar)",
              "qty": 1, "unit_price": 1.0}],
            "teste@example.com", 1.0)
        if pref.get("id") and pref.get("init_point"):
            return True, ("credencial aceita para criar preferências no sandbox. "
                          "Isto NÃO homologa pagamentos — a bateria de testes de pagamento segue pendente.")
        return False, "provedor respondeu sem preference_id/init_point"
    except httpx.HTTPStatusError as e:
        try:
            body = e.response.json()
            msg = str(body.get("message") or body.get("error") or "")
        except Exception:
            msg = ""
        import re
        msg = re.sub(r"[A-Za-z0-9_\-]{20,}", "[omitido]", msg)
        return False, f"provedor respondeu {e.response.status_code} — {msg}"
    except Exception:
        return False, "falha de comunicação com o provedor — tente novamente"
