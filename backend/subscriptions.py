"""Assinaturas de vinhos — Casa da Barrica Wines.

Cobrança recorrente real via Mercado Pago PreApproval (subscriptions_mp.py).
Princípios herdados da reconciliação de pedidos: o provedor é autoritativo,
todas as transições usam guards atômicos e as rotinas são idempotentes.

Estados da assinatura (separados de cobrança e kit):
- awaiting_authorization: criada, aguardando autorização/1ª cobrança no provedor
- active: autorizada e em dia
- payment_pending: cobrança agendada/em processamento
- delinquent: cobrança recusada / retentativas do provedor em curso
- cancel_pending: cliente cancelou; provedor ainda não confirmou — novas
  cobranças iniciadas pela loja já estão bloqueadas
- canceled: cancelamento confirmado

Este módulo é importado por server.py AO FINAL (rotas registradas no api_router).
"""
import asyncio
import logging
import random
import uuid
from datetime import datetime, timezone
from typing import Optional, List

import httpx
from fastapi import HTTPException, Depends, Request
from pydantic import BaseModel
from pymongo.errors import DuplicateKeyError

from server import (
    db, api_router, now_utc, audit, require_user, require_admin, get_settings,
    validate_age, reserve_stock, release_stock, log_movement, sync_display_fields,
    find_variant, shipping_quote, rate_limit, _points_cfg, _track_server,
    TERMS_VERSION, WEBHOOK_CRON_SECRET,
)
from email_service import send_email
from subscriptions_mp import (
    create_preapproval, get_preapproval, cancel_preapproval,
    update_preapproval_amount, search_authorized_payments,
)
from payments_mp import mp_configured

logger = logging.getLogger(__name__)

SUB_TIERS = ["entrada", "reserva", "gran_reserva", "gran_cru", "premium"]
TIER_LABELS = {"entrada": "Entrada", "reserva": "Reserva", "gran_reserva": "Gran Reserva",
               "gran_cru": "Gran Cru", "premium": "Premium"}
# Níveis que compõem o Premium (um vinho de cada nível anterior, configurável)
PREMIUM_LEVELS = ["entrada", "reserva", "gran_reserva", "gran_cru"]
# Níveis com brinde obrigatório no kit
GIFT_REQUIRED_TIERS = {"gran_cru", "premium"}
ACTIVE_SUB_STATUSES = ("awaiting_authorization", "active", "payment_pending", "delinquent", "cancel_pending")

SUB_STATUS_LABELS = {
    "awaiting_authorization": "Aguardando autorização",
    "active": "Ativa",
    "payment_pending": "Pagamento pendente",
    "delinquent": "Inadimplente",
    "cancel_pending": "Cancelamento em processamento",
    "canceled": "Cancelada",
}

# Campos comerciais: alteração restrita ao proprietário
OWNER_ONLY_FIELDS = {"price", "billing", "kit", "regions", "shipping", "points_eligible",
                     "substitution", "no_repeat_window", "internal", "limits"}
# Campos nunca expostos em respostas públicas (faixa interna de Reserva inclusa)
PRIVATE_FIELDS = {"internal", "limits", "created_by"}


# ------------------- MODELOS -------------------
class PlanIn(BaseModel):
    name: str
    tier: str
    tagline: str = ""
    description: str = ""
    image: str = ""
    price: Optional[float] = None
    billing: dict = {"frequency": 1, "frequency_type": "months"}
    kit: dict = {"bottles": None, "volume_ml": 750, "levels": {}, "gifts": []}
    eligibility: dict = {"wine_ids": []}
    internal: dict = {}
    limits: dict = {"max_subscribers": None}
    regions: dict = {"countries": ["BR"], "states": [], "international_enabled": False, "taxes_note": ""}
    shipping: dict = {"mode": "table", "custom_price": None, "promo_note": ""}
    points_eligible: bool = False
    substitution: dict = {"allow": False, "note": ""}
    no_repeat_window: int = 0


class CycleIn(BaseModel):
    key: str                      # ex.: "2026-10" — competência do kit
    cutoff_at: str                # data limite de adesão/pagamento (ISO)
    prep_at: Optional[str] = None
    ship_at: Optional[str] = None
    delivery_estimate: str = ""


class GiftIn(BaseModel):
    gift_id: Optional[str] = None
    name: str
    description: str = ""
    image: str = ""
    stock: int = 0
    active: bool = True


class SubscribeIn(BaseModel):
    plan_id: str
    client_request_id: str
    birth_date: str
    address: dict
    accepted_terms: bool
    card_token_id: Optional[str] = None


class AddressIn(BaseModel):
    address: dict


class KitStatusIn(BaseModel):
    status: str
    tracking_code: Optional[str] = None


# ------------------- HELPERS -------------------
def _require_owner(admin: dict):
    if admin.get("role") != "owner":
        raise HTTPException(403, "Somente o proprietário pode alterar regras comerciais de assinaturas")


def _plan_public(p: dict) -> dict:
    """Somente campos públicos. A faixa interna de preço de Reserva, custos,
    margens e observações administrativas NUNCA saem desta função."""
    out = {k: v for k, v in p.items() if k not in PRIVATE_FIELDS and not k.startswith("_")}
    return out


def _subs_cfg(s: dict) -> dict:
    cfg = s.get("subscriptions") or {}
    return {"enabled": bool(cfg.get("enabled", False)),
            "info": cfg.get("info", "")}


def _parse_dt(v) -> Optional[datetime]:
    if not v:
        return None
    d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


async def _eligible_cycle(now: Optional[datetime] = None) -> Optional[dict]:
    """Primeiro ciclo ABERTO cuja data de corte ainda não passou.
    Quem adere após o corte entra no próximo ciclo elegível."""
    now = now or now_utc()
    cycles = await db.sub_cycles.find({"status": "open"}, {"_id": 0}).sort("cutoff_at", 1).to_list(50)
    for c in cycles:
        cut = _parse_dt(c.get("cutoff_at"))
        if cut and cut >= now:
            return c
    return None


async def _tier_plans() -> dict:
    plans = await db.sub_plans.find({"tier": {"$in": PREMIUM_LEVELS}}, {"_id": 0}).to_list(20)
    return {p["tier"]: p for p in plans}


def _ref_price(wine: dict, price_ref: str) -> float:
    v = (wine.get("variants") or [{}])[0]
    if price_ref == "discount_price":
        return float(v.get("discount_price") or v.get("price") or 0)
    return float(v.get("price") or 0)


async def _eligible_pool(plan: dict, level: Optional[str] = None) -> List[dict]:
    """Rótulos elegíveis: aprovados pelo admin na lista do plano, não arquivados
    e com disponibilidade. Para o nível Reserva, aplica também a faixa interna
    de preço de referência (exclusivamente administrativa)."""
    tier = level or plan.get("tier")
    if tier == "premium":
        return []
    if level:
        # pool de um nível específico (uso do Premium): lista do plano daquele nível
        tiers = await _tier_plans()
        src = tiers.get(level)
        wine_ids = (src or {}).get("eligibility", {}).get("wine_ids", [])
        internal = (src or {}).get("internal", {})
    else:
        wine_ids = plan.get("eligibility", {}).get("wine_ids", [])
        internal = plan.get("internal", {})
    if not wine_ids:
        return []
    wines = await db.wines.find({"wine_id": {"$in": wine_ids}, "archived": {"$ne": True}},
                                {"_id": 0}).to_list(500)
    out = []
    for w in wines:
        v = (w.get("variants") or [{}])[0]
        if int(v.get("stock", 0)) - int(v.get("reserved", 0)) <= 0:
            continue
        if tier == "reserva" and internal.get("apply_price_filter", True):
            ref = _ref_price(w, internal.get("price_ref", "price"))
            lo = internal.get("reserva_min")
            hi = internal.get("reserva_max")
            if lo is not None and ref < float(lo):
                continue
            if hi is not None and ref > float(hi):
                continue
        out.append(w)
    return out


async def _recent_kit_wines(plan_id: str, window: int) -> set:
    """Rótulos usados nos últimos N ciclos (controle de repetição)."""
    if not window:
        return set()
    cycles = await db.sub_cycles.find({}, {"_id": 0, "cycle_id": 1}).sort("key", -1).limit(window).to_list(window)
    ids = [c["cycle_id"] for c in cycles]
    if not ids:
        return set()
    used = set()
    async for k in db.sub_kits.find({"plan_id": plan_id, "cycle_id": {"$in": ids}},
                                    {"_id": 0, "composition": 1}):
        for it in k.get("composition", []):
            used.add(it.get("wine_id"))
    return used


def _pick(pool: List[dict], n: int, exclude: set, rng: random.Random) -> List[dict]:
    candidates = [w for w in pool if w["wine_id"] not in exclude]
    if len(candidates) < n:
        candidates = list(pool)  # sem alternativa: relaxa o anti-repetição (registrado em issues)
    rng.shuffle(candidates)
    return candidates[:n]


async def _build_composition(plan: dict, n_subs: int, rng: random.Random) -> tuple[list, list, list]:
    """Monta a composição do kit do plano para o ciclo. Retorna
    (composition, gifts, issues) — issues bloqueiam a confirmação."""
    issues, composition = [], []
    tier = plan.get("tier")
    kit = plan.get("kit") or {}
    exclude = await _recent_kit_wines(plan["plan_id"], int(plan.get("no_repeat_window") or 0))

    if tier == "premium":
        levels = kit.get("levels") or {}
        for level in PREMIUM_LEVELS:
            want = int(levels.get(level) or 1)
            if want < 1:
                issues.append(f"Premium: defina a quantidade do nível {TIER_LABELS[level]} (mínimo 1)")
                continue
            pool = await _eligible_pool(plan, level=level)
            if len(pool) < want:
                issues.append(f"Premium: nível {TIER_LABELS[level]} tem {len(pool)} rótulo(s) elegível(is), "
                              f"mas o kit exige {want}")
                continue
            for w in _pick(pool, want, exclude, rng):
                composition.append({"wine_id": w["wine_id"], "variant_id": "default",
                                    "name": w["name"], "qty": 1, "level": level})
    else:
        bottles = kit.get("bottles")
        if not bottles:
            issues.append("Defina a quantidade de garrafas do kit antes de gerar a composição")
        else:
            pool = await _eligible_pool(plan)
            if len(pool) < int(bottles):
                issues.append(f"Apenas {len(pool)} rótulo(s) elegível(is) com estoque para {bottles} garrafa(s)")
            else:
                for w in _pick(pool, int(bottles), exclude, rng):
                    composition.append({"wine_id": w["wine_id"], "variant_id": "default",
                                        "name": w["name"], "qty": 1, "level": tier})

    gifts = []
    gift_specs = kit.get("gifts") or []
    if tier in GIFT_REQUIRED_TIERS and not gift_specs:
        issues.append(f"O plano {TIER_LABELS.get(tier, tier)} promete brinde exclusivo: cadastre o brinde do kit")
    for g in gift_specs:
        gift = await db.sub_gifts.find_one({"gift_id": g.get("gift_id"), "active": True}, {"_id": 0})
        if not gift:
            issues.append(f"Brinde {g.get('gift_id')} não encontrado ou inativo")
            continue
        need = int(g.get("qty", 1)) * n_subs
        avail = int(gift.get("stock", 0)) - int(gift.get("reserved", 0))
        if avail < need:
            issues.append(f"Brinde '{gift['name']}' insuficiente: disponível {avail}, necessário {need}")
        gifts.append({"gift_id": gift["gift_id"], "name": gift["name"], "qty": int(g.get("qty", 1))})

    # estoque de vinho compartilhado com a loja avulsa
    needed = {}
    for it in composition:
        needed[it["wine_id"]] = needed.get(it["wine_id"], 0) + it["qty"] * n_subs
    for wid, qty in needed.items():
        w = await db.wines.find_one({"wine_id": wid}, {"_id": 0})
        v = (w.get("variants") or [{}])[0]
        avail = int(v.get("stock", 0)) - int(v.get("reserved", 0))
        if avail < qty:
            issues.append(f"'{w['name']}': disponível {avail}, necessário {qty} para o ciclo")
    return composition, gifts, issues


async def _apply_club_adhesion(sub: dict, user: dict):
    """1ª cobrança confirmada → membro do Clube automaticamente, sem duplicar
    cadastro/CPF e SEM alterar consentimentos de marketing existentes."""
    if sub.get("club_adhesion", {}).get("applied"):
        return
    if not user.get("club_member"):
        await db.users.update_one({"user_id": user["user_id"]},
                                  {"$set": {"club_member": True,
                                            "club_terms_version": TERMS_VERSION,
                                            "club_accepted_at": now_utc().isoformat(),
                                            "club_origin": "assinatura"}})
    await db.subscriptions.update_one({"sub_id": sub["sub_id"]},
                                      {"$set": {"club_adhesion.applied": True}})


async def _award_charge_points(sub: dict, charge: dict, plan: dict, cfg: dict):
    """Pontos por cobrança REAL de assinatura (idempotente por cobrança).
    Demo e homologação nunca pontuam; elegibilidade vem do plano."""
    if sub.get("demo") or sub.get("payment_mode") != "mercadopago_live":
        return
    if not plan.get("points_eligible"):
        return
    if not cfg.get("earn_enabled") or cfg.get("reais_per_point", 0) <= 0:
        return
    pts = int(charge["amount"] // cfg["reais_per_point"])
    if pts <= 0:
        return
    if await db.points_ledger.find_one({"charge_id": charge["charge_id"], "type": "earn"}):
        return
    await db.points_ledger.insert_one({
        "entry_id": f"pts_{uuid.uuid4().hex[:12]}", "user_id": sub["user_id"],
        "charge_id": charge["charge_id"], "type": "earn", "points": pts,
        "amount_brl": round(charge["amount"], 2),
        "note": f"Assinatura {plan.get('name')} — cobrança do ciclo {charge.get('cycle_id')}",
        "actor": "sistema", "created_at": now_utc().isoformat()})
    await db.users.update_one({"user_id": sub["user_id"]}, {"$inc": {"points_balance": pts}})


async def _next_cycle_for_charge(sub: dict) -> Optional[dict]:
    """Ciclo ainda sem cobrança aprovada para esta assinatura (1ª = first_cycle)."""
    if not await db.sub_charges.find_one({"sub_id": sub["sub_id"], "status": "approved"}):
        if sub.get("first_cycle_id"):
            return await db.sub_cycles.find_one({"cycle_id": sub["first_cycle_id"]}, {"_id": 0})
    charged = {c["cycle_id"] async for c in
               db.sub_charges.find({"sub_id": sub["sub_id"], "status": "approved"},
                                   {"_id": 0, "cycle_id": 1})}
    cycle = await db.sub_cycles.find_one(
        {"cycle_id": {"$nin": list(charged)}, "status": {"$in": ["open", "closed"]}},
        {"_id": 0}, sort=[("cutoff_at", 1)])
    return cycle


async def _process_charge_approved(sub: dict, charge: dict, via: str):
    """Cobrança aprovada (guard atômico): ativa assinatura, aplica adesão ao
    Clube, credita pontos se elegível e define o ciclo contemplado."""
    r = await db.sub_charges.update_one(
        {"charge_id": charge["charge_id"], "status": {"$nin": ["approved", "refunded"]}},
        {"$set": {"status": "approved", "paid_at": now_utc().isoformat(), "paid_via": via}})
    if r.modified_count == 0:
        return
    user = await db.users.find_one({"user_id": sub["user_id"]}, {"_id": 0})
    plan = await db.sub_plans.find_one({"plan_id": sub["plan_id"]}, {"_id": 0})
    if sub.get("status") in ("awaiting_authorization", "payment_pending", "delinquent"):
        await db.subscriptions.update_one({"sub_id": sub["sub_id"]},
                                          {"$set": {"status": "active"}})
    if user:
        await _apply_club_adhesion(sub, user)
    if plan:
        await _award_charge_points(sub, charge, plan, _points_cfg(await get_settings()))
    if user:
        asyncio.create_task(send_email(
            to=user["email"],
            subject=f"Cobrança da assinatura aprovada — {plan.get('name') if plan else 'Casa da Barrica'}",
            html=("<p>Sua cobrança de assinatura foi aprovada. Acompanhe o ciclo do seu kit "
                  "em Minha Conta &gt; Minha Assinatura.</p>")))


# ------------------- SEED DOS 5 PLANOS (rascunho) -------------------
SEED_PLANS = [
    {"tier": "entrada", "name": "Entrada", "position": 1,
     "tagline": "Sua porta de entrada para o mundo dos vinhos",
     "description": "Seleção variada de vinhos para descoberta, escolhidos pela nossa curadoria entre rótulos selecionados. Uma forma acolhedora de explorar novos estilos a cada ciclo."},
    {"tier": "reserva", "name": "Reserva", "position": 2,
     "tagline": "Rótulos com seleção ainda mais criteriosa",
     "description": "Seleção de rótulos Reserva escolhidos pela curadoria, com perfis marcantes para quem já conhece e quer se aprofundar."},
    {"tier": "gran_reserva", "name": "Gran Reserva", "position": 3,
     "tagline": "Complexidade e diferentes estilos em cada kit",
     "description": "Seleção variada de rótulos Gran Reserva de diferentes faixas de preço, escolhidos entre os vinhos aprovados pela nossa curadoria."},
    {"tier": "gran_cru", "name": "Gran Cru", "position": 4,
     "tagline": "A assinatura Elite da Casa da Barrica",
     "description": "Vinhos de maior estrutura, rótulos tradicionais e opções de guarda selecionados pelo nosso time. Cada kit inclui ao menos um brinde exclusivo."},
    {"tier": "premium", "name": "Premium", "position": 5,
     "tagline": "A experiência completa, nível por nível",
     "description": "Cada kit contempla ao menos um vinho de cada um dos quatro níveis anteriores, com brindes exclusivos, cupons de desconto e acesso antecipado a informações sobre eventos de vinhos, cursos e masterclasses."},
]


async def seed_subscriptions():
    """Cria os 5 planos como RASCUNHO (sem preço nem quantidades — definidos
    pelo proprietário no painel). Idempotente; nunca sobrescreve edições."""
    for sp in SEED_PLANS:
        if await db.sub_plans.find_one({"tier": sp["tier"]}):
            continue
        doc = {
            "plan_id": f"plan_{uuid.uuid4().hex[:10]}",
            "status": "draft",
            "price": None, "currency": "BRL",
            "image": "",
            "billing": {"frequency": 1, "frequency_type": "months"},
            "kit": {"bottles": None, "volume_ml": 750, "levels": {}, "gifts": []},
            "eligibility": {"wine_ids": []},
            "internal": ({"price_ref": "price", "reserva_min": 80.0, "reserva_max": 200.0,
                          "apply_price_filter": True, "cost": None, "margin_note": "", "admin_notes": ""}
                         if sp["tier"] == "reserva" else
                         {"price_ref": "price", "cost": None, "margin_note": "", "admin_notes": ""}),
            "limits": {"max_subscribers": None},
            "regions": {"countries": ["BR"], "states": [], "international_enabled": False,
                        "taxes_note": ""},
            "shipping": {"mode": "table", "custom_price": None, "promo_note": ""},
            "points_eligible": False,
            "substitution": {"allow": False, "note": ""},
            "no_repeat_window": 0,
            "version": 1,
            "created_at": now_utc().isoformat(), "updated_at": now_utc().isoformat(),
            "created_by": "seed",
            **sp,
        }
        await db.sub_plans.insert_one(doc)
    await db.subscriptions.create_index("external_reference", unique=True)
    await db.subscriptions.create_index("mp_preapproval_id", unique=True, sparse=True)
    await db.sub_charges.create_index([("sub_id", 1), ("cycle_id", 1)], unique=True, sparse=True)
    await db.sub_kits.create_index([("sub_id", 1), ("cycle_id", 1)], unique=True)


# ------------------- ADMIN: PLANOS -------------------
@api_router.get("/admin/subscriptions/plans")
async def admin_list_plans(admin=Depends(require_admin)):
    return await db.sub_plans.find({}, {"_id": 0}).sort("position", 1).to_list(100)


@api_router.post("/admin/subscriptions/plans")
async def admin_create_plan(payload: PlanIn, admin=Depends(require_admin)):
    _require_owner(admin)
    if payload.tier not in SUB_TIERS:
        raise HTTPException(400, "Nível inválido")
    top = await db.sub_plans.find_one({}, {"_id": 0, "position": 1}, sort=[("position", -1)])
    doc = payload.model_dump()
    doc.update({"plan_id": f"plan_{uuid.uuid4().hex[:10]}", "status": "draft",
                "position": (top or {}).get("position", 0) + 1, "version": 1,
                "created_at": now_utc().isoformat(), "updated_at": now_utc().isoformat(),
                "created_by": admin["email"]})
    await db.sub_plans.insert_one(doc)
    await audit(admin, "sub_plan_created", {"plan_id": doc["plan_id"], "name": doc["name"]})
    doc.pop("_id", None)
    return doc


@api_router.put("/admin/subscriptions/plans/{plan_id}")
async def admin_update_plan(plan_id: str, payload: PlanIn, admin=Depends(require_admin)):
    plan = await db.sub_plans.find_one({"plan_id": plan_id}, {"_id": 0})
    if not plan:
        raise HTTPException(404, "Plano não encontrado")
    incoming = payload.model_dump()
    changed_owner_fields = [f for f in OWNER_ONLY_FIELDS if incoming.get(f) != plan.get(f)]
    if changed_owner_fields:
        _require_owner(admin)
    # preço/benefícios nunca alteram silenciosamente contratos vigentes
    price_changed = incoming.get("price") != plan.get("price")
    doc = {**incoming, "updated_at": now_utc().isoformat(), "version": plan.get("version", 1) + 1}
    await db.sub_plans.update_one({"plan_id": plan_id}, {"$set": doc})
    if price_changed and plan.get("status") == "published":
        n = await db.subscriptions.update_many(
            {"plan_id": plan_id, "status": {"$in": ACTIVE_SUB_STATUSES}},
            {"$set": {"pending_change": {"new_version": doc["version"],
                                         "new_price": incoming.get("price"),
                                         "since": now_utc().isoformat(),
                                         "accepted": False}}})
        if n.modified_count:
            await audit(admin, "sub_plan_price_change_flagged",
                        {"plan_id": plan_id, "affected": n.modified_count})
    await audit(admin, "sub_plan_updated", {"plan_id": plan_id, "version": doc["version"]})
    return {"ok": True, "version": doc["version"]}


@api_router.post("/admin/subscriptions/plans/{plan_id}/duplicate")
async def admin_duplicate_plan(plan_id: str, admin=Depends(require_admin)):
    plan = await db.sub_plans.find_one({"plan_id": plan_id}, {"_id": 0})
    if not plan:
        raise HTTPException(404, "Plano não encontrado")
    top = await db.sub_plans.find_one({}, {"_id": 0, "position": 1}, sort=[("position", -1)])
    doc = {**plan, "plan_id": f"plan_{uuid.uuid4().hex[:10]}",
           "name": f"{plan['name']} (cópia)", "status": "draft",
           "position": (top or {}).get("position", 0) + 1, "version": 1,
           "created_at": now_utc().isoformat(), "updated_at": now_utc().isoformat(),
           "created_by": admin["email"]}
    await db.sub_plans.insert_one(doc)
    await audit(admin, "sub_plan_duplicated", {"from": plan_id, "plan_id": doc["plan_id"]})
    doc.pop("_id", None)
    return doc


@api_router.post("/admin/subscriptions/plans/{plan_id}/publish")
async def admin_publish_plan(plan_id: str, admin=Depends(require_admin)):
    _require_owner(admin)
    plan = await db.sub_plans.find_one({"plan_id": plan_id}, {"_id": 0})
    if not plan:
        raise HTTPException(404, "Plano não encontrado")
    if plan.get("status") == "archived":
        raise HTTPException(400, "Desarquive o plano antes de publicar")
    if plan.get("price") is None or float(plan.get("price") or 0) <= 0:
        raise HTTPException(400, "Defina o preço antes de publicar")
    if not (plan.get("kit") or {}).get("bottles") and plan.get("tier") != "premium":
        raise HTTPException(400, "Defina a quantidade de garrafas do kit antes de publicar")
    if plan.get("tier") in GIFT_REQUIRED_TIERS and not (plan.get("kit") or {}).get("gifts"):
        raise HTTPException(400, "Este plano promete brinde exclusivo: cadastre o brinde do kit")
    await db.sub_plans.update_one({"plan_id": plan_id},
                                  {"$set": {"status": "published", "updated_at": now_utc().isoformat()}})
    await audit(admin, "sub_plan_published", {"plan_id": plan_id})
    return {"ok": True}


@api_router.post("/admin/subscriptions/plans/{plan_id}/archive")
async def admin_archive_plan(plan_id: str, body: dict, admin=Depends(require_admin)):
    _require_owner(admin)
    plan = await db.sub_plans.find_one({"plan_id": plan_id}, {"_id": 0})
    if not plan:
        raise HTTPException(404, "Plano não encontrado")
    unarchive = bool(body.get("unarchive"))
    # Arquivar impede novas adesões; assinaturas existentes são preservadas
    await db.sub_plans.update_one({"plan_id": plan_id},
                                  {"$set": {"status": "draft" if unarchive else "archived",
                                            "updated_at": now_utc().isoformat()}})
    await audit(admin, "sub_plan_unarchived" if unarchive else "sub_plan_archived", {"plan_id": plan_id})
    return {"ok": True}


@api_router.put("/admin/subscriptions/plans-order")
async def admin_reorder_plans(body: dict, admin=Depends(require_admin)):
    _require_owner(admin)
    for pos, pid in enumerate(body.get("plan_ids", []), start=1):
        await db.sub_plans.update_one({"plan_id": pid}, {"$set": {"position": pos}})
    await audit(admin, "sub_plans_reordered", {"count": len(body.get("plan_ids", []))})
    return {"ok": True}


# ------------------- ADMIN: CONFIG GERAL DE ASSINATURAS -------------------
@api_router.put("/admin/subscriptions/settings")
async def admin_sub_settings(body: dict, admin=Depends(require_admin)):
    _require_owner(admin)
    update = {"subscriptions": {"enabled": bool(body.get("enabled", False)),
                                "info": (body.get("info") or "")[:500]}}
    await db.store_settings.update_one({"key": "store"}, {"$set": update}, upsert=True)
    await audit(admin, "sub_settings_updated", {"enabled": update["subscriptions"]["enabled"]})
    return {"ok": True}


# ------------------- ADMIN: CICLOS (calendário de kits) -------------------
@api_router.get("/admin/subscriptions/cycles")
async def admin_list_cycles(admin=Depends(require_admin)):
    return await db.sub_cycles.find({}, {"_id": 0}).sort("key", 1).to_list(100)


@api_router.post("/admin/subscriptions/cycles")
async def admin_upsert_cycle(payload: CycleIn, admin=Depends(require_admin)):
    _require_owner(admin)
    if not _parse_dt(payload.cutoff_at):
        raise HTTPException(400, "Data de corte inválida")
    existing = await db.sub_cycles.find_one({"key": payload.key}, {"_id": 0})
    doc = payload.model_dump()
    if existing:
        # alteração de calendário preserva histórico e marca comunicação
        history = existing.get("history", [])
        changes = {k: {"de": existing.get(k), "para": doc.get(k)}
                   for k in ("cutoff_at", "prep_at", "ship_at", "delivery_estimate")
                   if existing.get(k) != doc.get(k)}
        if changes:
            history.append({"at": now_utc().isoformat(), "by": admin["email"], "changes": changes})
            await db.subscriptions.update_many(
                {"current_cycle_id": existing["cycle_id"], "status": {"$in": ACTIVE_SUB_STATUSES}},
                {"$set": {"cycle_notice": {"cycle": payload.key, "at": now_utc().isoformat(),
                                           "message": "O calendário do seu ciclo foi atualizado. Confira as novas datas."}}})
        await db.sub_cycles.update_one({"cycle_id": existing["cycle_id"]},
                                       {"$set": {**doc, "history": history}})
        await audit(admin, "sub_cycle_updated", {"key": payload.key, "changes": list(changes)})
        return {"ok": True, "cycle_id": existing["cycle_id"]}
    doc.update({"cycle_id": f"cyc_{uuid.uuid4().hex[:10]}", "status": "open",
                "history": [], "created_at": now_utc().isoformat()})
    await db.sub_cycles.insert_one(doc)
    await audit(admin, "sub_cycle_created", {"key": payload.key})
    return {"ok": True, "cycle_id": doc["cycle_id"]}


@api_router.post("/admin/subscriptions/cycles/{cycle_id}/status")
async def admin_cycle_status(cycle_id: str, body: dict, admin=Depends(require_admin)):
    _require_owner(admin)
    status = body.get("status")
    if status not in ("open", "closed", "preparing", "shipped", "done"):
        raise HTTPException(400, "Status de ciclo inválido")
    r = await db.sub_cycles.update_one({"cycle_id": cycle_id}, {"$set": {"status": status}})
    if not r.matched_count:
        raise HTTPException(404, "Ciclo não encontrado")
    return {"ok": True}


# ------------------- ADMIN: BRINDES (estoque próprio) -------------------
@api_router.get("/admin/subscriptions/gifts")
async def admin_list_gifts(admin=Depends(require_admin)):
    return await db.sub_gifts.find({}, {"_id": 0}).to_list(200)


@api_router.post("/admin/subscriptions/gifts")
async def admin_upsert_gift(payload: GiftIn, admin=Depends(require_admin)):
    doc = payload.model_dump()
    doc["gift_id"] = doc.get("gift_id") or f"gift_{uuid.uuid4().hex[:8]}"
    doc["reserved"] = (await db.sub_gifts.find_one({"gift_id": doc["gift_id"]}, {"_id": 0}) or {}).get("reserved", 0)
    await db.sub_gifts.update_one({"gift_id": doc["gift_id"]}, {"$set": doc}, upsert=True)
    await audit(admin, "sub_gift_saved", {"gift_id": doc["gift_id"], "name": doc["name"]})
    return {"ok": True, "gift_id": doc["gift_id"]}


@api_router.delete("/admin/subscriptions/gifts/{gift_id}")
async def admin_delete_gift(gift_id: str, admin=Depends(require_admin)):
    await db.sub_gifts.delete_one({"gift_id": gift_id, "reserved": 0})
    return {"ok": True}


# ------------------- ADMIN: CURADORIA / KITS -------------------
@api_router.get("/admin/subscriptions/eligible-wines/{plan_id}")
async def admin_eligible_wines(plan_id: str, admin=Depends(require_admin)):
    """Pool elegível do plano (para o editor). Para Reserva, inclui a faixa
    interna somente nesta resposta administrativa."""
    plan = await db.sub_plans.find_one({"plan_id": plan_id}, {"_id": 0})
    if not plan:
        raise HTTPException(404, "Plano não encontrado")
    pool = await _eligible_pool(plan)
    return {"pool": [{"wine_id": w["wine_id"], "name": w["name"],
                      "price": _ref_price(w, plan.get("internal", {}).get("price_ref", "price")),
                      "available": int((w.get("variants") or [{}])[0].get("stock", 0))
                      - int((w.get("variants") or [{}])[0].get("reserved", 0))} for w in pool],
            "internal": plan.get("internal", {})}


@api_router.post("/admin/subscriptions/plans/{plan_id}/cycles/{cycle_id}/propose")
async def admin_propose_kit(plan_id: str, cycle_id: str, admin=Depends(require_admin)):
    """Gera PROPOSTA de composição do kit para revisão — nada é reservado aqui."""
    plan = await db.sub_plans.find_one({"plan_id": plan_id}, {"_id": 0})
    cycle = await db.sub_cycles.find_one({"cycle_id": cycle_id}, {"_id": 0})
    if not plan or not cycle:
        raise HTTPException(404, "Plano ou ciclo não encontrado")
    subs = await db.subscriptions.find(
        {"plan_id": plan_id, "status": "active", "charges_blocked": {"$ne": True}},
        {"_id": 0}).to_list(1000)
    eligible = []
    for s in subs:
        paid = await db.sub_charges.find_one({"sub_id": s["sub_id"], "cycle_id": cycle_id,
                                              "status": "approved"})
        has_kit = await db.sub_kits.find_one({"sub_id": s["sub_id"], "cycle_id": cycle_id})
        if paid and not has_kit:
            eligible.append(s["sub_id"])
    rng = random.Random(f"{plan_id}:{cycle_id}")  # proposta determinística por ciclo
    composition, gifts, issues = await _build_composition(plan, len(eligible), rng)
    doc = {"plan_id": plan_id, "cycle_id": cycle_id, "composition": composition,
           "gifts": gifts, "issues": issues, "subscribers": eligible,
           "status": "draft", "proposed_at": now_utc().isoformat(),
           "proposed_by": admin["email"]}
    await db.sub_kit_proposals.update_one({"plan_id": plan_id, "cycle_id": cycle_id},
                                          {"$set": doc}, upsert=True)
    await audit(admin, "sub_kit_proposed", {"plan_id": plan_id, "cycle_id": cycle_id,
                                            "subs": len(eligible), "issues": len(issues)})
    return doc


@api_router.get("/admin/subscriptions/proposals/{plan_id}/{cycle_id}")
async def admin_get_proposal(plan_id: str, cycle_id: str, admin=Depends(require_admin)):
    p = await db.sub_kit_proposals.find_one({"plan_id": plan_id, "cycle_id": cycle_id}, {"_id": 0})
    return p or {}


@api_router.post("/admin/subscriptions/proposals/{plan_id}/{cycle_id}/confirm")
async def admin_confirm_proposal(plan_id: str, cycle_id: str, admin=Depends(require_admin)):
    """Confirma a proposta revisada: cria UM kit por assinante (único por ciclo)
    com reserva atômica de vinho (estoque compartilhado com a loja) e brindes.
    Falta de item exige revisão — nada é substituído silenciosamente."""
    prop = await db.sub_kit_proposals.find_one({"plan_id": plan_id, "cycle_id": cycle_id}, {"_id": 0})
    if not prop:
        raise HTTPException(404, "Gere a proposta primeiro")
    if prop.get("status") == "confirmed":
        return {"ok": True, "kits": prop.get("kits_created", 0), "idempotent": True}
    if prop.get("issues"):
        raise HTTPException(400, "A proposta tem pendências de revisão: " + "; ".join(prop["issues"]))
    plan = await db.sub_plans.find_one({"plan_id": plan_id}, {"_id": 0})
    created, reserved_lines = 0, []
    try:
        for it in prop["composition"]:
            ok = await reserve_stock(it["wine_id"], it.get("variant_id") or "default",
                                     it["qty"] * len(prop["subscribers"]))
            if not ok:
                raise HTTPException(409, f"Estoque insuficiente para '{it['name']}' — revise a proposta")
            reserved_lines.append(("wine", it["wine_id"], it.get("variant_id") or "default",
                                   it["qty"] * len(prop["subscribers"])))
        for g in prop["gifts"]:
            r = await db.sub_gifts.update_one(
                {"gift_id": g["gift_id"], "$expr": {"$gte": [{"$subtract": ["$stock", "$reserved"]},
                                                             g["qty"] * len(prop["subscribers"])]}},
                {"$inc": {"reserved": g["qty"] * len(prop["subscribers"])}})
            if r.modified_count == 0:
                raise HTTPException(409, f"Brinde '{g['name']}' insuficiente — revise a proposta")
            reserved_lines.append(("gift", g["gift_id"], None, g["qty"] * len(prop["subscribers"])))
    except HTTPException:
        for kind, rid, vid, q in reserved_lines:
            if kind == "wine":
                await release_stock(rid, vid, q)
            else:
                await db.sub_gifts.update_one({"gift_id": rid}, {"$inc": {"reserved": -q}})
        raise

    for sub_id in prop["subscribers"]:
        sub = await db.subscriptions.find_one({"sub_id": sub_id}, {"_id": 0})
        kit = {"kit_id": f"kit_{uuid.uuid4().hex[:10]}", "sub_id": sub_id, "cycle_id": cycle_id,
               "plan_id": plan_id, "user_id": sub["user_id"],
               "composition": prop["composition"], "gifts": prop["gifts"],
               "status": "reserved", "address": sub.get("pending_address") or sub.get("address"),
               "tracking_code": None, "shipped_at": None, "delivered_at": None,
               "received_at": None, "created_at": now_utc().isoformat()}
        try:
            await db.sub_kits.insert_one(kit)
            created += 1
        except DuplicateKeyError:
            pass  # reexecução nunca duplica kit do ciclo
        if sub.get("pending_address"):
            await db.subscriptions.update_one({"sub_id": sub_id},
                                              {"$set": {"address": sub["pending_address"]},
                                               "$unset": {"pending_address": ""}})
        for it in prop["composition"]:
            await log_movement(it["wine_id"], it.get("variant_id") or "default", "reserva",
                               it["qty"], f"Kit assinatura {cycle_id}", admin)
    for it in prop["composition"]:
        await sync_display_fields(it["wine_id"])
    await db.sub_kit_proposals.update_one({"plan_id": plan_id, "cycle_id": cycle_id},
                                          {"$set": {"status": "confirmed", "kits_created": created,
                                                    "confirmed_at": now_utc().isoformat(),
                                                    "confirmed_by": admin["email"]}})
    await audit(admin, "sub_kit_confirmed", {"plan_id": plan_id, "cycle_id": cycle_id, "kits": created})
    return {"ok": True, "kits": created}


@api_router.get("/admin/subscriptions/kits")
async def admin_list_kits(cycle_id: Optional[str] = None, admin=Depends(require_admin)):
    q = {"cycle_id": cycle_id} if cycle_id else {}
    return await db.sub_kits.find(q, {"_id": 0}).sort("created_at", -1).to_list(500)


@api_router.post("/admin/subscriptions/kits/{kit_id}/status")
async def admin_kit_status(kit_id: str, payload: KitStatusIn, admin=Depends(require_admin)):
    kit = await db.sub_kits.find_one({"kit_id": kit_id}, {"_id": 0})
    if not kit:
        raise HTTPException(404, "Kit não encontrado")
    allowed = {"reserved": ["preparing"], "preparing": ["shipped"], "shipped": ["delivered"]}
    if payload.status not in allowed.get(kit["status"], []):
        raise HTTPException(400, f"Transição inválida: {kit['status']} → {payload.status}")
    upd = {"status": payload.status}
    if payload.status == "shipped":
        upd["shipped_at"] = now_utc().isoformat()
        upd["tracking_code"] = payload.tracking_code
    if payload.status == "delivered":
        upd["delivered_at"] = now_utc().isoformat()
    await db.sub_kits.update_one({"kit_id": kit_id}, {"$set": upd})
    if payload.status == "shipped":
        sub = await db.subscriptions.find_one({"sub_id": kit["sub_id"]}, {"_id": 0})
        if sub:
            asyncio.create_task(send_email(
                to=sub["user_email"], subject="Seu kit de assinatura foi postado — Casa da Barrica",
                html=("<p>Seu kit foi postado. Na entrega, um adulto maior de 18 anos deve "
                      "apresentar documento com foto. Acompanhe em Minha Conta &gt; Minha Assinatura.</p>")))
    return {"ok": True}


# ------------------- ADMIN: ASSINANTES -------------------
@api_router.get("/admin/subscriptions/subscribers")
async def admin_subscribers(status: Optional[str] = None, admin=Depends(require_admin)):
    q = {"status": status} if status else {}
    subs = await db.subscriptions.find(q, {"_id": 0}).sort("created_at", -1).to_list(500)
    plans = {p["plan_id"]: p["name"] async for p in db.sub_plans.find({}, {"_id": 0, "plan_id": 1, "name": 1})}
    out = []
    for s in subs:
        charges = await db.sub_charges.find({"sub_id": s["sub_id"]}, {"_id": 0}).sort("created_at", -1).to_list(50)
        kits = await db.sub_kits.find({"sub_id": s["sub_id"]}, {"_id": 0}).sort("created_at", -1).to_list(50)
        out.append({**s, "plan_name": plans.get(s["plan_id"], "?"),
                    "status_label": SUB_STATUS_LABELS.get(s["status"], s["status"]),
                    "charges": charges, "kits": kits})
    return out


# ------------------- PÚBLICO -------------------
@api_router.get("/subscriptions/plans")
async def public_plans():
    """Somente planos PUBLICADOS e somente campos públicos."""
    s = await get_settings()
    plans = await db.sub_plans.find({"status": "published"}, {"_id": 0}).sort("position", 1).to_list(50)
    cycle = await _eligible_cycle()
    return {"enabled": _subs_cfg(s)["enabled"], "info": _subs_cfg(s)["info"],
            "plans": [_plan_public(p) for p in plans],
            "next_cycle": _cycle_public(cycle) if cycle else None}


def _cycle_public(c: dict) -> dict:
    return {"key": c["key"], "cutoff_at": c["cutoff_at"], "ship_at": c.get("ship_at"),
            "delivery_estimate": c.get("delivery_estimate", "")}


@api_router.get("/subscriptions/plans/{plan_id}")
async def public_plan_detail(plan_id: str):
    p = await db.sub_plans.find_one({"plan_id": plan_id, "status": "published"}, {"_id": 0})
    if not p:
        raise HTTPException(404, "Plano não encontrado")
    cycle = await _eligible_cycle()
    return {"plan": _plan_public(p), "next_cycle": _cycle_public(cycle) if cycle else None}


# ------------------- ADESÃO -------------------
@api_router.post("/subscriptions/subscribe")
async def subscribe(payload: SubscribeIn, request: Request):
    user = await require_user(request)
    validate_age(payload.birth_date)
    s = await get_settings()
    cfg = _subs_cfg(s)
    if not cfg["enabled"]:
        raise HTTPException(403, "As assinaturas ainda não estão abertas. Deixe seu interesse no atendimento.")
    plan = await db.sub_plans.find_one({"plan_id": payload.plan_id}, {"_id": 0})
    if not plan or plan.get("status") != "published":
        raise HTTPException(404, "Plano indisponível para adesão")
    if plan.get("price") is None or float(plan.get("price") or 0) <= 0:
        raise HTTPException(400, "Plano sem condições comerciais definidas")
    if not payload.accepted_terms:
        raise HTTPException(400, "É preciso aceitar as condições da assinatura e a adesão ao Clube")

    addr = payload.address or {}
    country = (addr.get("country") or "BR").upper()
    regions = plan.get("regions") or {}
    if country != "BR":
        if not regions.get("international_enabled") or country not in (regions.get("countries") or []):
            raise HTTPException(400, "Este plano ainda não atende o destino informado")
    elif regions.get("states") and (addr.get("state") or "").upper() not in [x.upper() for x in regions["states"]]:
        raise HTTPException(400, "Este plano ainda não atende o seu estado")

    limit = (plan.get("limits") or {}).get("max_subscribers")
    if limit is not None:
        n = await db.subscriptions.count_documents({"plan_id": plan["plan_id"],
                                                    "status": {"$in": ACTIVE_SUB_STATUSES}})
        if n >= int(limit):
            raise HTTPException(409, "Limite de assinantes deste plano atingido")

    # idempotência PRIMEIRO: mesmo client_request_id = mesma operação (clique repetido)
    ext_ref = f"sub:{payload.client_request_id}"
    existing = await db.subscriptions.find_one({"external_reference": ext_ref}, {"_id": 0})
    if existing:
        return {"sub_id": existing["sub_id"], "status": existing["status"],
                "mode": existing.get("payment_mode", "demo"), "idempotent": True}

    dup = await db.subscriptions.find_one({"user_id": user["user_id"], "plan_id": plan["plan_id"],
                                           "status": {"$in": ACTIVE_SUB_STATUSES}}, {"_id": 0})
    if dup:
        raise HTTPException(409, "Você já possui uma assinatura ativa deste plano")

    # frete: tabela própria da loja (CEP) ou regra do plano — sem inventar preços
    ship = plan.get("shipping") or {}
    shipping_info = {"mode": ship.get("mode", "table"), "price": 0.0, "note": ""}
    if country == "BR" and shipping_info["mode"] == "table":
        q = await shipping_quote(addr.get("cep", ""))
        shipping_info.update({"price": q["price"], "zone": q["zone"], "deadline": q.get("deadline")})
    elif shipping_info["mode"] == "custom":
        shipping_info["price"] = float(ship.get("custom_price") or 0)
    if ship.get("promo_note"):
        shipping_info["note"] = ship["promo_note"]

    payment_mode = s.get("payment_mode", "demo")
    cycle = await _eligible_cycle()
    snapshot = _plan_public(plan)
    sub_id = f"sub_{uuid.uuid4().hex[:12]}"
    doc = {
        "sub_id": sub_id, "user_id": user["user_id"], "user_email": user["email"],
        "plan_id": plan["plan_id"], "plan_version": plan.get("version", 1),
        "plan_snapshot": snapshot,
        "status": "awaiting_authorization",
        "demo": payment_mode == "demo", "payment_mode": payment_mode,
        "external_reference": ext_ref,
        "amount": round(float(plan["price"]), 2), "currency": "BRL",
        "shipping": shipping_info,
        "address": addr, "birth_date_attested": payload.birth_date,
        "first_cycle_id": cycle["cycle_id"] if cycle else None,
        "current_cycle_id": cycle["cycle_id"] if cycle else None,
        "next_charge_at": None,
        "club_adhesion": {"informed_at": now_utc().isoformat(), "terms_version": TERMS_VERSION,
                          "applied": False},
        "charges_blocked": False, "cancel": None, "pending_change": None,
        "created_at": now_utc().isoformat(), "updated_at": now_utc().isoformat(),
    }
    try:
        await db.subscriptions.insert_one(doc)
    except DuplicateKeyError:
        winner = await db.subscriptions.find_one({"external_reference": ext_ref}, {"_id": 0})
        return {"sub_id": winner["sub_id"], "status": winner["status"],
                "mode": winner.get("payment_mode", "demo"), "idempotent": True}

    await _track_server("subscription_start", sub_id=sub_id, plan=plan["name"],
                        demo=(payment_mode == "demo"))

    if payment_mode == "demo":
        return {"sub_id": sub_id, "status": "awaiting_authorization", "mode": "demo",
                "note": "Modo demonstração — nenhuma cobrança real será feita.",
                "first_cycle": _cycle_public(cycle) if cycle else None}

    # Mercado Pago PreApproval (teste/live): tokenização feita pelo provedor
    if not mp_configured():
        raise HTTPException(503, "Pagamento recorrente não configurado no servidor")
    try:
        pre = await create_preapproval(
            external_reference=ext_ref, payer_email=user["email"],
            reason=f"Assinatura {plan['name']} — Casa da Barrica Wines",
            amount=doc["amount"], frequency=plan["billing"]["frequency"],
            frequency_type=plan["billing"]["frequency_type"],
            card_token_id=payload.card_token_id)
    except Exception:
        logger.exception("falha ao criar preapproval")
        await db.subscriptions.update_one({"sub_id": sub_id},
                                          {"$set": {"status": "canceled",
                                                    "cancel": {"requested_at": now_utc().isoformat(),
                                                               "status": "confirmed",
                                                               "reason": "falha na criação no provedor"}}})
        raise HTTPException(502, "Falha ao registrar a assinatura no provedor de pagamento")
    mp_status = pre.get("status")
    await db.subscriptions.update_one({"sub_id": sub_id}, {"$set": {
        "mp_preapproval_id": pre["id"],
        "status": "active" if mp_status == "authorized" else "awaiting_authorization"}})
    return {"sub_id": sub_id,
            "status": "active" if mp_status == "authorized" else "awaiting_authorization",
            "mode": payment_mode, "init_point": pre.get("init_point"),
            "first_cycle": _cycle_public(cycle) if cycle else None}


@api_router.post("/subscriptions/mine/confirm-demo")
async def confirm_demo_subscription(request: Request):
    """Homologação em modo demo: simula a 1ª cobrança aprovada, SEM cobrança real
    e SEM pontos comerciais. Mesmo guard do checkout demo."""
    user = await require_user(request)
    s = await get_settings()
    if s.get("payment_mode") != "demo":
        raise HTTPException(400, "Loja não está em modo demonstração")
    sub = await db.subscriptions.find_one({"user_id": user["user_id"], "demo": True,
                                           "status": {"$in": ["awaiting_authorization", "active"]}},
                                          {"_id": 0})
    if not sub:
        raise HTTPException(404, "Nenhuma assinatura demo aguardando confirmação")
    cycle = await db.sub_cycles.find_one({"cycle_id": sub.get("first_cycle_id")}, {"_id": 0}) \
        if sub.get("first_cycle_id") else None
    charge = {"charge_id": f"chg_{uuid.uuid4().hex[:12]}", "sub_id": sub["sub_id"],
              "seq": 1, "cycle_id": (cycle or {}).get("cycle_id"), "amount": sub["amount"],
              "status": "pending", "demo": True, "created_at": now_utc().isoformat()}
    try:
        await db.sub_charges.insert_one(charge)
    except DuplicateKeyError:
        charge = await db.sub_charges.find_one({"sub_id": sub["sub_id"],
                                                "cycle_id": (cycle or {}).get("cycle_id")}, {"_id": 0})
    await _process_charge_approved(sub, charge, via="demo")
    await audit(user, "sub_demo_confirmed", {"sub_id": sub["sub_id"]})
    return {"ok": True, "status": "active"}


# ------------------- MINHA ASSINATURA -------------------
async def _my_sub(user: dict) -> dict:
    sub = await db.subscriptions.find_one(
        {"user_id": user["user_id"]}, {"_id": 0}, sort=[("created_at", -1)])
    if not sub:
        raise HTTPException(404, "Você ainda não possui assinatura")
    return sub


@api_router.get("/subscriptions/mine")
async def my_subscription(user=Depends(require_user)):
    sub = await _my_sub(user)
    charges = await db.sub_charges.find({"sub_id": sub["sub_id"]}, {"_id": 0}).sort("created_at", -1).to_list(100)
    kits = await db.sub_kits.find({"sub_id": sub["sub_id"]}, {"_id": 0}).sort("created_at", -1).to_list(100)
    cycle = await db.sub_cycles.find_one({"cycle_id": sub.get("current_cycle_id")}, {"_id": 0}) \
        if sub.get("current_cycle_id") else None
    next_cycle = await _eligible_cycle()
    return {"subscription": {**sub, "status_label": SUB_STATUS_LABELS.get(sub["status"], sub["status"])},
            "charges": charges, "kits": kits,
            "current_cycle": _cycle_public(cycle) if cycle else None,
            "next_cycle": _cycle_public(next_cycle) if next_cycle else None}


@api_router.patch("/subscriptions/mine/address")
async def my_subscription_address(payload: AddressIn, user=Depends(require_user)):
    sub = await _my_sub(user)
    if sub["status"] == "canceled":
        raise HTTPException(400, "Assinatura cancelada")
    addr = payload.address or {}
    cycle = await db.sub_cycles.find_one({"cycle_id": sub.get("current_cycle_id")}, {"_id": 0}) \
        if sub.get("current_cycle_id") else None
    blocked = False
    if cycle:
        cut = _parse_dt(cycle.get("cutoff_at"))
        kit = await db.sub_kits.find_one({"sub_id": sub["sub_id"], "cycle_id": cycle["cycle_id"]})
        if (cut and cut < now_utc()) or (kit and kit["status"] in ("reserved", "preparing", "shipped")):
            blocked = True
    if blocked:
        # respeita a data de corte: vale a partir do próximo ciclo
        await db.subscriptions.update_one({"sub_id": sub["sub_id"]},
                                          {"$set": {"pending_address": addr}})
        return {"ok": True, "effective": "next_cycle",
                "message": "Endereço atualizado para os próximos ciclos (o ciclo atual já passou da data de corte)."}
    await db.subscriptions.update_one({"sub_id": sub["sub_id"]}, {"$set": {"address": addr}})
    return {"ok": True, "effective": "current_cycle"}


@api_router.post("/subscriptions/mine/cancel")
async def cancel_my_subscription(request: Request, user=Depends(require_user)):
    """Cancelamento a qualquer momento, sem multa e sem atendimento. Bloqueia
    novas cobranças iniciadas pela loja imediatamente; o encerramento no
    provedor é confirmado de forma assíncrona quando necessário. Ciclos já
    pagos são preservados conforme as condições contratadas."""
    sub = await _my_sub(user)
    if sub["status"] == "canceled":
        return {"ok": True, "status": "canceled", "idempotent": True}
    if sub["status"] == "cancel_pending":
        return {"ok": True, "status": "cancel_pending"}
    now = now_utc().isoformat()
    # bloqueio imediato de cobranças futuras iniciadas pela loja
    await db.subscriptions.update_one({"sub_id": sub["sub_id"]}, {"$set": {
        "charges_blocked": True, "cancel": {"requested_at": now, "status": "processing"},
        "status": "cancel_pending", "updated_at": now}})

    if sub.get("mp_preapproval_id"):
        try:
            await cancel_preapproval(sub["mp_preapproval_id"])
            await db.subscriptions.update_one({"sub_id": sub["sub_id"]}, {"$set": {
                "status": "canceled", "cancel.status": "confirmed", "cancel.confirmed_at": now_utc().isoformat()}})
        except (httpx.HTTPStatusError, httpx.TimeoutException, Exception) as e:
            logger.warning("cancelamento MP pendente para %s: %s", sub["sub_id"], type(e).__name__)
            # conciliação conclui o cancelamento; loja já não inicia cobranças
    else:
        await db.subscriptions.update_one({"sub_id": sub["sub_id"]}, {"$set": {
            "status": "canceled", "cancel.status": "confirmed", "cancel.confirmed_at": now_utc().isoformat()}})
    fresh = await db.subscriptions.find_one({"sub_id": sub["sub_id"]}, {"_id": 0})
    await audit(user, "sub_cancel_requested", {"sub_id": sub["sub_id"], "result": fresh["status"]})
    asyncio.create_task(send_email(
        to=user["email"], subject="Cancelamento da sua assinatura — Casa da Barrica",
        html=("<p>Recebemos o cancelamento da sua assinatura. Cobranças futuras foram "
              "interrompidas. Benefícios e kits de ciclos já pagos são preservados conforme "
              "as condições contratadas. Sua conta e sua participação gratuita no Clube "
              "permanecem ativas, e o histórico segue disponível em Minha Conta.</p>")))
    return {"ok": True, "status": fresh["status"],
            "status_label": SUB_STATUS_LABELS.get(fresh["status"], fresh["status"])}


@api_router.post("/subscriptions/mine/kits/{kit_id}/confirm-received")
async def confirm_kit_received(kit_id: str, user=Depends(require_user)):
    r = await db.sub_kits.update_one(
        {"kit_id": kit_id, "status": "delivered",
         "sub_id": {"$in": [s["sub_id"] async for s in db.subscriptions.find(
             {"user_id": user["user_id"]}, {"_id": 0, "sub_id": 1})]}},
        {"$set": {"status": "received", "received_at": now_utc().isoformat(),
                  "received_by": "cliente"}})
    if r.modified_count == 0:
        raise HTTPException(400, "Kit não encontrado ou ainda não entregue pela transportadora")
    return {"ok": True}


@api_router.post("/subscriptions/mine/accept-change")
async def accept_plan_change(request: Request, user=Depends(require_user)):
    """Aceite registrado de alteração de preço/condições do plano."""
    sub = await _my_sub(user)
    pc = sub.get("pending_change")
    if not pc or pc.get("accepted"):
        raise HTTPException(400, "Não há alteração pendente de aceite")
    plan = await db.sub_plans.find_one({"plan_id": sub["plan_id"]}, {"_id": 0})
    if sub.get("mp_preapproval_id") and plan:
        try:
            await update_preapproval_amount(sub["mp_preapproval_id"], float(plan["price"]),
                                            plan["billing"]["frequency"],
                                            plan["billing"]["frequency_type"])
        except Exception:
            raise HTTPException(502, "Não foi possível atualizar o valor no provedor. Tente novamente.")
    await db.subscriptions.update_one({"sub_id": sub["sub_id"]}, {"$set": {
        "pending_change.accepted": True, "pending_change.accepted_at": now_utc().isoformat(),
        "amount": round(float(plan["price"]), 2), "plan_version": plan.get("version", 1),
        "plan_snapshot": _plan_public(plan)}})
    await audit(user, "sub_change_accepted", {"sub_id": sub["sub_id"], "version": plan.get("version")})
    return {"ok": True}


# ------------------- RECONCILIAÇÃO DE ASSINATURAS (consulta à API MP) -------------------
_MP_SUB_STATE = {"authorized": "active", "pending": "awaiting_authorization",
                 "paused": "payment_pending", "canceled": "canceled", "cancelled": "canceled"}


async def _reconcile_one_subscription(sub: dict) -> str:
    """Consulta preapproval + invoices no provedor e reflete localmente.
    Falhas de comunicação nunca viram cancelamento/inadimplência."""
    pid = sub.get("mp_preapproval_id")
    if not pid:
        return "sem_preapproval"
    try:
        pre = await get_preapproval(pid)
    except httpx.TimeoutException:
        return "erro:timeout"
    except httpx.HTTPStatusError as e:
        return f"erro:http_{e.response.status_code}"
    if pre is None:
        return "nao_encontrado"
    mp_state = _MP_SUB_STATE.get(pre.get("status"))
    if mp_state == "canceled":
        await db.subscriptions.update_one({"sub_id": sub["sub_id"]}, {"$set": {
            "status": "canceled", "cancel.status": "confirmed",
            "cancel.confirmed_at": now_utc().isoformat()}})
        return "cancelada_confirmada"
    if mp_state and sub["status"] not in ("canceled", "cancel_pending"):
        # cancel_pending: loja já bloqueou; não reativa por consulta fora de ordem
        await db.subscriptions.update_one({"sub_id": sub["sub_id"]}, {"$set": {"status": mp_state}})

    processed = 0
    offset = 0
    while True:
        page = await search_authorized_payments(pid, offset=offset)
        results = page.get("results", [])
        for inv in results:
            apid = str(inv.get("id"))
            pay_status = (inv.get("payment") or {}).get("status")
            charge = await db.sub_charges.find_one({"mp_authorized_payment_id": apid}, {"_id": 0})
            if not charge:
                cycle = await _next_cycle_for_charge(sub)
                seq = await db.sub_charges.count_documents({"sub_id": sub["sub_id"]}) + 1
                charge = {"charge_id": f"chg_{uuid.uuid4().hex[:12]}", "sub_id": sub["sub_id"],
                          "seq": seq, "cycle_id": (cycle or {}).get("cycle_id"),
                          "amount": float(inv.get("transaction_amount") or sub["amount"]),
                          "status": "pending", "demo": False,
                          "mp_authorized_payment_id": apid,
                          "created_at": now_utc().isoformat()}
                await db.sub_charges.insert_one(charge)
            if pay_status == "approved":
                await _process_charge_approved(sub, charge, via="reconciliacao_assinatura")
                processed += 1
            elif pay_status in ("rejected", "cancelled"):
                await db.sub_charges.update_one({"charge_id": charge["charge_id"]},
                                                {"$set": {"status": "rejected"}})
                if sub["status"] == "active":
                    await db.subscriptions.update_one({"sub_id": sub["sub_id"]},
                                                      {"$set": {"status": "delinquent"}})
                    asyncio.create_task(send_email(
                        to=sub["user_email"],
                        subject="Pagamento da assinatura recusado — Casa da Barrica",
                        html=("<p>Não conseguimos processar a cobrança da sua assinatura. "
                              "O provedor fará novas tentativas automaticamente. Atualize seu "
                              "cartão em Minha Conta &gt; Minha Assinatura para não perder o próximo kit.</p>")))
        paging = page.get("paging") or {}
        offset += len(results)
        if not results or offset >= int(paging.get("total", 0)):
            break
    return f"ok:{processed}"


_SUB_RECONCILE_LOCK = asyncio.Lock()


async def _reconcile_subscriptions(run_id: str):
    if _SUB_RECONCILE_LOCK.locked():
        await db.cron_runs.update_one({"run_id": run_id}, {"$set": {
            "run_id": run_id, "job": "reconcile-subscriptions", "status": "skipped_lock",
            "at": now_utc().isoformat()}}, upsert=True)
        return
    async with _SUB_RECONCILE_LOCK:
        stats = {"checked": 0, "charges_approved": 0, "errors": 0}
        try:
            subs = await db.subscriptions.find(
                {"mp_preapproval_id": {"$ne": None},
                 "status": {"$in": ["awaiting_authorization", "active", "payment_pending",
                                    "delinquent", "cancel_pending"]}},
                {"_id": 0}).to_list(100)
            for sub in subs:
                result = await _reconcile_one_subscription(sub)
                stats["checked"] += 1
                if result.startswith("ok:"):
                    stats["charges_approved"] += int(result.split(":", 1)[1])
                elif result.startswith("erro"):
                    stats["errors"] += 1
                if result == "erro:http_429":
                    break
            await db.cron_runs.update_one({"run_id": run_id}, {"$set": {
                "run_id": run_id, "job": "reconcile-subscriptions", "status": "ok",
                "stats": stats, "at": now_utc().isoformat()}}, upsert=True)
        except Exception as e:
            logger.exception("cron reconcile-subscriptions falhou")
            await db.cron_runs.update_one({"run_id": run_id}, {"$set": {
                "run_id": run_id, "job": "reconcile-subscriptions", "status": "error",
                "error": type(e).__name__, "at": now_utc().isoformat()}}, upsert=True)


@api_router.post("/cron/reconcile-subscriptions")
async def cron_reconcile_subscriptions(request: Request):
    import hmac as hmac_mod
    auth = request.headers.get("Authorization", "")
    token = auth.split(" ", 1)[1] if auth.startswith("Bearer ") else ""
    if not hmac_mod.compare_digest(token, WEBHOOK_CRON_SECRET):
        raise HTTPException(401, "Não autorizado")
    run_id = request.headers.get("X-Webhook-Id", f"manual_{uuid.uuid4().hex[:8]}")
    asyncio.create_task(_reconcile_subscriptions(run_id))
    return {"ok": True}


@api_router.get("/admin/subscriptions/reconciliation/status")
async def admin_sub_reconcile_status(admin=Depends(require_admin)):
    last = await db.cron_runs.find({"job": "reconcile-subscriptions"}, {"_id": 0}).sort("at", -1).to_list(1)
    active = await db.subscriptions.count_documents({"status": {"$in": list(ACTIVE_SUB_STATUSES)}})
    delinquent = await db.subscriptions.count_documents({"status": "delinquent"})
    return {"last_run": last[0] if last else None, "active_subscriptions": active,
            "delinquent": delinquent}
