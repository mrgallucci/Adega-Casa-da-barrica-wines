"""Mercado Pago Assinaturas (PreApproval) — cobrança recorrente autorizada.

Checkout Pro/Preferences NÃO oferece recorrência: assinaturas usam a API
/preapproval com tokenização de cartão feita pelo provedor no navegador.
Este módulo nunca recebe nem armazena PAN/CVV — apenas IDs e tokens do MP.

Credenciais e base de API são herdadas de payments_mp (mesma conta MP da loja;
MP_API_BASE_URL continua sendo o override exclusivo dos testes isolados).
"""
import logging
import httpx

from payments_mp import _token, _mp_api, _timeout, PUBLIC_APP_URL

logger = logging.getLogger(__name__)


async def _mp(method: str, path: str, *, json: dict | None = None,
              params: dict | None = None, idem: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}
    if idem:
        headers["X-Idempotency-Key"] = idem
    async with httpx.AsyncClient(timeout=_timeout()) as client:
        r = await client.request(method, f"{_mp_api()}{path}", headers=headers,
                                 json=json, params=params)
    r.raise_for_status()
    return r.json()


async def create_preapproval(*, external_reference: str, payer_email: str, reason: str,
                             amount: float, frequency: int, frequency_type: str,
                             card_token_id: str | None) -> dict:
    """Cria assinatura. Com card_token_id → nasce 'authorized'; sem → 'pending'
    (cliente autoriza no init_point do MP). X-Idempotency-Key = referência única
    da adesão: cliques repetidos não criam assinatura duplicada no provedor."""
    body = {
        "reason": reason,
        "external_reference": external_reference,
        "payer_email": payer_email,
        "back_url": f"{PUBLIC_APP_URL}/conta",
        "notification_url": f"{PUBLIC_APP_URL}/api/webhooks/mercadopago",
        "auto_recurring": {
            "frequency": frequency,
            "frequency_type": frequency_type,  # "months" | "days"
            "transaction_amount": round(amount, 2),
            "currency_id": "BRL",
        },
    }
    if card_token_id:
        body["card_token_id"] = card_token_id
        body["status"] = "authorized"
    else:
        body["status"] = "pending"
    return await _mp("POST", "/preapproval", json=body, idem=external_reference)


async def get_preapproval(preapproval_id: str) -> dict | None:
    async with httpx.AsyncClient(timeout=_timeout()) as client:
        r = await client.get(f"{_mp_api()}/preapproval/{preapproval_id}",
                             headers={"Authorization": f"Bearer {_token()}"})
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


async def cancel_preapproval(preapproval_id: str) -> dict:
    """Cancelamento oficial: PUT /preapproval/{id} {status: canceled}. Irreversível."""
    return await _mp("PUT", f"/preapproval/{preapproval_id}",
                     json={"status": "canceled"}, idem=f"cancel:{preapproval_id}")


async def update_preapproval_amount(preapproval_id: str, amount: float, frequency: int,
                                    frequency_type: str) -> dict:
    """Atualização de valor SOMENTE após aceite registrado do assinante."""
    return await _mp("PUT", f"/preapproval/{preapproval_id}", json={
        "auto_recurring": {"frequency": frequency, "frequency_type": frequency_type,
                           "transaction_amount": round(amount, 2), "currency_id": "BRL"},
    }, idem=f"amount:{preapproval_id}:{round(amount, 2)}")


async def search_authorized_payments(preapproval_id: str, offset: int = 0, limit: int = 50) -> dict:
    """Invoices da assinatura (reconciliação por consulta — mesma filosofia do
    reconcile de pedidos avulsos: o provedor é a fonte autoritativa)."""
    return await _mp("GET", "/authorized_payments/search",
                     params={"preapproval_id": preapproval_id,
                             "offset": offset, "limit": min(limit, 50)})
