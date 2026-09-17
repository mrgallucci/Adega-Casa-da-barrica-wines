from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, Response
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Literal
import uuid
import secrets
import hashlib
import hmac as hmac_mod
import asyncio
from datetime import datetime, timezone, timedelta
import jwt as pyjwt
import bcrypt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
import pyotp

from email_service import (
    send_welcome, send_password_reset, send_order_received,
    send_payment_approved, send_order_shipped, send_order_cancelled, send_mfa_recovery, email_configured,
)
import httpx  # exceções de timeout/HTTP tratadas na reconciliação de pagamentos
from payments_mp import mp_configured, create_preference, valid_webhook_signature, get_payment, search_payments, refund_payment, set_runtime_credentials, test_connection, _token_for
from storage_service import init_storage, put_object, get_object, APP_NAME

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

JWT_SECRET = os.environ['JWT_SECRET']
JWT_ALGO = "HS256"
JWT_EXP_HOURS = 24 * 7
OWNER_EMAIL = os.environ['OWNER_EMAIL']
EMERGENT_LLM_KEY = os.environ['EMERGENT_LLM_KEY']
from pymongo.errors import DuplicateKeyError
PUBLIC_APP_URL = os.environ.get('PUBLIC_APP_URL', '').rstrip('/')
WEBHOOK_CRON_SECRET = os.environ['WEBHOOK_CRON_SECRET']

ph = PasswordHasher()
RESERVATION_TTL_MIN = 30
ADMIN_ROLES = ("owner", "staff")
TERMS_VERSION = "1.0"  # versão dos Termos de Adesão do Clube registrada no aceite
ANALYTICS_RETENTION_DAYS = 180  # retenção dos eventos de analytics (TTL no Mongo)

MSG_CADASTRO_DUPLICADO = ("Já existe um cadastro com os dados informados. "
                          "Entre na sua conta ou utilize 'Esqueci minha senha'.")

def _norm_email(email: str) -> str:
    """Normalização consistente: trim + lowercase. Pontos e sufixos '+' são preservados."""
    return email.strip().lower()

async def _cpf_em_uso(cpf: str, exclude_user_id: Optional[str] = None) -> bool:
    q = {"club.cpf": cpf}
    if exclude_user_id:
        q["user_id"] = {"$ne": exclude_user_id}
    return bool(await db.users.find_one(q, {"_id": 1}))

def _valid_cpf(cpf: str) -> bool:
    d = [int(c) for c in cpf if c.isdigit()]
    if len(d) != 11 or len(set(d)) == 1:
        return False
    for i in (9, 10):
        chk = (sum(d[j] * ((i + 1) - j) for j in range(i)) * 10 % 11) % 10
        if chk != d[i]:
            return False
    return True

# Criptografia de segredos em repouso: chave derivada de segredo de ambiente do
# servidor (SECRETS_ENC_KEY, ou JWT_SECRET como fonte), NUNCA armazenada no banco.
from cryptography.fernet import Fernet
import base64
_key_src = os.environ.get("SECRETS_ENC_KEY") or JWT_SECRET
_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(_key_src.encode()).digest()))

def _enc(v: str) -> str:
    return _fernet.encrypt(v.encode()).decode()

def _dec(v: Optional[str]) -> Optional[str]:
    if not v:
        return v
    try:
        return _fernet.decrypt(v.encode()).decode()
    except Exception:
        return v  # legado em texto puro (migração)

app = FastAPI(title="Casa da Barrica Wines API")
api_router = APIRouter(prefix="/api")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------- MODELS -------------------
class ClubAddressIn(BaseModel):
    cep: Optional[str] = None
    street: Optional[str] = None
    number: Optional[str] = None
    complement: Optional[str] = None
    district: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None

class ClubJoinIn(BaseModel):
    """Adesão ao Clube: CPF/telefone/endereço são OPCIONAIS. Aceites são obrigatórios."""
    cpf: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[ClubAddressIn] = None
    optin_email: bool = False
    optin_whatsapp: bool = False
    accepted_terms: bool = False
    accepted_privacy: bool = False

class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str
    birth_date: str  # YYYY-MM-DD — controle de maioridade
    club: Optional[ClubJoinIn] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class ForgotIn(BaseModel):
    email: EmailStr

class ResetIn(BaseModel):
    token: str
    new_password: str = Field(min_length=8)

class MfaVerifyIn(BaseModel):
    mfa_token: str
    code: str

class MfaRecoveryRequestIn(BaseModel):
    email: EmailStr

class MfaRecoveryConfirmIn(BaseModel):
    token: str
    recovery_code: str

class VariantIn(BaseModel):
    variant_id: Optional[str] = None
    vintage: Optional[str] = None  # None/"sem safra" permitido
    volume_ml: int = 750
    sku: Optional[str] = None
    price: float
    discount_price: Optional[float] = None
    cost: Optional[float] = None
    stock: int = 0

class WineIn(BaseModel):
    name: str
    winery: Optional[str] = None
    country: Optional[str] = None
    region: Optional[str] = None
    denomination: Optional[str] = None
    type: Literal["Tinto", "Branco", "Rosé", "Espumante", "Fortificado", "Sobremesa"] = "Tinto"
    grapes: List[str] = []
    alcohol: Optional[str] = None
    sweetness: Optional[str] = None
    body: Optional[str] = None
    acidity: Optional[str] = None
    tannins: Optional[str] = None
    aromas: Optional[str] = None
    service_temp: Optional[str] = None
    decant: Optional[str] = None
    aging_potential: Optional[str] = None
    pairing_notes: Optional[str] = None
    story: Optional[str] = None
    sources: Optional[str] = None
    image: Optional[str] = None
    images: List[str] = []
    featured: bool = False
    badge: Optional[str] = None
    archived: bool = False
    variants: List[VariantIn] = []

class CartItem(BaseModel):
    wine_id: str
    variant_id: Optional[str] = None
    qty: int

class CheckoutIn(BaseModel):
    items: List[CartItem]
    cep: str
    address: str
    birth_date: str
    payment_method: Literal["pix", "credit_card"] = "pix"
    coupon: Optional[str] = None
    redeem_points: Optional[int] = None

class PairingRequest(BaseModel):
    dish: str
    preparation: Optional[str] = ""
    occasion: Optional[str] = ""
    preferences: Optional[str] = ""
    max_budget: Optional[float] = None

class CouponIn(BaseModel):
    code: str
    percent: float = Field(gt=0, le=100)
    active: bool = True
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    max_uses: Optional[int] = None

class StockEntryIn(BaseModel):
    wine_id: str
    variant_id: Optional[str] = None
    qty: int
    reason: str
    supplier: Optional[str] = None
    unit_cost: Optional[float] = None

class SettingsIn(BaseModel):
    store_name: Optional[str] = None
    tagline: Optional[str] = None
    home: Optional[dict] = None
    footer: Optional[dict] = None
    payment_mode: Optional[Literal["demo", "mercadopago_test", "mercadopago_live"]] = None
    sales_status: Optional[Literal["open", "suspended"]] = None
    points: Optional[dict] = None
    chat: Optional[dict] = None

class ProfileIn(BaseModel):
    name: Optional[str] = None
    marketing_opt_in: Optional[bool] = None
    wine_preferences: Optional[str] = None

class StaffGrantIn(BaseModel):
    email: EmailStr
    role: Literal["staff", "customer"]

class ShippingZoneIn(BaseModel):
    zone_id: Optional[str] = None
    name: str
    cep_prefixes: List[str]  # prefixos de CEP atendidos, ex.: ["01", "02", "13"]
    price: float = Field(ge=0)
    deadline: Optional[str] = None  # ex.: "2 a 4 dias úteis" — prazo real da operação
    active: bool = True
    confirmed: bool = False  # só faz efeito após confirmação do proprietário

class ResolveStockIn(BaseModel):
    action: Literal["fulfill", "refund"]
    reason: str = Field(min_length=3)
    amount: Optional[float] = None  # reembolso parcial; None = total

# ------------------- HELPERS -------------------
def now_utc():
    return datetime.now(timezone.utc)

def hash_password(pw: str) -> str:
    return ph.hash(pw)

def verify_password(pw: str, hashed: str) -> tuple[bool, bool]:
    """Retorna (ok, precisa_rehash). Suporta bcrypt legado e Argon2id."""
    if not hashed:
        return False, False
    try:
        if hashed.startswith("$2"):
            return bcrypt.checkpw(pw.encode(), hashed.encode()), True
        return ph.verify(hashed, pw), ph.check_needs_rehash(hashed)
    except (VerifyMismatchError, Exception):
        return False, False

def create_jwt(user: dict) -> str:
    payload = {
        "user_id": user["user_id"],
        "pv": user.get("password_version", 0),
        "exp": now_utc() + timedelta(hours=JWT_EXP_HOURS),
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)

def create_mfa_token(user_id: str, purpose: str) -> str:
    return pyjwt.encode(
        {"user_id": user_id, "purpose": purpose, "exp": now_utc() + timedelta(minutes=10)},
        JWT_SECRET, algorithm=JWT_ALGO)

async def audit(user: dict, action: str, details: dict):
    """Trilha de auditoria administrativa (append-only)."""
    await db.audit_log.insert_one({
        "audit_id": f"aud_{uuid.uuid4().hex[:12]}",
        "user_id": user.get("user_id"), "email": user.get("email"),
        "action": action, "details": {k: v for k, v in details.items() if k not in ("password", "token", "secret")},
        "created_at": now_utc().isoformat(),
    })

# --- rate limit simples (login/reset): 10 tentativas / 10 min por e-mail+IP ---
_attempts: dict = {}
def rate_limit(key: str, limit: int = 10, window_s: int = 600):
    now = now_utc().timestamp()
    hits = [t for t in _attempts.get(key, []) if now - t < window_s]
    if len(hits) >= limit:
        raise HTTPException(429, "Muitas tentativas. Aguarde alguns minutos.")
    hits.append(now)
    _attempts[key] = hits

async def get_current_user(request: Request) -> Optional[dict]:
    token = None
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        token = auth.split(" ", 1)[1]
    if token:
        try:
            payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
            if payload.get("purpose"):
                return None  # tokens de MFA não autenticam
            user = await db.users.find_one({"user_id": payload["user_id"]}, {"_id": 0})
            if user and not user.get("deleted") and payload.get("pv", 0) == user.get("password_version", 0):
                return user
        except Exception:
            pass
    session_token = request.cookies.get("session_token") or token
    if session_token:
        session = await db.user_sessions.find_one({"session_token": session_token}, {"_id": 0})
        if session:
            expires_at = session.get("expires_at")
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at)
            if expires_at and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at and expires_at > now_utc():
                user = await db.users.find_one({"user_id": session["user_id"]}, {"_id": 0})
                if user and not user.get("deleted") and session.get("pv", 0) == user.get("password_version", 0):
                    return user
    return None

async def require_user(request: Request) -> dict:
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Autenticação necessária")
    if user.get("deleted") or user.get("blocked"):
        raise HTTPException(403, "Conta desativada ou bloqueada")
    return user

async def require_admin(request: Request) -> dict:
    """Papel re-derivado do banco a cada requisição (revogação imediata)."""
    user = await require_user(request)
    fresh = await db.users.find_one({"user_id": user["user_id"]}, {"_id": 0})
    if not fresh or fresh.get("role") not in ADMIN_ROLES:
        raise HTTPException(403, "Acesso restrito a administradores")
    if not fresh.get("mfa_enabled"):
        raise HTTPException(403, "MFA obrigatório para administradores")
    return fresh

def validate_age(birth_date: str):
    try:
        bd = datetime.strptime(birth_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(400, "Data de nascimento inválida (use AAAA-MM-DD)")
    age = (now_utc() - bd).days / 365.25
    if age < 18:
        raise HTTPException(403, "Venda proibida para menores de 18 anos")
    if age > 120:
        raise HTTPException(400, "Data de nascimento inválida")

# ------------------- VARIANT / STOCK -------------------
def default_variant(w: dict) -> dict:
    return {
        "variant_id": "default",
        "vintage": w.get("vintage"),
        "volume_ml": w.get("volume_ml", 750),
        "sku": w.get("sku"),
        "price": w.get("price", 0),
        "discount_price": w.get("discount_price"),
        "cost": w.get("cost"),
        "stock": w.get("stock", 0),
        "reserved": 0,
    }

async def find_variant(wine_id: str, variant_id: Optional[str]) -> tuple[dict, dict]:
    wine = await db.wines.find_one({"wine_id": wine_id}, {"_id": 0})
    if not wine or wine.get("archived"):
        raise HTTPException(400, f"Vinho indisponível: {wine_id}")
    variants = wine.get("variants") or [default_variant(wine)]
    vid = variant_id or variants[0]["variant_id"]
    for v in variants:
        if v["variant_id"] == vid:
            return wine, v
    raise HTTPException(400, "Variante não encontrada")

async def reserve_stock(wine_id: str, variant_id: str, qty: int) -> bool:
    """Reserva atômica: só incrementa 'reserved' se (stock - reserved) >= qty.
    Antes, libera preguiçosamente reservas expiradas deste vinho para que o prazo
    de 30 min informado ao cliente seja cumprido mesmo entre execuções do cron."""
    await _release_expired_for_wine(wine_id)
    r = await db.wines.update_one(
        {"wine_id": wine_id, "$expr": {"$gte": [
            {"$let": {"vars": {"v": {"$first": {"$filter": {"input": "$variants", "as": "x",
                "cond": {"$eq": ["$$x.variant_id", variant_id]}}}}},
                "in": {"$subtract": ["$$v.stock", {"$ifNull": ["$$v.reserved", 0]}]}}},
            qty]}},
        {"$inc": {"variants.$[v].reserved": qty}},
        array_filters=[{"v.variant_id": variant_id}],
    )
    return r.modified_count == 1

async def release_stock(wine_id: str, variant_id: str, qty: int):
    await db.wines.update_one(
        {"wine_id": wine_id},
        {"$inc": {"variants.$[v].reserved": -qty}},
        array_filters=[{"v.variant_id": variant_id, "v.reserved": {"$gte": qty}}],
    )

async def convert_reservation(wine_id: str, variant_id: str, qty: int) -> bool:
    """Baixa definitiva: stock -= qty e reserved -= qty, atomicamente."""
    r = await db.wines.update_one(
        {"wine_id": wine_id},
        {"$inc": {"variants.$[v].stock": -qty, "variants.$[v].reserved": -qty}},
        array_filters=[{"v.variant_id": variant_id, "v.reserved": {"$gte": qty}}],
    )
    return r.modified_count == 1

async def log_movement(wine_id: str, variant_id: str, mov_type: str, qty: int, reason: str, user: Optional[dict], extra: dict = None):
    await db.stock_movements.insert_one({
        "movement_id": f"mov_{uuid.uuid4().hex[:12]}",
        "wine_id": wine_id, "variant_id": variant_id, "type": mov_type, "qty": qty,
        "reason": reason, "user_email": (user or {}).get("email", "sistema"),
        "created_at": now_utc().isoformat(), **(extra or {}),
    })

async def sync_display_fields(wine_id: str):
    """Mantém campos legados (preço/estoque de exibição) coerentes com variantes."""
    wine = await db.wines.find_one({"wine_id": wine_id}, {"_id": 0})
    if not wine:
        return
    variants = wine.get("variants") or []
    if not variants:
        return
    active = [v for v in variants]
    prices = [v.get("discount_price") or v["price"] for v in active]
    await db.wines.update_one({"wine_id": wine_id}, {"$set": {
        "price": min(v["price"] for v in active),
        "discount_price": min(prices) if prices else None,
        "stock": sum(v.get("stock", 0) - v.get("reserved", 0) for v in active),
    }})

ALLOWED_CONTENT_LINKS = ["/", "/catalogo", "/harmonizar", "/carrinho", "/conta", "/login"]
HOME_LINK_KEYS = {"hero_primary_href", "hero_secondary_href", "cta_button_href"}

DEFAULT_HOME_CONTENT = {
    "hero_eyebrow": "Adega Rústica Sofisticada",
    "hero_title_start": "Cada garrafa,",
    "hero_title_highlight": "uma história",
    "hero_title_end": "para servir.",
    "hero_description": "Vinhos selecionados manualmente, harmonização inteligente com o Sommelier Virtual e entrega climatizada. Bem-vindo à sua adega pessoal.",
    "hero_primary_label": "Explorar Catálogo",
    "hero_primary_href": "/catalogo",
    "hero_secondary_label": "Harmonizar meu prato",
    "hero_secondary_href": "/harmonizar",
    "featured_eyebrow": "Seleção do sommelier",
    "featured_title": "Rótulos em destaque",
    "cta_eyebrow": "Experiência exclusiva",
    "cta_title": "Qual vinho combina com seu prato?",
    "cta_description": "Descreva o prato, a ocasião e seu orçamento. Nosso sommelier virtual sugere até três garrafas da nossa adega, com explicação técnica.",
    "cta_button_label": "Consultar sommelier",
    "cta_button_href": "/harmonizar",
    "card_badge": "Novo",
    "card_text": "Sommelier Virtual",
    "card_title": "Qual vinho combina com seu prato?",
    "card_description": "Sugestões personalizadas para o seu prato em segundos.",
    "card_button_label": "Ver harmonização",
}

DEFAULT_FOOTER_CONTENT = {
    "about": "Casa da Barrica Wines — e-commerce premium de vinhos com curadoria de sommelier, harmonização com o Sommelier Virtual e entrega climatizada.",
    "email": "contato@casadabarrica.com.br",
    "phone": "(11) 4000-0000",
    "address": "São Paulo — SP",
}

DEFAULT_POINTS_CONFIG = {
    "earn_enabled": True,       # crédito de pontos em compras REAIS aprovadas
    "reais_per_point": 5.0,     # 1 ponto a cada R$ 5 em produtos (sem frete)
    "redeem_enabled": False,    # resgate DESATIVADO até o proprietário definir o valor
    "point_value_brl": 0.0,     # R$ por ponto no resgate (definido pelo proprietário)
    "max_redeem_percent": 50,   # limite do resgate sobre o valor dos produtos
}

POINTS_KEYS = set(DEFAULT_POINTS_CONFIG.keys())

def _points_cfg(s: dict) -> dict:
    return {**DEFAULT_POINTS_CONFIG, **(s.get("points") or {})}

def _validate_points(p: dict, current: dict) -> dict:
    clean = {}
    for k, v in p.items():
        if k not in POINTS_KEYS:
            raise HTTPException(400, f"Configuração de pontos desconhecida: {k}")
        if k in ("earn_enabled", "redeem_enabled"):
            if not isinstance(v, bool):
                raise HTTPException(400, f"{k} deve ser verdadeiro/falso")
            clean[k] = v
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise HTTPException(400, f"{k} deve ser numérico")
        v = float(v)
        if k == "reais_per_point" and not (0.5 <= v <= 10000):
            raise HTTPException(400, "reais_per_point deve estar entre 0,5 e 10000")
        if k == "point_value_brl" and not (0 <= v <= 100):
            raise HTTPException(400, "Valor do ponto deve estar entre 0 e 100")
        if k == "max_redeem_percent" and not (1 <= v <= 100):
            raise HTTPException(400, "Limite do resgate deve estar entre 1% e 100%")
        clean[k] = v
    merged = {**current, **clean}
    if merged.get("redeem_enabled") and merged.get("point_value_brl", 0) <= 0:
        raise HTTPException(400, "Defina o valor do ponto (R$) antes de ativar o resgate")
    return clean

CHAT_RETENTION_DAYS = 90  # retenção das conversas do atendimento (TTL no Mongo)
WHATSAPP_DEFAULT_NUMBER = "5524981293634"
WHATSAPP_MSG = "Olá! Preciso de ajuda com a Casa da Barrica Wines."

DEFAULT_CHAT_CONFIG = {
    "enabled": False,  # entregue DESATIVADO até o proprietário revisar limites e custos
    "greeting": "Olá! Sou o Sommelier Virtual da Casa da Barrica Wines, um assistente automatizado. "
                "Posso ajudar com vinhos, harmonizações, cadastro, Clube, pontos, frete e funcionamento da loja.",
    "atendimento_info": "Atendimento humano pelo WhatsApp em horário comercial.",
    "faqs": "",
    "whatsapp_number": WHATSAPP_DEFAULT_NUMBER,
    "instagram_url": "",
    "max_messages_per_session": 20,
}

CHAT_KEYS = set(DEFAULT_CHAT_CONFIG.keys())

def _chat_cfg(s: dict) -> dict:
    return {**DEFAULT_CHAT_CONFIG, **(s.get("chat") or {})}

def _wa_link(number: str) -> str:
    from urllib.parse import quote
    return f"https://wa.me/{number}?text={quote(WHATSAPP_MSG)}"

def _validate_chat(p: dict, current: dict) -> dict:
    clean = {}
    for k, v in p.items():
        if k not in CHAT_KEYS:
            raise HTTPException(400, f"Configuração de atendimento desconhecida: {k}")
        if k == "enabled":
            if not isinstance(v, bool):
                raise HTTPException(400, "enabled deve ser verdadeiro/falso")
            clean[k] = v
            continue
        if k == "max_messages_per_session":
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not (1 <= int(v) <= 200):
                raise HTTPException(400, "Limite de mensagens deve ser um número entre 1 e 200")
            clean[k] = int(v)
            continue
        if not isinstance(v, str):
            raise HTTPException(400, f"{k} deve ser texto")
        v = v.strip()
        if k == "whatsapp_number":
            digits = "".join(ch for ch in v if ch.isdigit())
            if not (10 <= len(digits) <= 15):
                raise HTTPException(400, "WhatsApp inválido — use DDI+DDD+número, somente dígitos")
            clean[k] = digits
            continue
        if k == "instagram_url":
            import re as _re
            if v and not _re.match(r"^https://(www\.)?instagram\.com/[\w./?=_-]*$", v):
                raise HTTPException(400, "Instagram inválido — use a URL HTTPS completa do perfil (https://instagram.com/seu_perfil)")
            clean[k] = v
            continue
        limits = {"greeting": 600, "atendimento_info": 2000, "faqs": 4000}
        if len(v) > limits.get(k, 2000):
            raise HTTPException(400, f"{k} excede o tamanho máximo")
        clean[k] = v
    return clean


def _validate_content(data: dict, defaults: dict, link_keys: set) -> dict:
    clean = {}
    for k, v in data.items():
        if k not in defaults:
            raise HTTPException(400, f"Campo de conteúdo desconhecido: {k}")
        if not isinstance(v, str):
            raise HTTPException(400, "Conteúdo deve ser texto")
        v = v.strip()
        if not v or len(v) > 500:
            raise HTTPException(400, "Conteúdo vazio ou longo demais (máx. 500 caracteres)")
        if k in link_keys and v not in ALLOWED_CONTENT_LINKS:
            raise HTTPException(400, "Destino inválido — escolha uma página da loja")
        if k == "email" and ("@" not in v or "." not in v.split("@")[-1]):
            raise HTTPException(400, "E-mail de contato inválido")
        clean[k] = v
    return clean


async def get_settings() -> dict:
    s = await db.store_settings.find_one({"key": "store"}, {"_id": 0})
    s = s or {"key": "store", "store_name": "Casa da Barrica Wines",
              "tagline": "Curadoria, Histórias & Descobertas em cada vinho",
              "payment_mode": "demo", "sales_status": "open"}
    s["home"] = {**DEFAULT_HOME_CONTENT, **s.get("home", {})}
    s["footer"] = {**DEFAULT_FOOTER_CONTENT, **s.get("footer", {})}
    s["points"] = _points_cfg(s)
    s["chat"] = _chat_cfg(s)
    return s

# ------------------- AUTH -------------------
def _finish_login(user: dict) -> dict:
    role = user.get("role", "customer")
    if role in ADMIN_ROLES:
        if not user.get("mfa_enabled"):
            return {"mfa_setup_required": True, "mfa_token": create_mfa_token(user["user_id"], "mfa_setup")}
        return {"mfa_required": True, "mfa_token": create_mfa_token(user["user_id"], "mfa_verify")}
    token = create_jwt(user)
    return {"token": token, "user": {"user_id": user["user_id"], "email": user["email"], "name": user["name"], "role": role, "homologation_buyer": bool(user.get("homologation_buyer"))}}

def _club_payload(c: ClubJoinIn) -> dict:
    """Valida e normaliza os dados opcionais do Clube (CPF com dígitos verificadores)."""
    cpf = "".join(ch for ch in c.cpf if ch.isdigit()) if c.cpf else None
    if cpf and not _valid_cpf(cpf):
        raise HTTPException(400, "CPF inválido")
    addr = {k: (v.strip() if isinstance(v, str) else v)
            for k, v in (c.address.model_dump() if c.address else {}).items() if v}
    if addr.get("cep"):
        cep_digits = "".join(ch for ch in addr["cep"] if ch.isdigit())
        if len(cep_digits) != 8:
            raise HTTPException(400, "CEP do endereço inválido (8 dígitos)")
        addr["cep"] = cep_digits
    return {"cpf": cpf or None, "phone": (c.phone or "").strip() or None,
            "address": addr or None,
            "optin_email": bool(c.optin_email), "optin_whatsapp": bool(c.optin_whatsapp)}

@api_router.post("/auth/register")
async def register(payload: UserCreate, request: Request):
    email = _norm_email(payload.email)
    rate_limit(f"reg:{email}:{request.client.host}", limit=20)
    validate_age(payload.birth_date)
    existing = await db.users.find_one({"email": email}, {"_id": 0})
    if existing:
        raise HTTPException(400, MSG_CADASTRO_DUPLICADO)
    club = None
    if payload.club is not None:
        if not payload.club.accepted_terms or not payload.club.accepted_privacy:
            raise HTTPException(400, "Para participar do Clube é preciso aceitar os Termos de Adesão e a Política de Privacidade")
        club = _club_payload(payload.club)
        if club["cpf"] and await _cpf_em_uso(club["cpf"]):
            raise HTTPException(400, MSG_CADASTRO_DUPLICADO)
    user_id = f"user_{uuid.uuid4().hex[:12]}"
    doc = {
        "user_id": user_id,
        "email": email,
        "name": payload.name,
        "role": "customer",  # papel nunca vem do cliente; admin só via proprietário
        "birth_date": payload.birth_date,
        "password_hash": hash_password(payload.password),
        "password_version": 1,
        "mfa_enabled": False,
        "marketing_opt_in": club["optin_email"] if club else False,
        "optin_email": club["optin_email"] if club else False,
        "optin_whatsapp": club["optin_whatsapp"] if club else False,
        "club_member": club is not None,
        "points_balance": 0,
        "created_at": now_utc().isoformat(),
    }
    if club:
        doc["club"] = {"cpf": club["cpf"], "phone": club["phone"], "address": club["address"]}
        doc["club_terms_version"] = TERMS_VERSION
        doc["club_accepted_at"] = now_utc().isoformat()
    try:
        await db.users.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(400, MSG_CADASTRO_DUPLICADO)
    asyncio.create_task(send_welcome(doc["email"], doc["name"]))
    await _track_server("signup", via="email")
    token = create_jwt(doc)
    return {"token": token, "user": {"user_id": user_id, "email": doc["email"], "name": doc["name"], "role": "customer"}}

@api_router.post("/auth/login")
async def login(payload: UserLogin, request: Request):
    email = _norm_email(payload.email)
    rate_limit(f"login:{email}:{request.client.host}")
    user = await db.users.find_one({"email": email}, {"_id": 0})
    ok, needs_rehash = verify_password(payload.password, (user or {}).get("password_hash"))
    if not user or not ok:
        raise HTTPException(401, "Credenciais inválidas")
    if needs_rehash:
        await db.users.update_one({"user_id": user["user_id"]},
                                  {"$set": {"password_hash": hash_password(payload.password)}})
    return _finish_login(user)

@api_router.post("/auth/mfa/setup")
async def mfa_setup(payload: dict):
    """Gera segredo TOTP para admin. Requer mfa_token com purpose=mfa_setup."""
    try:
        data = pyjwt.decode(payload.get("mfa_token", ""), JWT_SECRET, algorithms=[JWT_ALGO])
        assert data.get("purpose") == "mfa_setup"
    except Exception:
        raise HTTPException(401, "Token inválido ou expirado")
    secret = pyotp.random_base32()
    await db.users.update_one({"user_id": data["user_id"]}, {"$set": {"mfa_secret_pending": secret}})
    user = await db.users.find_one({"user_id": data["user_id"]}, {"_id": 0})
    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=user["email"], issuer_name="Casa da Barrica Wines")
    return {"secret": secret, "otpauth_url": uri}

@api_router.post("/auth/mfa/verify")
async def mfa_verify(payload: MfaVerifyIn):
    try:
        data = pyjwt.decode(payload.mfa_token, JWT_SECRET, algorithms=[JWT_ALGO])
    except Exception:
        raise HTTPException(401, "Token inválido ou expirado")
    user = await db.users.find_one({"user_id": data["user_id"]}, {"_id": 0})
    if not user:
        raise HTTPException(401, "Usuário não encontrado")
    if data.get("purpose") == "mfa_setup":
        secret = user.get("mfa_secret_pending")
        valid = bool(secret) and pyotp.TOTP(secret).verify(payload.code, valid_window=1)
    else:
        secret = user.get("mfa_secret")
        valid = bool(secret) and pyotp.TOTP(secret).verify(payload.code, valid_window=1)
        if not valid:
            # códigos de recuperação de uso único (hash SHA-256 no banco)
            code_hash = hashlib.sha256(payload.code.strip().lower().encode()).hexdigest()
            r = await db.users.update_one(
                {"user_id": user["user_id"], "mfa_recovery_codes": {"$elemMatch": {"hash": code_hash, "used": False}}},
                {"$set": {"mfa_recovery_codes.$.used": True}})
            valid = r.modified_count == 1
            if valid:
                await audit(user, "mfa_recovery_code_used", {})
    if not valid:
        raise HTTPException(401, "Código inválido")
    recovery_codes = None
    if data.get("purpose") == "mfa_setup":
        raw_codes = [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(8)]
        await db.users.update_one({"user_id": user["user_id"]},
                                  {"$set": {"mfa_enabled": True, "mfa_secret": secret,
                                            "mfa_recovery_codes": [{"hash": hashlib.sha256(c.encode()).hexdigest(), "used": False} for c in raw_codes]},
                                   "$unset": {"mfa_secret_pending": ""}})
        await audit(user, "mfa_enabled", {})
        recovery_codes = raw_codes
    token = create_jwt(user)
    # Verificação só conclui com MFA ativo (setup acabou de ativá-lo; verify exigia mfa_enabled).
    # O indicador precisa ir no payload: o frontend (AccountPage) libera o painel com base nele.
    return {"token": token, "user": {"user_id": user["user_id"], "email": user["email"], "name": user["name"],
            "role": user.get("role", "customer"), "mfa_enabled": True},
            **({"recovery_codes": recovery_codes} if recovery_codes else {})}

@api_router.post("/auth/forgot-password")
async def forgot_password(payload: ForgotIn, request: Request):
    rate_limit(f"forgot:{payload.email.lower()}:{request.client.host}", limit=5)
    user = await db.users.find_one({"email": payload.email.lower()}, {"_id": 0})
    if user and (user.get("password_hash") is not None or payload.email.lower() == OWNER_EMAIL.lower()):
        if user:
            raw = secrets.token_urlsafe(32)
            await db.password_resets.insert_one({
                "token_hash": hashlib.sha256(raw.encode()).hexdigest(),
                "user_id": user["user_id"], "used": False,
                "expires_at": (now_utc() + timedelta(minutes=30)).isoformat(),
                "expires_at_dt": now_utc() + timedelta(minutes=30),
                "created_at": now_utc().isoformat(),
            })
            asyncio.create_task(send_password_reset(
                user["email"], user.get("name", ""), f"{PUBLIC_APP_URL}/redefinir-senha?token={raw}"))
    # resposta idêntica independente de existência (anti-enumeração)
    return {"ok": True, "message": "Se o e-mail existir, enviaremos um link de redefinição."}

@api_router.post("/auth/reset-password")
async def reset_password(payload: ResetIn):
    token_hash = hashlib.sha256(payload.token.encode()).hexdigest()
    rec = await db.password_resets.find_one({"token_hash": token_hash}, {"_id": 0})
    if not rec or rec.get("used"):
        raise HTTPException(400, "Link inválido ou já utilizado")
    exp = datetime.fromisoformat(rec["expires_at"])
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < now_utc():
        raise HTTPException(400, "Link expirado")
    # marca como usado atomicamente (uso único)
    r = await db.password_resets.update_one({"token_hash": token_hash, "used": False}, {"$set": {"used": True}})
    if r.modified_count == 0:
        raise HTTPException(400, "Link já utilizado")
    user = await db.users.find_one({"user_id": rec["user_id"]}, {"_id": 0})
    pv = user.get("password_version", 0) + 1
    update = {"$set": {"password_hash": hash_password(payload.new_password), "password_version": pv}}
    if user.get("mfa_residual_test"):
        # RECUPERAÇÃO EXCEPCIONAL DE USO ÚNICO: a conta tinha configuração de MFA
        # comprovadamente residual (criada por teste automatizado e marcada pelo
        # operador com mfa_residual_test=true). A marca é consumida aqui — redefinições
        # futuras de senha PRESERVAM o MFA legítimo.
        update["$set"]["mfa_enabled"] = False
        update["$unset"] = {"mfa_secret": "", "mfa_secret_pending": "", "mfa_recovery_codes": "", "mfa_residual_test": ""}
        await audit(user, "mfa_residual_cleared_once", {})
    else:
        await audit(user, "password_reset", {"mfa_preserved": bool(user.get("mfa_enabled"))})
    await db.users.update_one({"user_id": rec["user_id"]}, update)
    await db.user_sessions.delete_many({"user_id": rec["user_id"]})  # revoga sessões Google
    return {"ok": True}

@api_router.post("/auth/mfa/recovery-request")
async def mfa_recovery_request(payload: MfaRecoveryRequestIn, request: Request):
    """Fluxo SEPARADO de recuperação de MFA (perda do autenticador legítimo).
    Verificação de identidade = posse da caixa de e-mail da conta. Link de uso
    único, 30 min. Não redefine senha nem concede acesso — apenas permite
    recadastrar o autenticador após novo login com senha."""
    rate_limit(f"mfarec:{payload.email.lower()}:{request.client.host}", limit=5)
    user = await db.users.find_one({"email": payload.email.lower()}, {"_id": 0})
    if user and user.get("mfa_enabled") and not user.get("deleted"):
        raw = secrets.token_urlsafe(32)
        await db.mfa_recoveries.insert_one({
            "token_hash": hashlib.sha256(raw.encode()).hexdigest(),
            "user_id": user["user_id"], "used": False,
            "expires_at": (now_utc() + timedelta(minutes=30)).isoformat(),
            "expires_at_dt": now_utc() + timedelta(minutes=30),
            "created_at": now_utc().isoformat(),
        })
        asyncio.create_task(send_mfa_recovery(
            user["email"], user.get("name", ""), f"{PUBLIC_APP_URL}/recuperar-mfa?token={raw}"))
        await audit(user, "mfa_recovery_requested", {})
    return {"ok": True, "message": "Se a conta existir e tiver MFA ativo, enviaremos um link de recuperação."}

@api_router.post("/auth/mfa/recovery-confirm")
async def mfa_recovery_confirm(payload: MfaRecoveryConfirmIn):
    """Recuperação administrativa de MFA. O e-mail SOZINHO nunca remove o MFA:
    é exigido também um código de recuperação de uso único (gerado no setup do
    autenticador) — outro fator previamente cadastrado. Sem códigos disponíveis,
    o caminho é o procedimento manual de verificação de identidade."""
    token_hash = hashlib.sha256(payload.token.encode()).hexdigest()
    rec = await db.mfa_recoveries.find_one({"token_hash": token_hash}, {"_id": 0})
    if not rec or rec.get("used"):
        raise HTTPException(400, "Link inválido ou já utilizado")
    exp = datetime.fromisoformat(rec["expires_at"])
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < now_utc():
        raise HTTPException(400, "Link expirado")
    user = await db.users.find_one({"user_id": rec["user_id"]}, {"_id": 0})
    if not user or user.get("deleted"):
        raise HTTPException(400, "Conta inválida")
    codes = user.get("mfa_recovery_codes") or []
    if not any(not c.get("used") for c in codes):
        raise HTTPException(400, "Sem códigos de recuperação disponíveis. Procedimento manual: verificação de identidade documental fora do sistema; um operador remove o MFA apenas após essa verificação.")
    # Segundo fator: código de recuperação de uso único (consumido atomicamente)
    code_hash = hashlib.sha256(payload.recovery_code.strip().lower().encode()).hexdigest()
    r2 = await db.users.update_one(
        {"user_id": user["user_id"], "mfa_recovery_codes": {"$elemMatch": {"hash": code_hash, "used": False}}},
        {"$set": {"mfa_recovery_codes.$.used": True}})
    if r2.modified_count == 0:
        # link NÃO é consumido: o usuário pode tentar outro código dentro da validade
        raise HTTPException(400, "Código de recuperação inválido ou já utilizado")
    r = await db.mfa_recoveries.update_one({"token_hash": token_hash, "used": False}, {"$set": {"used": True}})
    if r.modified_count == 0:
        raise HTTPException(400, "Link já utilizado")
    # Remove o MFA perdido e revoga TODAS as sessões/JWTs: o usuário precisa entrar
    # com a senha (segunda prova de identidade) e recadastrar o autenticador.
    pv = user.get("password_version", 0) + 1
    await db.users.update_one({"user_id": rec["user_id"]},
        {"$set": {"mfa_enabled": False, "password_version": pv},
         "$unset": {"mfa_secret": "", "mfa_secret_pending": "", "mfa_recovery_codes": ""}})
    await db.user_sessions.delete_many({"user_id": rec["user_id"]})
    await audit(user, "mfa_recovery_completed", {"second_factor": "recovery_code"})
    return {"ok": True, "message": "MFA removido. Entre com sua senha e cadastre um novo autenticador."}

@api_router.get("/auth/me")
async def me(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Não autenticado")
    return {"user_id": user["user_id"], "email": user["email"], "name": user["name"],
            "role": user.get("role", "customer"), "picture": user.get("picture"),
            "marketing_opt_in": user.get("marketing_opt_in", False),
            "mfa_enabled": user.get("mfa_enabled", False),
            "homologation_buyer": bool(user.get("homologation_buyer")),
            "club_member": bool(user.get("club_member")),
            "optin_email": user.get("optin_email", user.get("marketing_opt_in", False)),
            "optin_whatsapp": user.get("optin_whatsapp", False),
            "points_balance": user.get("points_balance", 0)}

@api_router.post("/auth/emergent-session")
async def emergent_session(request: Request, response: Response):
    body = await request.json()
    session_id = body.get("session_id")
    if not session_id:
        raise HTTPException(400, "session_id ausente")
    async with httpx.AsyncClient() as hc:
        r = await hc.get("https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data",
                         headers={"X-Session-ID": session_id})
    if r.status_code != 200:
        raise HTTPException(401, "Falha ao autenticar via Google")
    data = r.json()
    email = _norm_email(data["email"])
    existing = await db.users.find_one({"email": email}, {"_id": 0})
    if existing:
        # vincula por e-mail verificado pelo Google; papel NUNCA é alterado neste fluxo
        user_id = existing["user_id"]
        await db.users.update_one({"user_id": user_id},
                                  {"$set": {"name": data["name"], "picture": data.get("picture")}})
        role = existing.get("role", "customer")
    else:
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        # apenas o e-mail do proprietário (verificado pelo Google) recebe role owner
        role = "owner" if email == OWNER_EMAIL.lower() else "customer"
        await db.users.insert_one({
            "user_id": user_id, "email": email, "name": data["name"], "picture": data.get("picture"),
            "role": role, "password_version": 1, "mfa_enabled": False,
            "marketing_opt_in": False, "created_at": now_utc().isoformat()})
    expires_at = now_utc() + timedelta(days=7)
    user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if role in ADMIN_ROLES and not user.get("mfa_enabled"):
        return {"mfa_setup_required": True, "mfa_token": create_mfa_token(user_id, "mfa_setup")}
    if role in ADMIN_ROLES and user.get("mfa_enabled"):
        return {"mfa_required": True, "mfa_token": create_mfa_token(user_id, "mfa_verify")}
    await db.user_sessions.insert_one({
        "user_id": user_id, "session_token": data["session_token"],
        "pv": user.get("password_version", 0),
        "expires_at": expires_at.isoformat(), "created_at": now_utc().isoformat()})
    response.set_cookie("session_token", data["session_token"], httponly=True, secure=True,
                        samesite="none", path="/", max_age=7*24*3600)
    return {"user_id": user_id, "email": email, "name": data["name"], "role": role, "picture": data.get("picture")}

@api_router.post("/auth/logout")
async def logout(request: Request, response: Response):
    session_token = request.cookies.get("session_token")
    if session_token:
        await db.user_sessions.delete_one({"session_token": session_token})
    response.delete_cookie("session_token", path="/")
    return {"ok": True}

# ------------------- CONTA / LGPD -------------------
@api_router.patch("/account/profile")
async def update_profile(payload: ProfileIn, user=Depends(require_user)):
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    if update:
        await db.users.update_one({"user_id": user["user_id"]}, {"$set": update})
    return {"ok": True}

class ClubUpdateIn(BaseModel):
    join: bool = False
    cpf: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[ClubAddressIn] = None
    optin_email: Optional[bool] = None
    optin_whatsapp: Optional[bool] = None
    accepted_terms: bool = False
    accepted_privacy: bool = False

@api_router.get("/account/club")
async def get_club(user=Depends(require_user)):
    club = user.get("club") or {}
    return {
        "club_member": bool(user.get("club_member")),
        "cpf": club.get("cpf"), "phone": club.get("phone"), "address": club.get("address"),
        "optin_email": user.get("optin_email", user.get("marketing_opt_in", False)),
        "optin_whatsapp": user.get("optin_whatsapp", False),
        "terms_version": user.get("club_terms_version"),
        "accepted_at": user.get("club_accepted_at"),
        "current_terms_version": TERMS_VERSION,
        "points_balance": user.get("points_balance", 0),
    }

@api_router.patch("/account/club")
async def update_club(payload: ClubUpdateIn, user=Depends(require_user)):
    """Adesão e atualização do Clube. CPF/telefone/endereço sempre opcionais;
    opt-ins de e-mail e WhatsApp são separados e desmarcados por padrão."""
    rate_limit(f"club:{user['user_id']}", limit=15, window_s=600)
    if payload.join and user.get("club_member"):
        raise HTTPException(400, "Você já faz parte do Clube Casa da Barrica Wines.")
    update = {}
    if payload.join and not user.get("club_member"):
        if not payload.accepted_terms or not payload.accepted_privacy:
            raise HTTPException(400, "Para aderir ao Clube é preciso aceitar os Termos de Adesão e a Política de Privacidade")
        update["club_member"] = True
        update["club_terms_version"] = TERMS_VERSION
        update["club_accepted_at"] = now_utc().isoformat()
    club = dict(user.get("club") or {})
    if payload.cpf is not None:
        digits = "".join(ch for ch in payload.cpf if ch.isdigit())
        if digits and not _valid_cpf(digits):
            raise HTTPException(400, "CPF inválido")
        if digits and await _cpf_em_uso(digits, exclude_user_id=user["user_id"]):
            raise HTTPException(400, MSG_CADASTRO_DUPLICADO)
        club["cpf"] = digits or None
    if payload.phone is not None:
        club["phone"] = payload.phone.strip() or None
    if payload.address is not None:
        addr = {k: (v.strip() if isinstance(v, str) else v)
                for k, v in payload.address.model_dump().items() if v}
        if addr.get("cep"):
            d = "".join(ch for ch in addr["cep"] if ch.isdigit())
            if len(d) != 8:
                raise HTTPException(400, "CEP inválido (8 dígitos)")
            addr["cep"] = d
        club["address"] = addr or None
    if club != (user.get("club") or {}):
        update["club"] = club
    if payload.optin_email is not None:
        update["optin_email"] = payload.optin_email
        update["marketing_opt_in"] = payload.optin_email  # compat legado
    if payload.optin_whatsapp is not None:
        update["optin_whatsapp"] = payload.optin_whatsapp
    if update:
        try:
            await db.users.update_one({"user_id": user["user_id"]}, {"$set": update})
        except DuplicateKeyError:
            raise HTTPException(400, MSG_CADASTRO_DUPLICADO)
    return {"ok": True, "club_member": update.get("club_member", bool(user.get("club_member")))}

# ------------------- CARRINHO DA CONTA (merge pós-login) -------------------
class CartItemStored(BaseModel):
    wine_id: str = Field(max_length=40)
    variant_id: str = Field(max_length=40)
    qty: int = Field(ge=1, le=99)
    name: Optional[str] = Field(default=None, max_length=120)
    variant_label: Optional[str] = Field(default=None, max_length=120)
    price: Optional[float] = Field(default=None, ge=0)
    image: Optional[str] = Field(default=None, max_length=300)

@api_router.get("/account/cart")
async def get_account_cart(user=Depends(require_user)):
    return {"items": user.get("cart", [])}

@api_router.put("/account/cart")
async def put_account_cart(payload: dict, user=Depends(require_user)):
    """Carrinho persistido na conta — combinado com o carrinho local no login.
    Preços/estoque NUNCA são confiáveis aqui: o checkout recalcula tudo no servidor."""
    items = (payload or {}).get("items") or []
    if len(items) > 50:
        raise HTTPException(400, "Carrinho excede o limite de itens")
    try:
        clean = [CartItemStored(**it).model_dump(exclude_none=True) for it in items]
    except Exception:
        raise HTTPException(400, "Itens do carrinho inválidos")
    await db.users.update_one({"user_id": user["user_id"]},
                              {"$set": {"cart": clean, "cart_updated_at": now_utc().isoformat()}})
    return {"ok": True}

@api_router.get("/account/points")
async def my_points(user=Depends(require_user)):
    entries = await db.points_ledger.find({"user_id": user["user_id"]}, {"_id": 0}).sort("created_at", -1).to_list(200)
    cfg = _points_cfg(await get_settings())
    return {"balance": user.get("points_balance", 0), "history": entries,
            "earn_enabled": cfg["earn_enabled"], "reais_per_point": cfg["reais_per_point"],
            "redeem_enabled": cfg["redeem_enabled"], "point_value_brl": cfg["point_value_brl"]}

@api_router.get("/account/export")
async def export_data(user=Depends(require_user)):
    """LGPD — acesso aos dados pessoais."""
    orders = await db.orders.find({"user_id": user["user_id"]}, {"_id": 0}).to_list(500)
    favs = await db.favorites.find({"user_id": user["user_id"]}, {"_id": 0}).to_list(500)
    return {
        "perfil": {k: user.get(k) for k in ("user_id", "email", "name", "birth_date", "role",
                                             "marketing_opt_in", "wine_preferences", "created_at")},
        "pedidos": orders,
        "favoritos": favs,
        "clube": {"membro": bool(user.get("club_member")), "dados": user.get("club"),
                  "termos_versao": user.get("club_terms_version"), "aceito_em": user.get("club_accepted_at"),
                  "optin_email": user.get("optin_email", False), "optin_whatsapp": user.get("optin_whatsapp", False)},
        "pontos": {"saldo": user.get("points_balance", 0),
                   "historico": await db.points_ledger.find({"user_id": user["user_id"]}, {"_id": 0}).to_list(500)},
        "nota_retencao": "Pedidos são mantidos por obrigações fiscais mesmo após exclusão da conta, com dados pessoais anonimizados.",
    }

@api_router.post("/account/delete")
async def delete_account(request: Request, response: Response, user=Depends(require_user)):
    """LGPD — exclusão/anonimização. Pedidos preservados anonimizados (obrigação fiscal)."""
    uid = user["user_id"]
    # anonimização real: nada que identifique o titular permanece nos pedidos
    await db.orders.update_many({"user_id": uid}, {"$set": {
        "user_email": "anonimizado@removido.local", "anonymized": True,
        "address": "[removido]", "birth_date_attested": None},
        "$unset": {"mp_status_detail": ""}})
    await db.orders.update_many({"user_id": uid}, [{"$set": {"cep": {"$concat": [{"$substrCP": ["$cep", 0, 3]}, "*****"]}}}])
    await db.favorites.delete_many({"user_id": uid})
    await db.user_sessions.delete_many({"user_id": uid})
    await db.points_ledger.delete_many({"user_id": uid})
    await db.users.update_one({"user_id": uid}, {"$set": {
        "email": f"removido_{uid}@removido.local", "name": "Usuário removido",
        "password_hash": None, "deleted": True, "deleted_at": now_utc().isoformat(),
        "club_member": False, "points_balance": 0, "optin_email": False,
        "optin_whatsapp": False, "marketing_opt_in": False},
        "$unset": {"club": "", "club_terms_version": "", "club_accepted_at": ""}})
    await audit(user, "account_deleted", {})
    response.delete_cookie("session_token", path="/")
    return {"ok": True}

# ------------------- WINES -------------------
@api_router.get("/wines")
async def list_wines(q: Optional[str] = None, type: Optional[str] = None, country: Optional[str] = None,
                     min_price: Optional[float] = None, max_price: Optional[float] = None,
                     featured: Optional[bool] = None, sort: Optional[str] = None):
    filt = {"archived": {"$ne": True}}
    if q:
        filt["$or"] = [
            {"name": {"$regex": q, "$options": "i"}},
            {"winery": {"$regex": q, "$options": "i"}},
            {"country": {"$regex": q, "$options": "i"}},
            {"region": {"$regex": q, "$options": "i"}},
            {"grapes": {"$regex": q, "$options": "i"}},
            {"pairing_notes": {"$regex": q, "$options": "i"}},
        ]
    if type: filt["type"] = type
    if country: filt["country"] = country
    price_f = {}
    if min_price is not None: price_f["$gte"] = min_price
    if max_price is not None: price_f["$lte"] = max_price
    if price_f: filt["price"] = price_f
    if featured is not None: filt["featured"] = featured
    # campo 'cost' jamais sai do servidor para não-admins
    cursor = db.wines.find(filt, {"_id": 0, "cost": 0, "variants.cost": 0})
    if sort == "price_asc": cursor = cursor.sort("price", 1)
    elif sort == "price_desc": cursor = cursor.sort("price", -1)
    elif sort == "newest": cursor = cursor.sort("created_at", -1)
    return await cursor.to_list(500)

@api_router.get("/wines/{wine_id}")
async def get_wine(wine_id: str):
    wine = await db.wines.find_one({"wine_id": wine_id}, {"_id": 0, "cost": 0, "variants.cost": 0})
    if not wine:
        raise HTTPException(404, "Vinho não encontrado")
    return wine

@api_router.post("/admin/wines")
async def create_wine(payload: WineIn, admin=Depends(require_admin)):
    wine_id = f"wine_{uuid.uuid4().hex[:10]}"
    doc = payload.model_dump()
    doc["wine_id"] = wine_id
    if not doc["variants"]:
        doc["variants"] = [{**VariantIn(price=0).model_dump(), "variant_id": "default", "reserved": 0}]
    for v in doc["variants"]:
        v["variant_id"] = v.get("variant_id") or f"var_{uuid.uuid4().hex[:8]}"
        v["reserved"] = 0
        v["sku"] = v.get("sku") or f"SKU-{uuid.uuid4().hex[:6].upper()}"
    doc["created_at"] = now_utc().isoformat()
    await db.wines.insert_one(doc)
    await sync_display_fields(wine_id)
    await audit(admin, "wine_created", {"wine_id": wine_id, "name": doc["name"]})
    return {"wine_id": wine_id}

@api_router.put("/admin/wines/{wine_id}")
async def update_wine(wine_id: str, payload: WineIn, admin=Depends(require_admin)):
    doc = payload.model_dump()
    existing = await db.wines.find_one({"wine_id": wine_id}, {"_id": 0})
    if not existing:
        raise HTTPException(404, "Vinho não encontrado")
    old_variants = {v["variant_id"]: v for v in (existing.get("variants") or [])}
    for v in doc["variants"]:
        v["variant_id"] = v.get("variant_id") or f"var_{uuid.uuid4().hex[:8]}"
        prev = old_variants.get(v["variant_id"], {})
        v["reserved"] = prev.get("reserved", 0)  # reservas ativas preservadas
        v["sku"] = v.get("sku") or prev.get("sku") or f"SKU-{uuid.uuid4().hex[:6].upper()}"
    await db.wines.update_one({"wine_id": wine_id}, {"$set": doc})
    await sync_display_fields(wine_id)
    await audit(admin, "wine_updated", {"wine_id": wine_id})
    return {"ok": True}

@api_router.delete("/admin/wines/{wine_id}")
async def archive_wine(wine_id: str, admin=Depends(require_admin)):
    """Arquivamento preserva histórico de pedidos antigos."""
    await db.wines.update_one({"wine_id": wine_id}, {"$set": {"archived": True}})
    await audit(admin, "wine_archived", {"wine_id": wine_id})
    return {"ok": True}

@api_router.get("/admin/wines-full")
async def list_wines_admin(admin=Depends(require_admin)):
    return await db.wines.find({}, {"_id": 0}).to_list(1000)

@api_router.post("/admin/stock/entry")
async def stock_entry(payload: StockEntryIn, admin=Depends(require_admin)):
    """Entrada de mercadoria com fornecedor, custo e responsável."""
    wine, v = await find_variant(payload.wine_id, payload.variant_id)
    await db.wines.update_one({"wine_id": payload.wine_id},
        {"$inc": {"variants.$[v].stock": payload.qty}},
        array_filters=[{"v.variant_id": v["variant_id"]}])
    await log_movement(payload.wine_id, v["variant_id"], "entrada", payload.qty, payload.reason, admin,
                       {"supplier": payload.supplier, "unit_cost": payload.unit_cost})
    await sync_display_fields(payload.wine_id)
    await audit(admin, "stock_entry", {"wine_id": payload.wine_id, "qty": payload.qty})
    return {"ok": True}

@api_router.post("/admin/stock/adjust")
async def stock_adjust(payload: StockEntryIn, admin=Depends(require_admin)):
    """Ajuste de estoque com motivo obrigatório (qty pode ser negativo)."""
    wine, v = await find_variant(payload.wine_id, payload.variant_id)
    if v.get("stock", 0) + payload.qty < v.get("reserved", 0):
        raise HTTPException(400, "Ajuste deixaria estoque menor que reservas ativas")
    await db.wines.update_one({"wine_id": payload.wine_id},
        {"$inc": {"variants.$[v].stock": payload.qty}},
        array_filters=[{"v.variant_id": v["variant_id"]}])
    await log_movement(payload.wine_id, v["variant_id"], "ajuste", payload.qty, payload.reason, admin)
    await sync_display_fields(payload.wine_id)
    await audit(admin, "stock_adjust", {"wine_id": payload.wine_id, "qty": payload.qty})
    return {"ok": True}

@api_router.get("/admin/stock/movements")
async def stock_movements(admin=Depends(require_admin)):
    return await db.stock_movements.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)

# ------------------- CUPONS -------------------
@api_router.get("/admin/coupons")
async def list_coupons(admin=Depends(require_admin)):
    return await db.coupons.find({}, {"_id": 0}).to_list(200)

@api_router.post("/admin/coupons")
async def upsert_coupon(payload: CouponIn, admin=Depends(require_admin)):
    code = payload.code.upper().strip()
    doc = payload.model_dump()
    doc["code"] = code
    doc["uses_count"] = (await db.coupons.find_one({"code": code}, {"_id": 0}) or {}).get("uses_count", 0)
    await db.coupons.update_one({"code": code}, {"$set": doc}, upsert=True)
    await audit(admin, "coupon_saved", {"code": code})
    return {"ok": True}

@api_router.delete("/admin/coupons/{code}")
async def delete_coupon(code: str, admin=Depends(require_admin)):
    await db.coupons.delete_one({"code": code.upper()})
    return {"ok": True}

async def resolve_coupon(code: Optional[str]) -> tuple[float, Optional[dict]]:
    """Validação 100% server-side: existência, validade, limite de uso."""
    if not code:
        return 0.0, None
    c = await db.coupons.find_one({"code": code.upper().strip(), "active": True}, {"_id": 0})
    if not c:
        raise HTTPException(400, "Cupom inválido ou expirado")
    now = now_utc()
    if c.get("valid_from") and datetime.fromisoformat(c["valid_from"]).replace(tzinfo=timezone.utc) > now:
        raise HTTPException(400, "Cupom ainda não está válido")
    if c.get("valid_until") and datetime.fromisoformat(c["valid_until"]).replace(tzinfo=timezone.utc) < now:
        raise HTTPException(400, "Cupom expirado")
    if c.get("max_uses") is not None and c.get("uses_count", 0) >= c["max_uses"]:
        raise HTTPException(400, "Cupom esgotado")
    return c["percent"] / 100.0, c

# ------------------- FRETE (tabela própria administrável) -------------------
async def shipping_quote(cep: str) -> dict:
    """Tabela PRÓPRIA da loja, administrável no painel. Não é cotação de
    transportadora. Só valem regiões CONFIRMADAS pelo proprietário; CEPs sem
    região confirmada são bloqueados."""
    cep_num = "".join(ch for ch in cep if ch.isdigit())
    if len(cep_num) != 8:
        raise HTTPException(400, "CEP inválido (8 dígitos)")
    zones = await db.shipping_zones.find({"active": True, "confirmed": True}, {"_id": 0}).to_list(100)
    for z in zones:
        if any(cep_num.startswith(p.strip()) for p in z.get("cep_prefixes", [])):
            return {"price": z["price"], "deadline": z.get("deadline"), "zone": z["name"]}
    raise HTTPException(400, "Ainda não entregamos nesta região. Consulte as regiões atendidas ou fale conosco.")

@api_router.get("/shipping/zones")
async def list_shipping_zones_public():
    """Regiões atendidas e confirmadas (público, sem custos internos)."""
    zones = await db.shipping_zones.find({"active": True, "confirmed": True}, {"_id": 0}).to_list(100)
    return [{"name": z["name"], "price": z["price"], "deadline": z.get("deadline"),
             "cep_prefixes": z.get("cep_prefixes", [])} for z in zones]

@api_router.get("/admin/shipping")
async def admin_list_shipping(admin=Depends(require_admin)):
    return await db.shipping_zones.find({}, {"_id": 0}).to_list(100)

@api_router.post("/admin/shipping")
async def admin_upsert_shipping(payload: ShippingZoneIn, admin=Depends(require_admin)):
    doc = payload.model_dump()
    doc["zone_id"] = doc.get("zone_id") or f"zone_{uuid.uuid4().hex[:8]}"
    doc["cep_prefixes"] = [p.strip() for p in doc["cep_prefixes"] if p.strip()]
    if not doc["cep_prefixes"]:
        raise HTTPException(400, "Informe ao menos um prefixo de CEP")
    # defesa em profundidade: confirmação de região é ato exclusivo do proprietário
    existing = await db.shipping_zones.find_one({"zone_id": doc["zone_id"]}, {"_id": 0})
    if existing is None:
        doc["confirmed"] = False
    elif doc["confirmed"] != existing.get("confirmed", False):
        if admin.get("role") != "owner":
            raise HTTPException(403, "Somente o proprietário confirma regiões de entrega")
    await db.shipping_zones.update_one({"zone_id": doc["zone_id"]}, {"$set": doc}, upsert=True)
    await audit(admin, "shipping_zone_saved", {"zone_id": doc["zone_id"], "name": doc["name"], "confirmed": doc["confirmed"]})
    return {"ok": True, "zone_id": doc["zone_id"]}

@api_router.delete("/admin/shipping/{zone_id}")
async def admin_delete_shipping(zone_id: str, admin=Depends(require_admin)):
    await db.shipping_zones.delete_one({"zone_id": zone_id})
    await audit(admin, "shipping_zone_deleted", {"zone_id": zone_id})
    return {"ok": True}

# ------------------- CHECKOUT -------------------
async def _points_quote(user: Optional[dict], redeem_points: Optional[int], base: float) -> tuple[int, float]:
    """Valida o resgate para a cotação. Retorna (pontos_aplicáveis, desconto_R$).
    O débito efetivo (reserva) acontece só no checkout, de forma atômica."""
    if not redeem_points:
        return 0, 0.0
    cfg = _points_cfg(await get_settings())
    if not cfg["redeem_enabled"]:
        raise HTTPException(400, "O resgate de pontos está desativado no momento")
    if not user:
        raise HTTPException(401, "Entre na sua conta para usar pontos")
    if not user.get("club_member"):
        raise HTTPException(400, "O resgate de pontos é exclusivo para membros do Clube")
    if cfg["point_value_brl"] <= 0:
        raise HTTPException(400, "Resgate indisponível: valor do ponto não configurado pela loja")
    pts = min(int(redeem_points), int(user.get("points_balance", 0)))
    if pts <= 0:
        raise HTTPException(400, "Saldo de pontos insuficiente")
    cap = max(0.0, base) * cfg["max_redeem_percent"] / 100.0
    if pts * cfg["point_value_brl"] > cap:
        pts = int(cap // cfg["point_value_brl"])
    if pts <= 0:
        raise HTTPException(400, "Resgate acima do limite permitido para este pedido")
    return pts, round(pts * cfg["point_value_brl"], 2)

async def _build_quote(payload: CheckoutIn, user: Optional[dict] = None) -> dict:
    validate_age(payload.birth_date)
    subtotal = 0.0
    lines = []
    for it in payload.items:
        wine, v = await find_variant(it.wine_id, it.variant_id)
        price = v.get("discount_price") or v["price"]
        line_total = price * it.qty
        subtotal += line_total
        lines.append({"wine_id": wine["wine_id"], "variant_id": v["variant_id"],
                      "name": wine["name"], "vintage": v.get("vintage"), "volume_ml": v.get("volume_ml"),
                      "qty": it.qty, "unit_price": price, "total": round(line_total, 2), "image": wine.get("image")})
    shipping = await shipping_quote(payload.cep)
    pct, _coupon = await resolve_coupon(payload.coupon)
    discount = subtotal * pct
    pts_used, pts_discount = await _points_quote(user, payload.redeem_points, subtotal - discount)
    total = max(0.0, subtotal + shipping["price"] - discount - pts_discount)
    return {"subtotal": round(subtotal, 2), "shipping": shipping["price"], "discount": round(discount, 2),
            "points_used": pts_used, "points_discount": round(pts_discount, 2),
            "total": round(total, 2), "lines": lines,
            "shipping_mode": "tabela_propria",
            "shipping_zone": shipping["zone"], "shipping_deadline": shipping.get("deadline"),
            "shipping_notice": "Frete calculado pela tabela própria da loja para a região do CEP informado."}

@api_router.post("/checkout/quote")
async def checkout_quote(payload: CheckoutIn, request: Request):
    return await _build_quote(payload, await get_current_user(request))

async def _is_homologation_checkout(settings: dict, user: dict, items) -> bool:
    """Exceção restrita de homologação: libera checkout com vendas suspensas SOMENTE
    quando o modo é mercadopago_test (nunca demo nem produção), a conta autenticada
    está marcada pelo proprietário (flag no banco, re-lida a cada requisição) E todos
    os itens são produtos marcados como teste. Nada enviado pelo navegador autoriza."""
    if settings.get("payment_mode") != "mercadopago_test":
        return False
    fresh = await db.users.find_one({"user_id": user["user_id"]}, {"_id": 0, "homologation_buyer": 1})
    if not fresh or not fresh.get("homologation_buyer"):
        return False
    for it in items:
        wine = await db.wines.find_one({"wine_id": it.wine_id}, {"_id": 0, "homologation": 1})
        if not wine or not wine.get("homologation"):
            return False
    return True

@api_router.post("/checkout")
async def checkout(payload: CheckoutIn, request: Request):
    user = await require_user(request)
    validate_age(payload.birth_date)
    quote = await _build_quote(payload, user)
    settings = await get_settings()
    if settings.get("sales_status", "open") == "suspended" and not await _is_homologation_checkout(settings, user, payload.items):
        raise HTTPException(403, "Vendas temporariamente suspensas. Pedidos já pagos seguem em processamento normal. Tente novamente em breve.")
    payment_mode = settings.get("payment_mode", "demo")
    if payment_mode != "demo" and not mp_configured():
        raise HTTPException(503, "Pagamento não configurado. Credenciais do Mercado Pago ausentes no servidor.")

    order_id = f"ord_{uuid.uuid4().hex[:12]}"
    # Reserva atômica com rollback
    reserved = []
    try:
        for it in payload.items:
            _wine, v = await find_variant(it.wine_id, it.variant_id)
            ok = await reserve_stock(it.wine_id, v["variant_id"], it.qty)
            if not ok:
                raise HTTPException(409, "Estoque esgotado durante o checkout. Tente novamente.")
            reserved.append((it.wine_id, v["variant_id"], it.qty))
            await log_movement(it.wine_id, v["variant_id"], "reserva", it.qty, f"Pedido {order_id}", user)
    except HTTPException:
        for wid, vid, q in reserved:
            await release_stock(wid, vid, q)
        raise

    # Resgate de pontos: débito ATÔMICO do saldo (protege contra uso simultâneo do
    # mesmo saldo em dois checkouts). Liberação/cancelamento devolve os pontos.
    points_info = None
    if quote.get("points_used"):
        r = await db.users.update_one(
            {"user_id": user["user_id"], "points_balance": {"$gte": quote["points_used"]}},
            {"$inc": {"points_balance": -quote["points_used"]}})
        if r.modified_count == 0:
            for wid, vid, q in reserved:
                await release_stock(wid, vid, q)
            raise HTTPException(409, "Saldo de pontos insuficiente — os pontos podem ter sido usados em outro pedido")
        points_info = {"redeemed": quote["points_used"], "discount": quote["points_discount"], "status": "reserved"}
        await db.points_ledger.insert_one({
            "entry_id": f"pts_{uuid.uuid4().hex[:12]}", "user_id": user["user_id"], "order_id": order_id,
            "type": "redeem_reserve", "points": -quote["points_used"], "amount_brl": quote["points_discount"],
            "note": "Pontos reservados no checkout", "actor": user["email"], "created_at": now_utc().isoformat()})

    doc = {
        "order_id": order_id, "user_id": user["user_id"], "user_email": user["email"],
        "items": [it.model_dump() for it in payload.items], "quote": quote,
        "points": points_info,
        "cep": payload.cep, "address": payload.address,
        "birth_date_attested": payload.birth_date,
        "payment_method": payload.payment_method, "coupon": payload.coupon,
        "payment_status": "pending", "fulfillment_status": "aguardando_pagamento",
        "payment_mode": payment_mode,
        "demo": payment_mode == "demo",
        "reservation_expires_at": (now_utc() + timedelta(minutes=RESERVATION_TTL_MIN)).isoformat(),
        "reservation_active": True,
        "created_at": now_utc().isoformat(),
    }
    try:
        await db.orders.insert_one(doc)
    except Exception:
        for wid, vid, q in reserved:
            await release_stock(wid, vid, q)
        if points_info:
            await _release_point_reservation(order_id, user["user_id"], points_info["redeemed"],
                                             "Falha ao registrar pedido")
        raise HTTPException(500, "Erro ao registrar pedido")
    for wid, vid, q in reserved:
        await sync_display_fields(wid)

    if payload.coupon:
        await db.coupons.update_one({"code": payload.coupon.upper().strip()}, {"$inc": {"uses_count": 1}})

    await _track_server("checkout_start", order_id=order_id, total=quote["total"],
                        demo=(payment_mode == "demo"))

    asyncio.create_task(send_order_received(user["email"], user["name"], order_id,
                                            f"R$ {quote['total']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")))

    if payment_mode == "demo":
        return {"order_id": order_id, "quote": quote, "payment_status": "pending", "mode": "demo",
                "note": "Pagamento em modo demonstração — nenhuma cobrança real será feita."}
    # Mercado Pago (teste/live)
    try:
        pref = await create_preference(order_id, quote["lines"], user["email"], quote["total"])
    except Exception:
        raise HTTPException(502, "Falha ao criar preferência de pagamento no Mercado Pago")
    await db.orders.update_one({"order_id": order_id}, {"$set": {
        "mp_preference_id": pref["id"],
        "mp_collector_id": pref.get("collector_id"),  # conta recebedora esperada
    }})
    return {"order_id": order_id, "quote": quote, "payment_status": "pending",
            "mode": payment_mode, "checkout_url": pref["init_point"]}

@api_router.post("/orders/{order_id}/confirm-demo")
async def confirm_demo(order_id: str, request: Request):
    user = await require_user(request)
    order = await db.orders.find_one({"order_id": order_id, "user_id": user["user_id"]}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Pedido não encontrado")
    settings = await get_settings()
    if settings.get("payment_mode") != "demo":
        raise HTTPException(400, "Loja não está em modo demonstração")
    await _mark_paid(order, actor=user)
    return {"ok": True, "payment_status": "approved"}

async def _mark_paid(order: dict, actor: Optional[dict] = None, via: str = "demo"):
    """Confirmação idempotente. O status do PROVEDOR é autoritativo: se o pagamento
    chegar após expiração/cancelamento da reserva, o pedido é marcado como pago e
    sinalizado para revisão de estoque (envio bloqueado até resolução manual)."""
    r = await db.orders.update_one(
        {"order_id": order["order_id"], "payment_status": {"$nin": ["approved", "refunded"]}},
        {"$set": {"payment_status": "approved",
                  "paid_at": now_utc().isoformat(), "paid_via": via}})
    if r.modified_count == 0:
        return  # já pago/reembolsado — idempotência contra webhook repetido ou fora de ordem
    if order.get("reservation_active"):
        await db.orders.update_one({"order_id": order["order_id"]},
            {"$set": {"fulfillment_status": "preparando", "reservation_active": False}})
        for it in order["items"]:
            _wine, v = await find_variant(it["wine_id"], it.get("variant_id"))
            ok = await convert_reservation(it["wine_id"], v["variant_id"], it["qty"])
            if ok:
                await log_movement(it["wine_id"], v["variant_id"], "venda", it["qty"], f"Pedido {order['order_id']}", actor)
            else:
                await db.orders.update_one({"order_id": order["order_id"]},
                    {"$set": {"needs_stock_review": True, "fulfillment_status": "revisao_estoque"}})
                await log_movement(it["wine_id"], v["variant_id"], "revisao_estoque", it["qty"],
                                   f"Reserva inconsistente ao confirmar — pedido {order['order_id']}", actor)
            await sync_display_fields(it["wine_id"])
    else:
        # reserva já expirada/liberada: pago, mas envio bloqueado até decisão do admin
        await db.orders.update_one({"order_id": order["order_id"]},
            {"$set": {"needs_stock_review": True, "fulfillment_status": "revisao_estoque"}})
        for it in order["items"]:
            await log_movement(it["wine_id"], it.get("variant_id") or "default", "revisao_estoque", it["qty"],
                               f"Pagamento aprovado após expiração da reserva — pedido {order['order_id']}", actor)
    await _finalize_points(order, actor)
    await _award_points(order, _points_cfg(await get_settings()))
    await _track_server("purchase", order_id=order["order_id"], total=order["quote"]["total"],
                        demo=bool(order.get("demo")))
    asyncio.create_task(send_payment_approved(order["user_email"], "cliente", order["order_id"],
                        f"R$ {order['quote']['total']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")))

async def _release_order(order: dict, new_status: str, reason: str, actor: Optional[dict] = None):
    """Cancelamento/expiração: libera reserva e registra movimento. Idempotente."""
    r = await db.orders.update_one(
        {"order_id": order["order_id"], "reservation_active": True,
         "payment_status": {"$in": ["pending", "in_process"]}},
        {"$set": {"payment_status": new_status, "reservation_active": False,
                  "fulfillment_status": "cancelado"}})
    if r.modified_count == 0:
        return
    for it in order["items"]:
        _wine, v = await find_variant(it["wine_id"], it.get("variant_id"))
        await release_stock(it["wine_id"], v["variant_id"], it["qty"])
        await log_movement(it["wine_id"], v["variant_id"], "liberacao", it["qty"], reason, actor)
        await sync_display_fields(it["wine_id"])
    pts = order.get("points") or {}
    if pts.get("status") == "reserved":
        await _release_point_reservation(order["order_id"], order["user_id"], pts["redeemed"], reason)

@api_router.get("/orders")
async def my_orders(request: Request):
    user = await require_user(request)
    return await db.orders.find({"user_id": user["user_id"]}, {"_id": 0}).sort("created_at", -1).to_list(200)

# ------------------- PONTOS (Clube Casa da Barrica) -------------------
# Regras duras: SOMENTE compras reais de produção geram saldo — pedidos demo e de
# homologação (mercadopago_test) nunca pontuam. Base = produtos após descontos
# (cupom e pontos resgatados), SEM frete, arredondada para baixo. Todo movimento
# é registrado na coleção points_ledger (auditável) e o saldo nunca é alterado
# sem uma entrada correspondente no ledger.

async def _release_point_reservation(order_id: str, user_id: str, points: int, reason: str):
    """Devolve pontos reservados quando o pedido não se concretiza (idempotente
    via status do pedido — chamadores verificam points.status == 'reserved')."""
    await db.users.update_one({"user_id": user_id}, {"$inc": {"points_balance": points}})
    await db.orders.update_one({"order_id": order_id}, {"$set": {"points.status": "released"}})
    await db.points_ledger.insert_one({
        "entry_id": f"pts_{uuid.uuid4().hex[:12]}", "user_id": user_id, "order_id": order_id,
        "type": "redeem_release", "points": points, "amount_brl": None,
        "note": f"Reserva de pontos liberada — {reason}", "actor": "sistema",
        "created_at": now_utc().isoformat()})

async def _finalize_points(order: dict, actor: Optional[dict] = None):
    """Pagamento confirmado: a reserva de pontos vira resgate definitivo."""
    pts = order.get("points") or {}
    if pts.get("status") != "reserved":
        return
    await db.orders.update_one({"order_id": order["order_id"]}, {"$set": {"points.status": "used"}})
    await db.points_ledger.insert_one({
        "entry_id": f"pts_{uuid.uuid4().hex[:12]}", "user_id": order["user_id"],
        "order_id": order["order_id"], "type": "redeem", "points": -pts["redeemed"],
        "amount_brl": pts.get("discount"), "note": "Resgate de pontos confirmado no pagamento",
        "actor": (actor or {}).get("email", "sistema"), "created_at": now_utc().isoformat()})

async def _award_points(order: dict, cfg: dict):
    """Crédito de pontos por compra REAL aprovada. Idempotente por pedido."""
    if order.get("demo") or order.get("payment_mode") != "mercadopago_live":
        return  # demo e homologação/teste nunca geram saldo comercial
    if not cfg.get("earn_enabled") or cfg.get("reais_per_point", 0) <= 0:
        return
    q = order["quote"]
    base = max(0.0, q.get("subtotal", 0.0) - q.get("discount", 0.0) - q.get("points_discount", 0.0))
    pts = int(base // cfg["reais_per_point"])
    if pts <= 0:
        return
    if await db.points_ledger.find_one({"order_id": order["order_id"], "type": "earn"}):
        return  # já creditado (webhook duplicado/fora de ordem)
    await db.points_ledger.insert_one({
        "entry_id": f"pts_{uuid.uuid4().hex[:12]}", "user_id": order["user_id"],
        "order_id": order["order_id"], "type": "earn", "points": pts,
        "amount_brl": round(base, 2),
        "note": f"Compra aprovada — base R$ {base:.2f} (produtos, sem frete)",
        "actor": "sistema", "created_at": now_utc().isoformat()})
    await db.users.update_one({"user_id": order["user_id"]}, {"$inc": {"points_balance": pts}})

async def _settle_points_refund(order: dict, refund_amount: float):
    """Reembolso confirmado: revoga o crédito proporcional ao valor estornado e
    devolve os pontos resgatados. Idempotente — calcula o que já foi liquidado
    consultando o ledger. Reembolso parcial trata proporcionalmente (floor)."""
    total = order["quote"]["total"] or 1.0
    ratio = min(1.0, refund_amount / total)
    uid, oid = order["user_id"], order["order_id"]
    earn = await db.points_ledger.find_one({"order_id": oid, "type": "earn"})
    if earn:
        target = earn["points"] if ratio >= 1 else int(earn["points"] * ratio)
        revoked = 0
        async for e in db.points_ledger.find({"order_id": oid, "type": "refund_revoke"}):
            revoked += -e["points"]
        revoke_now = target - revoked
        if revoke_now > 0:
            await db.points_ledger.insert_one({
                "entry_id": f"pts_{uuid.uuid4().hex[:12]}", "user_id": uid, "order_id": oid,
                "type": "refund_revoke", "points": -revoke_now, "amount_brl": refund_amount,
                "note": f"Estorno de pontos por reembolso ({'total' if ratio >= 1 else 'parcial'})",
                "actor": "sistema", "created_at": now_utc().isoformat()})
            await db.users.update_one({"user_id": uid}, {"$inc": {"points_balance": -revoke_now}})
            u = await db.users.find_one({"user_id": uid}, {"_id": 0, "points_balance": 1})
            if u and u.get("points_balance", 0) < 0:
                # cliente já resgatou parte dos pontos — saldo não fica negativo; a diferença fica auditada
                deficit = -u["points_balance"]
                await db.users.update_one({"user_id": uid}, {"$set": {"points_balance": 0}})
                await db.points_ledger.insert_one({
                    "entry_id": f"pts_{uuid.uuid4().hex[:12]}", "user_id": uid, "order_id": oid,
                    "type": "adjust", "points": 0, "amount_brl": None,
                    "note": f"Saldo insuficiente no estorno — {deficit} pts não debitados (já resgatados)",
                    "actor": "sistema", "created_at": now_utc().isoformat()})
    pts = order.get("points") or {}
    if pts.get("status") == "used":
        target_r = pts["redeemed"] if ratio >= 1 else int(pts["redeemed"] * ratio)
        restored = 0
        async for e in db.points_ledger.find({"order_id": oid, "type": "redeem_restore"}):
            restored += e["points"]
        restore_now = target_r - restored
        if restore_now > 0:
            await db.points_ledger.insert_one({
                "entry_id": f"pts_{uuid.uuid4().hex[:12]}", "user_id": uid, "order_id": oid,
                "type": "redeem_restore", "points": restore_now, "amount_brl": refund_amount,
                "note": "Pontos resgatados devolvidos por reembolso",
                "actor": "sistema", "created_at": now_utc().isoformat()})
            await db.users.update_one({"user_id": uid}, {"$inc": {"points_balance": restore_now}})
        if ratio >= 1:
            await db.orders.update_one({"order_id": oid}, {"$set": {"points.status": "restored"}})

# ------------------- WEBHOOK MERCADO PAGO -------------------
# CAPTURA TEMPORÁRIA (ticket MP WCS-50562): materiais brutos das notificações
# dos 2 pagamentos de homologação, gravados apenas no banco de diagnóstico para
# análise offline (nunca em chat). Auto-expira em 6h. Validação NÃO é alterada.
_MP_DIAG_IDS = {"178102235185", "178106554507"}
_MP_DIAG_UNTIL_TS = datetime.now(timezone.utc).timestamp() + 6 * 3600


@api_router.post("/webhooks/mercadopago")
async def mp_webhook(request: Request):
    _q = request.query_params
    _pid = _q.get("data.id") or _q.get("id")
    if _pid in _MP_DIAG_IDS and datetime.now(timezone.utc).timestamp() < _MP_DIAG_UNTIL_TS:
        await db.webhook_diag.insert_one({
            "at": now_utc(),
            "raw_query": str(request.url.query),
            "x_signature": request.headers.get("x-signature", ""),
            "x_request_id": request.headers.get("x-request-id", ""),
            "user_agent": request.headers.get("user-agent", ""),
        })
    if not valid_webhook_signature(request.headers, request.query_params):
        raise HTTPException(401, "Assinatura inválida")
    payload = await request.json()
    ptype = payload.get("type")
    if ptype != "payment":
        # eventos de assinatura: nunca confiar no corpo — dispara a reconciliação
        # por consulta (mesma rotina do cron), que valida tudo no provedor
        if ptype in ("subscription_preapproval", "subscription_authorized_payment"):
            from subscriptions import _reconcile_subscriptions
            asyncio.create_task(_reconcile_subscriptions(f"webhook_{uuid.uuid4().hex[:8]}"))
        return {"ok": True}
    payment_id = str(payload.get("data", {}).get("id", ""))
    if not payment_id:
        return {"ok": True}
    p = await get_payment(payment_id)
    if not p:
        raise HTTPException(404, "Pagamento não encontrado no provedor")
    order_id = p.get("external_reference")
    if not order_id:
        return {"ok": True}
    order = await db.orders.find_one({"order_id": order_id}, {"_id": 0})
    if not order:
        return {"ok": True}
    # credencial do ambiente REGISTRADO no pedido (nunca inferida de prefixo/live_mode)
    tok = _token_for(order)
    if tok and tok != _token_current_default():
        p2 = await get_payment(payment_id, token=tok)
        if p2:
            p = p2
    result = await _process_mp_payment(order, p, via="webhook")
    return {"ok": True, "result": result}

# ------------------- RECONCILIAÇÃO DE PAGAMENTOS (consulta à API MP) -------------------
# Rotina ÚNICA de processamento (_process_mp_payment): webhook, tarefa recorrente e
# consulta manual do proprietário passam pelas MESMAS verificações. Nunca se confia
# na página de retorno, no navegador ou na solicitação do cliente como prova.

async def _flag_payment_review(order: dict, pid: str, via: str, reason: str) -> str:
    """Divergência/ambiguidade → revisão manual. NUNCA confirma nem duplica a venda."""
    await db.orders.update_one({"order_id": order["order_id"]}, {"$set": {
        "payment_review": {"needed": True, "reason": reason, "payment_id": pid,
                           "via": via, "at": now_utc().isoformat()}}})
    logger.warning("Revisão de pagamento — pedido %s: %s (via %s, pagamento %s)",
                   order["order_id"], reason, via, pid)
    return f"revisao:{reason}"

async def _process_mp_payment(order: dict, p: dict, via: str) -> str:
    """Verifica recebedor, referência, preferência (quando disponível), valor e moeda
    no PROVEDOR antes de confirmar. Idempotente: todas as transições usam guards
    atômicos — webhook, reconciliação e consulta manual podem ocorrer ao mesmo tempo
    sem confirmar duas vezes, baixar estoque duas vezes ou creditar pontos repetidos."""
    oid = order["order_id"]
    pid = str(p.get("id", ""))
    status = p.get("status")
    if p.get("external_reference") != oid:
        return await _flag_payment_review(order, pid, via, "referência externa não corresponde ao pedido")
    if order.get("mp_preference_id") and p.get("preference_id") and p["preference_id"] != order["mp_preference_id"]:
        return await _flag_payment_review(order, pid, via, "preferência divergente")
    if p.get("currency_id") != "BRL":
        return await _flag_payment_review(order, pid, via, f"moeda divergente ({p.get('currency_id')})")
    expected = round(order["quote"]["total"], 2)
    got = p.get("transaction_amount")
    if got is None or abs(float(got) - expected) > 0.01:
        return await _flag_payment_review(order, pid, via, f"valor divergente (esperado {expected}, provedor {got})")
    if order.get("mp_collector_id"):
        if str(p.get("collector_id")) != str(order["mp_collector_id"]):
            return await _flag_payment_review(order, pid, via, "conta recebedora divergente")
    elif p.get("collector_id"):
        # pedidos antigos sem registro: fixa a conta recebedora na primeira consulta verificada
        await db.orders.update_one({"order_id": oid}, {"$set": {"mp_collector_id": p["collector_id"]}})
    if status == "approved" and order.get("payment_status") == "approved" \
            and pid != str(order.get("mp_payment_id")):
        return await _flag_payment_review(order, pid, via, "segundo pagamento aprovado para o mesmo pedido")
    if order.get("mp_payment_id") == pid and order.get("mp_status") == status:
        return "duplicado"  # mesma notificação/consulta repetida
    await db.orders.update_one({"order_id": oid}, {"$set": {
        "mp_payment_id": pid, "mp_status": status, "mp_status_detail": p.get("status_detail")}})
    if status == "approved":
        await _mark_paid(order, via=via)  # guard atômico interno: só transiciona uma vez
        return "aprovado"
    if status in ("rejected", "cancelled"):
        if order.get("payment_status") in ("pending", "in_process"):
            await _release_order(order, "rejected" if status == "rejected" else "cancelled",
                                 f"Mercado Pago: {status}")
            asyncio.create_task(send_order_cancelled(order["user_email"], "cliente", oid, status))
        return status  # fora de ordem após aprovação: estado confirmado NÃO é rebaixado
    if status == "refunded":
        if order.get("payment_status") != "refunded":
            await db.orders.update_one({"order_id": oid}, {"$set": {
                "payment_status": "refunded", "refund.status": "confirmed",
                "refund.confirmed_at": now_utc().isoformat(),
                "refund.provider_status": status, "refund.provider_payment_id": pid}})
            await _settle_points_refund(order, order["quote"]["total"])
            asyncio.create_task(send_order_cancelled(order["user_email"], "cliente", oid,
                                                     "reembolso confirmado pelo Mercado Pago"))
        return "reembolsado"
    return f"pendente:{status}"  # pending/in_process etc.: só registra o estado atual

def _token_current_default() -> str:
    return os.environ.get("MP_ACCESS_TOKEN", "") or ""

async def _reconcile_order(order: dict, via: str) -> str:
    """Consulta UM pedido no provedor, com a credencial do ambiente registrado no
    pedido. 'sem_pagamento'/'nao_encontrado' são resultados, NÃO erros; falhas de
    comunicação nunca são tratadas como recusa/cancelamento."""
    token = _token_for(order)
    if not token:
        return "erro:sem_credencial"
    try:
        if order.get("mp_payment_id"):
            p = await get_payment(order["mp_payment_id"], token=token)
            if p is None:
                return "nao_encontrado"
        else:
            results = await search_payments(order["order_id"], token=token)
            if not results:
                return "sem_pagamento"
            approved = [r for r in results if r.get("status") == "approved"]
            if len(approved) > 1:
                return await _flag_payment_review(
                    order, ",".join(str(r.get("id")) for r in approved), via,
                    "múltiplos pagamentos aprovados para o mesmo pedido")
            p = approved[0] if approved else max(
                results, key=lambda r: str(r.get("date_last_updated") or r.get("id")))
        return await _process_mp_payment(order, p, via)
    except httpx.TimeoutException:
        return "erro:timeout"
    except httpx.HTTPStatusError as e:
        return f"erro:http_{e.response.status_code}"  # inclui 429 — nova tentativa pelo backoff
    except Exception as e:
        logger.exception("reconciliação falhou para o pedido %s", order["order_id"])
        return f"erro:{type(e).__name__}"

def _next_check_delay_min(order: dict) -> int:
    """Backoff progressivo por pedido: 2→4→8→16→32→64 min; pedidos com +24h: 6h."""
    attempts = order.get("payment_check_attempts", 0)
    try:
        age_h = (now_utc() - datetime.fromisoformat(
            str(order.get("created_at", "")).replace("Z", "+00:00"))).total_seconds() / 3600
    except Exception:
        age_h = 0
    if age_h > 24:
        return 360
    return min(2 * (2 ** min(attempts, 5)), 64)

_RECONCILE_LOCK = asyncio.Lock()

async def _reconcile_payments(run_id: str):
    """Tarefa recorrente: consulta pedidos com pagamento em aberto (inclui reservas
    EXPIRADAS, cujo pagamento ainda pode ser confirmado). Roda sem o agente e sem IA."""
    if _RECONCILE_LOCK.locked():
        await db.cron_runs.update_one({"run_id": run_id}, {"$set": {
            "run_id": run_id, "job": "reconcile-payments", "status": "skipped_lock",
            "at": now_utc().isoformat()}}, upsert=True)
        return
    async with _RECONCILE_LOCK:
        stats = {"checked": 0, "confirmed": 0, "review": 0, "errors": 0, "not_found": 0}
        try:
            now_iso = now_utc().isoformat()
            due = await db.orders.find({
                "payment_status": {"$in": ["pending", "in_process", "expired"]},
                "payment_mode": {"$in": ["mercadopago_test", "mercadopago_live"]},
                "$or": [{"payment_next_check_at": {"$lte": now_iso}},
                        {"payment_next_check_at": {"$exists": False}}],
            }, {"_id": 0}).sort("created_at", 1).to_list(25)
            for order in due:
                result = await _reconcile_order(order, "reconciliacao")
                stats["checked"] += 1
                if result == "aprovado":
                    stats["confirmed"] += 1
                elif result.startswith("revisao"):
                    stats["review"] += 1
                elif result.startswith("erro"):
                    stats["errors"] += 1
                elif result in ("sem_pagamento", "nao_encontrado"):
                    stats["not_found"] += 1
                await db.orders.update_one({"order_id": order["order_id"]}, {
                    "$set": {"payment_check": {"at": now_utc().isoformat(), "result": result,
                                               "via": "reconciliacao"},
                             "payment_next_check_at": (now_utc() + timedelta(
                                 minutes=_next_check_delay_min(order))).isoformat()},
                    "$inc": {"payment_check_attempts": 1}})
                if result == "erro:http_429":
                    logger.warning("reconcile-payments: 429 do provedor — interrompendo este ciclo")
                    break
            await db.cron_runs.update_one({"run_id": run_id}, {"$set": {
                "run_id": run_id, "job": "reconcile-payments", "status": "ok",
                "stats": stats, "at": now_utc().isoformat()}}, upsert=True)
        except Exception as e:
            logger.exception("cron reconcile-payments falhou")
            await db.cron_runs.update_one({"run_id": run_id}, {"$set": {
                "run_id": run_id, "job": "reconcile-payments", "status": "error",
                "error": type(e).__name__, "at": now_utc().isoformat()}}, upsert=True)

@api_router.post("/cron/reconcile-payments")
async def cron_reconcile_payments(request: Request):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    auth = request.headers.get("Authorization", "")
    token = auth.split(" ", 1)[1] if auth.startswith("Bearer ") else ""
    if not hmac_mod.compare_digest(token, WEBHOOK_CRON_SECRET):
        raise HTTPException(401, "Não autorizado")
    run_id = request.headers.get("X-Webhook-Id", f"manual_{uuid.uuid4().hex[:8]}")
    asyncio.create_task(_reconcile_payments(run_id))
    return {"ok": True}

@api_router.post("/admin/orders/{order_id}/consult-payment")
async def admin_consult_payment(order_id: str, request: Request, admin=Depends(require_admin)):
    """Consulta manual do proprietário — MESMA rotina segura da reconciliação.
    Nunca marca 'pago' arbitrariamente; apenas reflete o estado do provedor."""
    if admin.get("role") != "owner":
        raise HTTPException(403, "Somente o proprietário consulta pagamentos no provedor")
    rate_limit(f"consult:{order_id}", limit=4, window_s=60)
    order = await db.orders.find_one({"order_id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Pedido não encontrado")
    if order.get("payment_mode") not in ("mercadopago_test", "mercadopago_live"):
        raise HTTPException(400, "Pedido não é do Mercado Pago (modo demonstração)")
    result = await _reconcile_order(order, "consulta_manual")
    await db.orders.update_one({"order_id": order_id}, {
        "$set": {"payment_check": {"at": now_utc().isoformat(), "result": result, "via": "consulta_manual"}},
        "$inc": {"payment_check_attempts": 1}})
    await audit(admin, "payment_manual_consult", {"order_id": order_id, "result": result})
    fresh = await db.orders.find_one({"order_id": order_id}, {"_id": 0})
    return {"ok": True, "result": result, "payment_status": fresh.get("payment_status"),
            "paid_via": fresh.get("paid_via"), "payment_check": fresh.get("payment_check"),
            "payment_review": fresh.get("payment_review"),
            "needs_stock_review": bool(fresh.get("needs_stock_review"))}

@api_router.get("/admin/payment-reconciliation/status")
async def admin_reconcile_status(admin=Depends(require_admin)):
    """Última execução e falhas da tarefa — sem dados sensíveis."""
    last = await db.cron_runs.find({"job": "reconcile-payments"}, {"_id": 0}).sort("at", -1).to_list(1)
    due = await db.orders.count_documents({
        "payment_status": {"$in": ["pending", "in_process", "expired"]},
        "payment_mode": {"$in": ["mercadopago_test", "mercadopago_live"]}})
    review = await db.orders.count_documents({"payment_review.needed": True})
    return {"last_run": last[0] if last else None,
            "orders_in_watch": due, "orders_in_review": review}

# ------------------- ANALYTICS (privacy-first) -------------------
# Sem IP, sem fingerprint, sem cookies persistentes: a "sessão" é um id aleatório
# gerado no navegador (sessionStorage — morre com a aba) e gravado como hash.
# Compras, checkouts e cadastros são registrados pelo SERVIDOR (nunca por eventos
# livres do navegador). Acessos de administradores e de quem sinaliza Do Not
# Track / Global Privacy Control são descartados. Retenção: 180 dias (TTL).

class TrackIn(BaseModel):
    type: Literal["pageview", "add_to_cart"]
    path: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=8, max_length=64)
    wine_id: Optional[str] = Field(default=None, max_length=40)

async def _track_server(event_type: str, **extra):
    """Eventos confiáveis gerados pelo servidor (signup, checkout_start, purchase)."""
    try:
        now = now_utc()
        await db.analytics_events.insert_one({
            "event_id": f"evt_{uuid.uuid4().hex[:12]}", "type": event_type, "source": "server",
            "created_at": now.isoformat(), "created_dt": now, **extra})
    except Exception:
        logger.warning("analytics: falha ao registrar evento %s", event_type)

@api_router.post("/analytics/track")
async def analytics_track(payload: TrackIn, request: Request):
    if request.headers.get("dnt") == "1" or request.headers.get("sec-gpc") == "1":
        return {"ok": True, "stored": False, "reason": "privacy_preference"}
    user = await get_current_user(request)
    if user and user.get("role") in ADMIN_ROLES:
        return {"ok": True, "stored": False, "reason": "admin_excluded"}
    rate_limit(f"track:{payload.session_id}", limit=120, window_s=600)
    if not payload.path.startswith("/") or payload.path.startswith("//"):
        raise HTTPException(400, "Caminho inválido")
    now = now_utc()
    await db.analytics_events.insert_one({
        "event_id": f"evt_{uuid.uuid4().hex[:12]}", "type": payload.type,
        "path": payload.path, "wine_id": payload.wine_id,
        "session": hashlib.sha256(payload.session_id.encode()).hexdigest()[:32],
        "source": "browser", "created_at": now.isoformat(), "created_dt": now})
    return {"ok": True, "stored": True}

@api_router.get("/admin/analytics")
async def admin_analytics(days: int = 30, admin=Depends(require_admin)):
    """Funil: visita → carrinho → checkout → compra (confirmada no servidor).
    Cadastros são indicador SEPARADO — não são etapa obrigatória do funil."""
    days = min(max(int(days), 1), 365)
    since = (now_utc() - timedelta(days=days)).isoformat()
    events = await db.analytics_events.find({"created_at": {"$gte": since}}, {"_id": 0}).to_list(200000)
    pageviews = [e for e in events if e["type"] == "pageview"]
    adds = len([e for e in events if e["type"] == "add_to_cart"])
    checkout_starts = len([e for e in events if e["type"] == "checkout_start"])
    purchases_all = [e for e in events if e["type"] == "purchase"]
    purchases_real = [e for e in purchases_all if not e.get("demo")]
    signups = len([e for e in events if e["type"] == "signup"])
    sessions = {e["session"] for e in events if e.get("session")}
    from collections import Counter
    top_pages = Counter(e["path"] for e in pageviews).most_common(10)
    wine_views = Counter(e["wine_id"] for e in pageviews if e.get("wine_id")).most_common(10)
    wine_ids = [w for w, _ in wine_views]
    names = {w["wine_id"]: w["name"] async for w in db.wines.find(
        {"wine_id": {"$in": wine_ids}}, {"_id": 0, "wine_id": 1, "name": 1})}
    visits = len(sessions) or len(pageviews)
    def rate(a, b):
        return round(100.0 * a / b, 1) if b else 0.0
    return {
        "days": days,
        "pageviews": len(pageviews),
        "unique_sessions": len(sessions),
        "add_to_cart": adds,
        "checkout_starts": checkout_starts,
        "purchases": len(purchases_real),
        "purchases_demo": len(purchases_all) - len(purchases_real),
        "revenue_real": round(sum(e.get("total", 0) for e in purchases_real), 2),
        "signups": signups,
        "funnel": {
            "visita_para_carrinho": rate(adds, visits),
            "carrinho_para_checkout": rate(checkout_starts, adds),
            "checkout_para_compra": rate(len(purchases_real), checkout_starts),
            "visita_para_compra": rate(len(purchases_real), visits),
        },
        "top_pages": [{"path": p, "views": v} for p, v in top_pages],
        "top_wines": [{"wine_id": w, "name": names.get(w, w), "views": v} for w, v in wine_views],
        "retention_days": ANALYTICS_RETENTION_DAYS,
        "privacy_note": "Sem IP, fingerprint ou cookies persistentes; sessões são ids aleatórios por aba (gravados como hash). "
                        "Acessos de administradores são excluídos e preferências de privacidade (Do Not Track / GPC) são respeitadas. "
                        "Compras, checkouts e cadastros são registrados pelo servidor, nunca pelo navegador.",
    }

# ------------------- CRON -------------------
@api_router.post("/cron/release-reservations")
async def cron_release(request: Request):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    auth = request.headers.get("Authorization", "")
    token = auth.split(" ", 1)[1] if auth.startswith("Bearer ") else ""
    if not hmac_mod.compare_digest(token, WEBHOOK_CRON_SECRET):
        raise HTTPException(401, "Não autorizado")
    run_id = request.headers.get("X-Webhook-Id", f"manual_{uuid.uuid4().hex[:8]}")
    asyncio.create_task(_release_expired_logged(run_id))
    return {"ok": True}

async def _release_expired_logged(run_id: str):
    try:
        before = await db.orders.count_documents({"reservation_active": True, "payment_status": {"$in": ["pending", "in_process"]}, "reservation_expires_at": {"$lt": now_utc().isoformat()}})
        await _release_expired()
        await db.cron_runs.update_one({"run_id": run_id}, {"$set": {"run_id": run_id, "job": "release-reservations", "released": before, "status": "ok", "at": now_utc().isoformat()}}, upsert=True)
    except Exception as e:
        logger.exception("cron release-reservations falhou")
        await db.cron_runs.update_one({"run_id": run_id}, {"$set": {"run_id": run_id, "job": "release-reservations", "status": "error", "error": type(e).__name__, "at": now_utc().isoformat()}}, upsert=True)

@api_router.post("/cron/backup")
async def cron_backup(request: Request):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    auth = request.headers.get("Authorization", "")
    token = auth.split(" ", 1)[1] if auth.startswith("Bearer ") else ""
    if not hmac_mod.compare_digest(token, WEBHOOK_CRON_SECRET):
        raise HTTPException(401, "Não autorizado")
    run_id = request.headers.get("X-Webhook-Id", f"manual_{uuid.uuid4().hex[:8]}")
    asyncio.create_task(_run_backup(run_id))
    return {"ok": True}

async def _run_backup(run_id: str):
    ts = now_utc().strftime("%Y%m%d_%H%M%S")
    path = f"/app/backups/adega_{ts}.gz"
    os.makedirs("/app/backups", exist_ok=True)
    try:
        proc = await asyncio.create_subprocess_exec(
            "mongodump", f"--uri={mongo_url}", f"--db={os.environ['DB_NAME']}",
            f"--archive={path}", "--gzip",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(err.decode()[:200])
        remote_path = f"{APP_NAME}/backups/adega_{ts}.gz"
        with open(path, "rb") as f:
            await put_object(remote_path, f.read(), "application/gzip")
        await db.backups.insert_one({"backup_id": f"bkp_{uuid.uuid4().hex[:10]}", "file": path,
                                     "storage_path": remote_path, "size_bytes": os.path.getsize(path),
                                     "status": "stored_external", "created_at": now_utc().isoformat()})
        await db.cron_runs.update_one({"run_id": run_id}, {"$set": {"run_id": run_id, "job": "backup", "status": "ok", "file": path, "storage_path": remote_path, "at": now_utc().isoformat()}}, upsert=True)
    except Exception as e:
        logger.exception("cron backup falhou")
        await db.cron_runs.update_one({"run_id": run_id}, {"$set": {"run_id": run_id, "job": "backup", "status": "error", "error": type(e).__name__, "at": now_utc().isoformat()}}, upsert=True)

@api_router.get("/admin/backups")
async def admin_list_backups(admin=Depends(require_admin)):
    return await db.backups.find({}, {"_id": 0}).sort("created_at", -1).to_list(50)

@api_router.post("/admin/backups/{backup_id}/verify-restore")
async def admin_verify_restore(backup_id: str, admin=Depends(require_admin)):
    """Baixa o backup do armazenamento EXTERNO e restaura em banco scratch (restore_verify),
    comprovando que o backup sobrevive fora do disco do pod. Não toca no banco de produção."""
    bkp = await db.backups.find_one({"backup_id": backup_id}, {"_id": 0})
    if not bkp:
        raise HTTPException(404, "Backup não encontrado")
    try:
        data = await get_object(bkp["storage_path"])
    except Exception:
        raise HTTPException(502, "Falha ao baixar do armazenamento externo")
    tmp = f"/tmp/restore_verify_{backup_id}.gz"
    with open(tmp, "wb") as f:
        f.write(data)
    src_db = os.environ['DB_NAME']
    proc = await asyncio.create_subprocess_exec(
        "mongorestore", f"--uri={mongo_url}", f"--archive={tmp}", "--gzip",
        f"--nsFrom={src_db}.*", "--nsTo=restore_verify.*",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(500, "Falha na restauração de verificação")
    counts = {}
    for coll in ("wines", "users", "orders"):
        counts[coll] = await db.client["restore_verify"][coll].count_documents({})
    await db.client["restore_verify"].command("dropDatabase")
    os.remove(tmp)
    await audit(admin, "backup_restore_verified", {"backup_id": backup_id, "counts": counts})
    return {"ok": True, "source": "external_storage", "restored_counts": counts,
            "note": "Restauração feita em banco temporário 'restore_verify' e descartada. Produção não foi alterada."}

@api_router.get("/admin/cron-runs")
async def admin_cron_runs(admin=Depends(require_admin)):
    """Monitoramento das tarefas automáticas (últimas 50 execuções)."""
    return await db.cron_runs.find({}, {"_id": 0}).sort("at", -1).to_list(50)

async def _release_expired_for_wine(wine_id: str):
    """Liberação preguiçosa por vinho: chamada antes de cada nova reserva."""
    expired = await db.orders.find({
        "reservation_active": True, "payment_status": {"$in": ["pending", "in_process"]},
        "reservation_expires_at": {"$lt": now_utc().isoformat()},
        "items.wine_id": wine_id}, {"_id": 0}).to_list(50)
    for o in expired:
        await _release_order(o, "expired", "Reserva expirada sem pagamento")

async def _release_expired():
    expired = await db.orders.find({
        "reservation_active": True, "payment_status": {"$in": ["pending", "in_process"]},
        "reservation_expires_at": {"$lt": now_utc().isoformat()}}, {"_id": 0}).to_list(200)
    for o in expired:
        await _release_order(o, "expired", "Reserva expirada sem pagamento")

# ------------------- COFRE DE CREDENCIAIS (formulário seguro do painel) -------------------
# Valores ficam SOMENTE no servidor (Mongo + memória de runtime). Nunca são
# retornados por nenhuma API, nunca aparecem em logs e nunca vão ao frontend.
class SecretsIn(BaseModel):
    mp_access_token: Optional[str] = None
    mp_webhook_secret: Optional[str] = None

async def load_secrets():
    try:
        doc = await db.app_secrets.find_one({"key": "payments"}, {"_id": 0})
        if doc:
            set_runtime_credentials(_dec(doc.get("mp_access_token")), _dec(doc.get("mp_webhook_secret")))
    except Exception as e:
        logger.warning("Falha ao carregar cofre de credenciais: %s", type(e).__name__)

@api_router.get("/admin/secrets/status")
async def secrets_status(admin=Depends(require_admin)):
    """Apenas flags — valores jamais são devolvidos."""
    doc = await db.app_secrets.find_one({"key": "payments"}, {"_id": 0}) or {}
    return {
        "mp_access_token_set": bool(os.environ.get("MP_ACCESS_TOKEN") or doc.get("mp_access_token")),
        "mp_webhook_secret_set": bool(os.environ.get("MP_WEBHOOK_SECRET") or doc.get("mp_webhook_secret")),
        "source": "env" if os.environ.get("MP_ACCESS_TOKEN") else ("formulario" if doc.get("mp_access_token") else "nenhum"),
    }

@api_router.post("/admin/secrets")
async def save_secrets(payload: SecretsIn, request: Request, admin=Depends(require_admin)):
    if admin.get("role") != "owner":
        raise HTTPException(403, "Somente o proprietário configura credenciais de pagamento")
    update = {}
    if payload.mp_access_token:
        t = payload.mp_access_token.strip()
        if len(t) < 20 or " " in t:
            raise HTTPException(400, "Token inválido")
        update["mp_access_token"] = _enc(t)
    if payload.mp_webhook_secret:
        s = payload.mp_webhook_secret.strip()
        if len(s) < 16 or " " in s:
            raise HTTPException(400, "Segredo de webhook inválido")
        update["mp_webhook_secret"] = _enc(s)
    if not update:
        raise HTTPException(400, "Nada para salvar")
    update["updated_by"] = admin["email"]
    update["updated_at"] = now_utc().isoformat()
    await db.app_secrets.update_one({"key": "payments"}, {"$set": update}, upsert=True)
    set_runtime_credentials(update.get("mp_access_token"), update.get("mp_webhook_secret"))
    await audit(admin, "payment_credentials_updated", {"fields": [k for k in update if k not in ("updated_by", "updated_at")]})
    # Sem teste automático: a verificação cria uma preferência no sandbox e deve
    # ser uma ação explícita (POST /admin/secrets/test-connection).
    return {"ok": True}

@api_router.post("/admin/secrets/test-connection")
async def secrets_test_connection(admin=Depends(require_admin)):
    """Ação EXPLÍCITA de homologação da credencial: cria uma preferência mínima
    de R$ 1 no sandbox para provar que a credencial é aceita pela API de
    Preferences. Não abre checkout, não cria pedido, não mexe em estoque."""
    if admin.get("role") != "owner":
        raise HTTPException(403, "Somente o proprietário")
    if not mp_configured():
        raise HTTPException(400, "Nenhuma credencial salva")
    ok, msg = await test_connection()
    await audit(admin, "payment_credential_tested", {"ok": ok})
    return {"credential_accepted_for_preferences": ok, "detail": msg,
            "payments_homologated": False,
            "note": "Preferência de homologação criada sem abrir checkout. A bateria de pagamentos (aprovado/recusado/webhooks) segue pendente."}

@api_router.post("/admin/secrets/resync")
async def resync_secrets(admin=Depends(require_admin)):
    """Regrava no cofre as credenciais atualmente em memória de runtime.
    Nenhum valor trafega na requisição ou resposta — apenas re-sincroniza
    memória -> banco (recuperação após limpeza indevida do cofre)."""
    if admin.get("role") != "owner":
        raise HTTPException(403, "Somente o proprietário")
    import payments_mp
    token, secret = payments_mp._RT_TOKEN, payments_mp._RT_SECRET
    if not token and not secret:
        raise HTTPException(404, "Nenhuma credencial em memória para sincronizar")
    update = {"updated_by": admin["email"], "updated_at": now_utc().isoformat()}
    if token: update["mp_access_token"] = _enc(token)
    if secret: update["mp_webhook_secret"] = _enc(secret)
    await db.app_secrets.update_one({"key": "payments"}, {"$set": update}, upsert=True)
    await audit(admin, "payment_credentials_resynced", {})
    return {"ok": True, "fields": [k for k in update if k not in ("updated_by", "updated_at")]}

# ------------------- ADMIN -------------------
@api_router.get("/admin/orders")
async def admin_orders(admin=Depends(require_admin)):
    return await db.orders.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)

@api_router.put("/admin/orders/{order_id}/status")
async def admin_update_order(order_id: str, body: dict, admin=Depends(require_admin)):
    order = await db.orders.find_one({"order_id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Pedido não encontrado")
    old_fulfill = order.get("fulfillment_status")
    update = {}
    if "fulfillment_status" in body: update["fulfillment_status"] = body["fulfillment_status"]
    if "payment_status" in body: update["payment_status"] = body["payment_status"]
    if update:
        await db.orders.update_one({"order_id": order_id}, {"$set": update})
        await audit(admin, "order_status_changed", {"order_id": order_id, **update})
        if update.get("fulfillment_status") == "enviado" and old_fulfill != "enviado":
            asyncio.create_task(send_order_shipped(order["user_email"], "cliente", order_id))
        if update.get("fulfillment_status") == "cancelado" and old_fulfill != "cancelado":
            asyncio.create_task(send_order_cancelled(order["user_email"], "cliente", order_id))
    return {"ok": True}

@api_router.post("/admin/orders/{order_id}/resolve-stock")
async def admin_resolve_stock(order_id: str, body: ResolveStockIn, admin=Depends(require_admin)):
    """Fluxo de resolução para pagamento aprovado após expiração da reserva.
    fulfill: tenta baixar estoque disponível (falha se insuficiente) e libera para preparo.
    refund: marca reembolso (estorno a executar no painel do provedor) e cancela envio."""
    order = await db.orders.find_one({"order_id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Pedido não encontrado")
    # Reembolso: idempotente e permitido para qualquer pedido pago (não só revisão de estoque)
    if body.action == "refund":
        existing_refund = (order.get("refund") or {}).get("status")
        if existing_refund in ("requested", "processing", "confirmed"):
            return {"ok": True, "resolution": "refund",
                    "note": f"Reembolso já está em andamento ({existing_refund}) — nenhuma nova operação criada."}
        if order.get("payment_status") != "approved":
            raise HTTPException(400, "Só é possível reembolsar pedidos pagos")
    elif not order.get("needs_stock_review"):
        raise HTTPException(400, "Pedido não está pendente de revisão de estoque")
    if body.action == "fulfill" and order.get("payment_status") != "approved":
        raise HTTPException(400, "Pedido não está pago")
    if body.action == "fulfill":
        # tenta baixar do estoque disponível (reserva já foi liberada)
        lowered = []
        try:
            for it in order["items"]:
                _w, v = await find_variant(it["wine_id"], it.get("variant_id"))
                ok = await reserve_stock(it["wine_id"], v["variant_id"], it["qty"])
                if not ok:
                    raise HTTPException(409, f"Estoque insuficiente para {it.get('wine_id')} — impossível enviar; prefira reembolso")
                lowered.append((it["wine_id"], v["variant_id"], it["qty"]))
            for wid, vid, q in lowered:
                await convert_reservation(wid, vid, q)
                await log_movement(wid, vid, "venda", q, f"Resolução manual — pedido {order_id}", admin)
                await sync_display_fields(wid)
        except HTTPException:
            for wid, vid, q in lowered:
                await release_stock(wid, vid, q)
            raise
        await db.orders.update_one({"order_id": order_id},
            {"$set": {"needs_stock_review": False, "fulfillment_status": "preparando",
                      "stock_resolution": {"action": "fulfill", "reason": body.reason,
                                           "by": admin["email"], "at": now_utc().isoformat()}}})
        await audit(admin, "stock_review_fulfilled", {"order_id": order_id})
        return {"ok": True, "resolution": "fulfill"}
    # refund: SOLICITA — nunca marca como reembolsado antes da confirmação do provedor.
    # Idempotente: repetição não cria novo estorno.
    existing_refund = (order.get("refund") or {}).get("status")
    if existing_refund in ("requested", "processing", "confirmed"):
        return {"ok": True, "resolution": "refund",
                "note": f"Reembolso já está em andamento ({existing_refund}) — nenhuma nova operação criada."}
    amount = body.amount or order["quote"]["total"]
    if amount > order["quote"]["total"]:
        raise HTTPException(400, "Valor do reembolso excede o total do pedido")
    refund_rec = {"status": "requested", "amount": amount, "partial": amount < order["quote"]["total"],
                  "reason": body.reason, "requested_by": admin["email"],
                  "requested_at": now_utc().isoformat(),
                  "provider_refund_id": None, "confirmed_at": None}
    await db.orders.update_one({"order_id": order_id},
        {"$set": {"needs_stock_review": False, "payment_status": "refund_requested",
                  "fulfillment_status": "cancelado", "refund": refund_rec,
                  "stock_resolution": {"action": "refund", "reason": body.reason,
                                       "by": admin["email"], "at": now_utc().isoformat()}}})
    await audit(admin, "refund_requested", {"order_id": order_id, "amount": amount, "reason": body.reason})
    note = "Reembolso solicitado. O estado final só muda após confirmação do provedor (ou confirmação manual no modo demo)."
    if order.get("mp_payment_id") and mp_configured():
        try:
            rr = await refund_payment(order["mp_payment_id"], amount if refund_rec["partial"] else None)
            await db.orders.update_one({"order_id": order_id},
                {"$set": {"refund.status": "processing", "refund.provider_refund_id": str(rr.get("id", ""))}})
            note = "Estorno enviado ao Mercado Pago. Aguardando confirmação do provedor."
        except Exception:
            await db.orders.update_one({"order_id": order_id}, {"$set": {"refund.status": "failed"}})
            note = "Falha ao solicitar estorno no provedor. Use 'Confirmar estorno' para tentar novamente."
    return {"ok": True, "resolution": "refund", "note": note}

@api_router.post("/admin/orders/{order_id}/refund/confirm")
async def admin_confirm_refund(order_id: str, admin=Depends(require_admin)):
    """Confirma o reembolso JUNTO AO PROVEDOR antes de marcar como reembolsado.
    No modo demo, a confirmação manual do admin substitui o provedor."""
    order = await db.orders.find_one({"order_id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Pedido não encontrado")
    if order.get("payment_status") != "refund_requested":
        raise HTTPException(400, "Pedido não tem reembolso pendente de confirmação")
    refund = order.get("refund") or {}
    if order.get("mp_payment_id") and mp_configured():
        # se a solicitação anterior falhou, tenta executar agora (idempotente via X-Idempotency-Key)
        if refund.get("status") in ("requested", "failed"):
            try:
                rr = await refund_payment(order["mp_payment_id"],
                                          refund.get("amount") if refund.get("partial") else None)
                refund["provider_refund_id"] = str(rr.get("id", ""))
            except Exception:
                raise HTTPException(502, "Falha na comunicação com o provedor. Tente novamente.")
        # confirma o estado real no provedor antes de finalizar
        p = await get_payment(order["mp_payment_id"])
        if not p or p.get("status") != "refunded":
            raise HTTPException(409, f"Provedor ainda não confirmou o estorno (status: {(p or {}).get('status')}). Tente novamente em instantes.")
    await db.orders.update_one({"order_id": order_id}, {"$set": {
        "payment_status": "refunded", "fulfillment_status": "cancelado",
        "refund.status": "confirmed", "refund.confirmed_at": now_utc().isoformat(),
        "refund.confirmed_by": admin["email"], "refund.provider_refund_id": refund.get("provider_refund_id")}})
    await _settle_points_refund(order, refund.get("amount") or order["quote"]["total"])
    await audit(admin, "refund_confirmed", {"order_id": order_id, "amount": refund.get("amount")})
    # só agora avisamos o cliente que o valor foi devolvido
    asyncio.create_task(send_order_cancelled(order["user_email"], "cliente", order_id,
                                             "reembolso confirmado — o estorno foi processado pelo meio de pagamento"))
    return {"ok": True, "payment_status": "refunded"}

@api_router.get("/admin/stats")
async def admin_stats(admin=Depends(require_admin)):
    orders = await db.orders.find({}, {"_id": 0}).to_list(2000)
    def summarize(rows):
        paid = [o for o in rows if o.get("payment_status") == "approved"]
        revenue = sum(o["quote"]["total"] for o in paid)
        cancelled = len([o for o in rows if o.get("payment_status") in ("cancelled", "rejected", "expired")])
        refunded = len([o for o in rows if o.get("payment_status") == "refunded"])
        return {"revenue": round(revenue, 2), "orders_paid": len(paid),
                "orders_cancelled": cancelled, "orders_refunded": refunded,
                "avg_ticket": round(revenue / len(paid), 2) if paid else 0,
                "orders_total": len(rows)}
    real = [o for o in orders if not o.get("demo")]
    demo = [o for o in orders if o.get("demo")]
    wines = await db.wines.find({"archived": {"$ne": True}}, {"_id": 0}).to_list(1000)
    low_stock = []
    for w in wines:
        for v in (w.get("variants") or [default_variant(w)]):
            avail = v.get("stock", 0) - v.get("reserved", 0)
            if avail < 5:
                low_stock.append({"name": w["name"], "vintage": v.get("vintage") or "sem safra",
                                  "available": avail})
    return {"real": summarize(real), "demo": summarize(demo),
            "wines_count": len(wines), "low_stock": low_stock[:20],
            "note": "Receita = soma dos pedidos com payment_status=approved (confirmados pelo provedor). "
                    "Cancelados/reembolsados não compõem a receita. Pedidos demo são separados."}

@api_router.get("/admin/audit")
async def admin_audit(admin=Depends(require_admin)):
    if admin.get("role") != "owner":
        raise HTTPException(403, "Auditoria restrita ao proprietário")
    return await db.audit_log.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)

@api_router.post("/admin/staff")
async def grant_staff(payload: StaffGrantIn, admin=Depends(require_admin)):
    """Apenas o proprietário concede/revoga acesso administrativo."""
    if admin.get("role") != "owner":
        raise HTTPException(403, "Somente o proprietário gerencia a equipe")
    target = await db.users.find_one({"email": _norm_email(payload.email)}, {"_id": 0})
    if not target:
        raise HTTPException(404, "Usuário não encontrado")
    if target["user_id"] == admin["user_id"]:
        raise HTTPException(400, "Você não pode alterar o próprio papel")
    await db.users.update_one({"user_id": target["user_id"]}, {"$set": {"role": payload.role}})
    await db.user_sessions.delete_many({"user_id": target["user_id"]})  # força novo login (MFA)
    await audit(admin, "role_granted", {"target": payload.email, "role": payload.role})
    return {"ok": True}

@api_router.get("/admin/users")
async def admin_users(admin=Depends(require_admin)):
    users = await db.users.find({}, {"_id": 0, "password_hash": 0, "mfa_secret": 0, "mfa_secret_pending": 0}).to_list(1000)
    return users

@api_router.get("/settings")
async def get_public_settings():
    s = await get_settings()
    subs = s.get("subscriptions") or {}
    return {"store_name": s.get("store_name", "Casa da Barrica Wines"),
            "tagline": s.get("tagline", "Curadoria, Histórias & Descobertas em cada vinho"),
            "home": s["home"], "footer": s["footer"], "points": s["points"], "chat": s["chat"],
            "payment_mode": s.get("payment_mode", "demo"),
            "sales_status": s.get("sales_status", "open"),
            "subscriptions": {"enabled": bool(subs.get("enabled", False)), "info": subs.get("info", "")},
            "email_configured": email_configured(), "mp_configured": mp_configured()}

@api_router.put("/admin/settings")
async def update_settings(payload: SettingsIn, admin=Depends(require_admin)):
    if admin.get("role") != "owner":
        raise HTTPException(403, "Somente o proprietário")
    if payload.payment_mode in ("mercadopago_test", "mercadopago_live") and not mp_configured():
        raise HTTPException(400, "Configure MP_ACCESS_TOKEN e MP_WEBHOOK_SECRET no servidor antes de ativar.")
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    current = await get_settings()
    if "home" in update:
        update["home"] = {**current["home"], **_validate_content(update["home"], DEFAULT_HOME_CONTENT, HOME_LINK_KEYS)}
    if "footer" in update:
        update["footer"] = {**current["footer"], **_validate_content(update["footer"], DEFAULT_FOOTER_CONTENT, set())}
    if "points" in update:
        update["points"] = {**current["points"], **_validate_points(update["points"], current["points"])}
    if "chat" in update:
        update["chat"] = {**current["chat"], **_validate_chat(update["chat"], current["chat"])}
    await db.store_settings.update_one({"key": "store"}, {"$set": update}, upsert=True)
    await audit(admin, "settings_updated", {"fields": list(update.keys())})
    return {"ok": True}

# ------------------- ATENDIMENTO (Sommelier Virtual) -------------------
# Somente leitura: o assistente NUNCA altera pedidos, preços, estoque, pontos ou
# pagamentos, e nunca solicita senhas, MFA ou dados de cartão. Pedidos entram no
# contexto APENAS do cliente autenticado (autorização verificada no servidor).
# Dados enviados ao modelo são mínimos: catálogo resumido, frete aprovado,
# regras públicas do Clube e os textos de atendimento do proprietário.

class ChatIn(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)
    message: str = Field(min_length=1, max_length=500)

async def _chat_context(s: dict, user: Optional[dict]) -> str:
    wines = await db.wines.find({"archived": {"$ne": True}},
                                {"_id": 0, "name": 1, "type": 1, "price": 1, "discount_price": 1, "stock": 1}).to_list(100)
    cat = "\n".join(
        f"- {w['name']} ({w.get('type', '?')}), R$ {w.get('discount_price') or w.get('price')}, "
        f"{'disponível' if w.get('stock', 0) > 0 else 'esgotado'}"
        for w in wines[:60])
    zones = await db.shipping_zones.find({"confirmed": True}, {"_id": 0, "name": 1, "price": 1, "deadline": 1}).to_list(20)
    frete = "\n".join(f"- {z['name']}: R$ {z['price']}, prazo {z.get('deadline') or 'a confirmar'}" for z in zones)
    pts = s["points"]
    lines = [
        f"LOJA: {s.get('store_name')} — {s.get('tagline')}",
        f"VENDAS: {'suspensas temporariamente (loja em demonstração; pedidos não são cobrados)' if s.get('sales_status') == 'suspended' else 'abertas'}",
        "CADASTRO: e-mail e senha com maioridade (18+); o Clube é opcional e gratuito (CPF/telefone/endereço opcionais).",
        "",
        "CATÁLOGO ATUAL (nome, tipo, preço, disponibilidade):",
        cat or "catálogo vazio no momento",
        "",
        "FRETE (tabela própria aprovada):",
        frete or "não configurado no momento",
        "",
        f"CLUBE/PONTOS: 1 ponto a cada R$ {pts['reais_per_point']} em produtos (frete não conta), creditados após confirmação de compras reais; "
        f"resgate {'ativo' if pts['redeem_enabled'] else 'em breve (a loja está definindo o valor do ponto)'}.",
        f"ATENDIMENTO: {s['chat']['atendimento_info']}",
    ]
    if s["chat"].get("faqs"):
        lines += ["", "PERGUNTAS FREQUENTES:", s["chat"]["faqs"]]
    if user:
        orders = await db.orders.find(
            {"user_id": user["user_id"]},
            {"_id": 0, "order_id": 1, "quote.total": 1, "payment_status": 1, "fulfillment_status": 1, "created_at": 1}
        ).sort("created_at", -1).to_list(5)
        if orders:
            ol = "\n".join(
                f"- {o['order_id']} | pagamento: {o.get('payment_status')} | entrega: {o.get('fulfillment_status')} "
                f"| R$ {(o.get('quote') or {}).get('total')} | {str(o.get('created_at'))[:10]}"
                for o in orders)
            lines += ["", f"PEDIDOS DO CLIENTE AUTENTICADO ({user.get('name')}):", ol]
        else:
            lines += ["", f"PEDIDOS DO CLIENTE AUTENTICADO ({user.get('name')}): nenhum pedido até agora."]
    return "\n".join(lines)

async def _chat_llm(cfg: dict, context: str, history: List[dict], message: str, session_id: str) -> str:
    from emergentintegrations.llm.chat import LlmChat, UserMessage, TextDelta, StreamDone
    sys_msg = (
        "Você é o Sommelier Virtual da Casa da Barrica Wines, um assistente automatizado de atendimento. "
        "Se perguntarem quem você é, responda: 'Sou o Sommelier Virtual da Casa da Barrica Wines, um assistente automatizado.' "
        "Responda em pt-BR, de forma cordial e CURTA (máximo 1200 caracteres), SOMENTE com base nas INFORMAÇÕES APROVADAS abaixo "
        "(catálogo, frete, clube/pontos e funcionamento da loja). "
        "NUNCA invente disponibilidade, descontos, prazos ou políticas fora dessas informações. "
        "Se a informação não estiver disponível, diga que não tem essa informação e ofereça o atendimento humano "
        "(mencione o botão 'Falar com uma pessoa'). "
        "Pedidos: você só tem acesso aos pedidos do cliente AUTENTICADO desta conversa; se a seção de pedidos não existir "
        "no contexto, peça para o cliente entrar na conta (menu Entrar) e tentar de novo. "
        "Você NÃO pode alterar pedidos, preços, estoque, pontos ou pagamentos — apenas informar. "
        "NUNCA solicite senhas, códigos de verificação/MFA, dados de cartão ou documentos. "
        "Para compras, oriente a usar o catálogo e o carrinho do site.\n\n"
        f"INFORMAÇÕES APROVADAS:\n{context}"
    )
    chat = LlmChat(api_key=EMERGENT_LLM_KEY, session_id=f"chat_{session_id}",
                   system_message=sys_msg).with_model("anthropic", "claude-sonnet-5")
    hist = "\n".join(f"{'Cliente' if m['role'] == 'user' else 'Sommelier Virtual'}: {m['content']}" for m in history[-8:])
    prompt = (f"CONVERSA ATÉ AQUI:\n{hist}\n\n" if hist else "") + f"Cliente: {message}"
    parts = []
    async for ev in chat.stream_message(UserMessage(text=prompt)):
        if isinstance(ev, TextDelta):
            parts.append(ev.content)
        elif isinstance(ev, StreamDone):
            break
    return "".join(parts)

@api_router.post("/chat")
async def chat_message(payload: ChatIn, request: Request):
    s = await get_settings()
    cfg = s["chat"]
    human = _wa_link(cfg["whatsapp_number"])
    if not cfg["enabled"]:
        return {"reply": "O Sommelier Virtual está temporariamente desativado. Você pode falar com uma pessoa agora mesmo pelo WhatsApp.",
                "human_link": human, "disabled": True}
    rate_limit(f"chat:{payload.session_id}", limit=30, window_s=600)
    rate_limit(f"chatip:{request.client.host}", limit=60, window_s=600)
    used = await db.chat_messages.count_documents({"session_id": payload.session_id, "role": "user"})
    if used >= cfg["max_messages_per_session"]:
        return {"reply": "Você atingiu o limite de mensagens desta conversa. Para continuar, fale com uma pessoa pelo WhatsApp.",
                "human_link": human, "limit_reached": True}
    user = await get_current_user(request)
    context = await _chat_context(s, user)
    history = await db.chat_messages.find(
        {"session_id": payload.session_id}, {"_id": 0, "role": 1, "content": 1}
    ).sort("created_at", -1).to_list(10)
    history.reverse()
    try:
        reply = (await _chat_llm(cfg, context, history, payload.message, payload.session_id)).strip()
    except Exception:
        logger.exception("Sommelier Virtual: chamada ao modelo falhou")
        return {"reply": "O Sommelier Virtual está indisponível no momento. Fale com uma pessoa pelo WhatsApp.",
                "human_link": human, "unavailable": True}
    if not reply:
        reply = "Não consegui responder agora. Fale com uma pessoa pelo WhatsApp."
    reply = reply[:1200]
    now = now_utc()
    base = {"session_id": payload.session_id, "user_id": user["user_id"] if user else None}
    await db.chat_messages.insert_many([
        {**base, "msg_id": f"msg_{uuid.uuid4().hex[:10]}", "role": "user", "content": payload.message,
         "created_at": now.isoformat(), "created_dt": now},
        {**base, "msg_id": f"msg_{uuid.uuid4().hex[:10]}", "role": "assistant", "content": reply,
         "created_at": now_utc().isoformat(), "created_dt": now_utc()},
    ])
    return {"reply": reply, "human_link": human}

# ------------------- PAIRING (IA) -------------------
def rule_based_pairing(dish: str, wines: List[dict], budget: Optional[float]) -> List[dict]:
    d = dish.lower()
    def score(w):
        s = 0
        t = w.get("type", "")
        pn = (w.get("pairing_notes") or "").lower()
        if any(k in d for k in ["carne", "picanha", "churrasco", "cordeiro", "bife", "costela"]):
            if t == "Tinto": s += 3
        if any(k in d for k in ["peixe", "camarão", "ostra", "salada", "frango"]):
            if t in ("Branco", "Espumante"): s += 3
        if any(k in d for k in ["queijo", "risoto"]): s += 2 if t in ("Tinto", "Branco") else 0
        if any(k in d for k in ["chocolate", "sobremesa", "doce"]):
            if t in ("Fortificado", "Sobremesa", "Espumante"): s += 4
        if any(word in pn for word in d.split()): s += 2
        if budget and (w.get("discount_price") or w.get("price", 0)) > budget: s -= 5
        return s
    ranked = sorted([w for w in wines if not w.get("archived") and w.get("stock", 0) > 0], key=score, reverse=True)
    return ranked[:3]

@api_router.post("/pairing")
async def pairing(payload: PairingRequest):
    """IA consulta APENAS dados públicos do catálogo em estoque. Nenhum dado pessoal
    ou financeiro do cliente é enviado ao modelo."""
    wines = await db.wines.find({"archived": {"$ne": True}, "stock": {"$gt": 0}},
                                {"_id": 0, "cost": 0, "variants.cost": 0}).to_list(500)
    if not wines:
        return {"recommendations": [], "explanation": "Sem vinhos disponíveis em estoque no momento.", "mode": "empty"}
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        catalog_ctx = "\n".join([
            f"- id:{w['wine_id']} | {w['name']} ({w.get('type')}, {w.get('country','?')}, "
            f"R$ {w.get('discount_price') or w.get('price')}) — {w.get('pairing_notes') or ''}"
            for w in wines[:50]
        ])
        sys_msg = (
            "Você é um sommelier brasileiro experiente. Recomende ATÉ 3 vinhos APENAS do catálogo fornecido, "
            "usando os IDs exatos. Retorne JSON estrito no formato: "
            "{\"picks\":[{\"wine_id\":\"...\",\"reason\":\"... explicação curta em pt-BR considerando intensidade, acidez, gordura, taninos e doçura...\"}], \"note\":\"resumo curto\"}. "
            "Se nenhum vinho combinar bem, retorne picks vazio e explique em 'note'. Nunca invente vinhos, histórias ou características fora do catálogo."
        )
        chat = LlmChat(api_key=EMERGENT_LLM_KEY, session_id=f"pair_{uuid.uuid4().hex[:8]}",
                       system_message=sys_msg).with_model("anthropic", "claude-sonnet-5")
        user_txt = (
            f"CATÁLOGO DISPONÍVEL:\n{catalog_ctx}\n\n"
            f"PRATO: {payload.dish}\nPREPARO/MOLHO: {payload.preparation or 'não informado'}\n"
            f"OCASIÃO: {payload.occasion or 'não informada'}\nPREFERÊNCIAS: {payload.preferences or 'nenhuma'}\n"
            f"ORÇAMENTO MÁX (R$): {payload.max_budget if payload.max_budget else 'sem limite'}\n\nResponda APENAS com o JSON."
        )
        resp = await chat.send_message(UserMessage(text=user_txt))
        text = resp if isinstance(resp, str) else str(resp)
        import re, json
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            data = json.loads(m.group(0))
            picks = data.get("picks", [])
            by_id = {w["wine_id"]: w for w in wines}
            recs = []
            for p in picks[:3]:
                wid = p.get("wine_id")
                if wid in by_id:
                    recs.append({"wine": by_id[wid], "reason": p.get("reason", "")})
            return {"recommendations": recs, "explanation": data.get("note", ""), "mode": "ai"}
    except Exception:
        logger.exception("IA de harmonização falhou; usando regras")
    picked = rule_based_pairing(payload.dish, wines, payload.max_budget)
    return {
        "recommendations": [{"wine": w, "reason": f"Combinação recomendada por regras baseadas no perfil do prato ({payload.dish})."} for w in picked],
        "explanation": "Recomendação por regras — Sommelier Virtual indisponível no momento.",
        "mode": "rules",
    }

# ------------------- FAVORITOS -------------------
@api_router.post("/favorites/{wine_id}")
async def add_fav(wine_id: str, request: Request):
    user = await require_user(request)
    await db.favorites.update_one(
        {"user_id": user["user_id"], "wine_id": wine_id},
        {"$set": {"user_id": user["user_id"], "wine_id": wine_id, "created_at": now_utc().isoformat()}},
        upsert=True)
    return {"ok": True}

@api_router.delete("/favorites/{wine_id}")
async def remove_fav(wine_id: str, request: Request):
    user = await require_user(request)
    await db.favorites.delete_one({"user_id": user["user_id"], "wine_id": wine_id})
    return {"ok": True}

@api_router.get("/favorites")
async def list_favs(request: Request):
    user = await require_user(request)
    favs = await db.favorites.find({"user_id": user["user_id"]}, {"_id": 0}).to_list(500)
    ids = [f["wine_id"] for f in favs]
    return await db.wines.find({"wine_id": {"$in": ids}}, {"_id": 0, "cost": 0, "variants.cost": 0}).to_list(500)

# ------------------- SEED -------------------
SEED_WINES = [
    {"name": "Château Margaux Grand Vin", "winery": "Château Margaux", "country": "França", "region": "Bordeaux, Médoc", "type": "Tinto", "grapes": ["Cabernet Sauvignon 90%", "Merlot 7%", "Cabernet Franc 2%"], "alcohol": "13.5%", "body": "Encorpado", "sweetness": "Seco", "featured": True, "badge": "Ícone Mundial", "image": "https://images.unsplash.com/photo-1642603437398-0a8e30fb93ae?crop=entropy&cs=srgb&fm=jpg&q=85&w=800", "pairing_notes": "Carré de cordeiro com ervas da Provença, carnes maturadas e queijo Comté curado.", "aromas": "Groselha preta, violetas, cedro e notas de trufas. Taninos sedosos e final eterno.", "service_temp": "16–18°C", "demo": True, "variants": [{"variant_id": "default", "vintage": "2018", "volume_ml": 750, "sku": "SKU-MARGAUX18", "price": 4290.00, "discount_price": 3890.00, "cost": 2900, "stock": 12, "reserved": 0}]},
    {"name": "Brunello di Montalcino Riserva", "winery": "Biondi-Santi", "country": "Itália", "region": "Toscana", "type": "Tinto", "grapes": ["Sangiovese Grosso 100%"], "alcohol": "14.5%", "body": "Muito Encorpado", "sweetness": "Seco", "featured": True, "badge": "Safra Histórica", "image": "https://images.unsplash.com/photo-1642603436412-11e2e2fc08bb?crop=entropy&cs=srgb&fm=jpg&q=85&w=800", "pairing_notes": "Bife à Fiorentina, risoto de funghi porcini e ossobuco alla milanese.", "aromas": "Cereja silvestre, couro nobre, tabaco e taberna italiana clássica.", "service_temp": "17–18°C", "demo": True, "variants": [{"variant_id": "default", "vintage": "2016", "volume_ml": 750, "sku": "SKU-BRUNELLO16", "price": 1850.00, "discount_price": 1690.00, "cost": 1200, "stock": 18, "reserved": 0}]},
    {"name": "AlmaViva Epu Assemblage", "winery": "Viña Almaviva", "country": "Chile", "region": "Valle del Maipo", "type": "Tinto", "grapes": ["Cabernet Sauvignon 78%", "Carmenère 15%", "Cabernet Franc 7%"], "alcohol": "14.8%", "body": "Encorpado", "sweetness": "Seco", "featured": True, "badge": "Mais Desejado", "image": "https://images.unsplash.com/photo-1724882207681-9e7e8c3dd45c?crop=entropy&cs=srgb&fm=jpg&q=85&w=800", "pairing_notes": "Picanha grelhada na brasa de angus, costela de fogo de chão e queijos azuis.", "aromas": "Cacau, amoras e especiarias doces. Opulência andina com elegância bordalesa.", "service_temp": "16–18°C", "demo": True, "variants": [{"variant_id": "default", "vintage": "2020", "volume_ml": 750, "sku": "SKU-ALMAVIVA20", "price": 680.00, "discount_price": 599.00, "cost": 420, "stock": 34, "reserved": 0}]},
    {"name": "Chablis Premier Cru Fourchaume", "winery": "Domaine Laroche", "country": "França", "region": "Borgonha", "type": "Branco", "grapes": ["Chardonnay 100%"], "alcohol": "12.5%", "body": "Médio", "sweetness": "Seco", "featured": False, "badge": "Terroir Calcário", "image": "https://images.unsplash.com/photo-1598306442928-4d90f32c6866?crop=entropy&cs=srgb&fm=jpg&q=85&w=800", "pairing_notes": "Ostras frescas, vieiras grelhadas na manteiga de sálvia e lagosta.", "aromas": "Mineralidade de giz e sílex, casca de limão siciliano e maçã verde.", "service_temp": "10–12°C", "demo": True, "variants": [{"variant_id": "default", "vintage": "2021", "volume_ml": 750, "sku": "SKU-CHABLIS21", "price": 540.00, "discount_price": 490.00, "cost": 320, "stock": 22, "reserved": 0}]},
    {"name": "Barolo DOCG Cannubi", "winery": "Marchesi di Barolo", "country": "Itália", "region": "Piemonte", "type": "Tinto", "grapes": ["Nebbiolo 100%"], "alcohol": "14.5%", "body": "Encorpado", "sweetness": "Seco", "featured": True, "badge": "Rei dos Vinhos", "image": "https://images.unsplash.com/photo-1723013824329-58e636ce9650?crop=entropy&cs=srgb&fm=jpg&q=85&w=800", "pairing_notes": "Tagliolini com trufas brancas de Alba, javali estufado e Parmigiano 36m.", "aromas": "Rosas secas, alcatrão, especiarias orientais e alcaçuz.", "service_temp": "17–18°C", "demo": True, "variants": [{"variant_id": "default", "vintage": "2017", "volume_ml": 750, "sku": "SKU-BAROLO17", "price": 1150.00, "discount_price": 998.00, "cost": 720, "stock": 15, "reserved": 0}]},
    {"name": "Catena Zapata Adrianna Malbec", "winery": "Catena Zapata", "country": "Argentina", "region": "Mendoza, Gualtallary", "type": "Tinto", "grapes": ["Malbec 100%"], "alcohol": "14.2%", "body": "Encorpado", "sweetness": "Seco", "featured": True, "badge": "100 Pts Parker", "image": "https://images.unsplash.com/photo-1553361371-9b22f78e8b1d?crop=entropy&cs=srgb&fm=jpg&q=85&w=800", "pairing_notes": "Ancho grelhado com chimichurri artesanal, morcilla e polenta rústica.", "aromas": "Violetas, mirtilo fresco, notas de grafite e textura mineral única.", "service_temp": "16–18°C", "demo": True, "variants": [{"variant_id": "default", "vintage": "2019", "volume_ml": 750, "sku": "SKU-CATENA19", "price": 1420.00, "discount_price": 1280.00, "cost": 890, "stock": 20, "reserved": 0}]},
    {"name": "Veuve Clicquot La Grande Dame", "winery": "Veuve Clicquot Ponsardin", "country": "França", "region": "Champagne", "type": "Espumante", "grapes": ["Pinot Noir 90%", "Chardonnay 10%"], "alcohol": "12.5%", "body": "Médio", "sweetness": "Brut", "featured": False, "badge": "Cuvée Prestige", "image": "https://images.unsplash.com/photo-1625922379195-3f891e47eaee?crop=entropy&cs=srgb&fm=jpg&q=85&w=800", "pairing_notes": "Caviar Ossetra, blinis com crème fraîche e vieiras cruas.", "aromas": "Brioche tostado, damasco, amêndoas e acidez cítrica sublime.", "service_temp": "6–8°C", "demo": True, "variants": [{"variant_id": "default", "vintage": "2015", "volume_ml": 750, "sku": "SKU-VEUVE15", "price": 1950.00, "discount_price": 1780.00, "cost": 1200, "stock": 10, "reserved": 0}]},
    {"name": "Guaspari Vista da Serra Syrah", "winery": "Vinícola Guaspari", "country": "Brasil", "region": "Espírito Santo do Pinhal, SP", "type": "Tinto", "grapes": ["Syrah 100%"], "alcohol": "14.2%", "body": "Médio-Encorpado", "sweetness": "Seco", "featured": True, "badge": "Orgulho Brasileiro", "image": "https://images.unsplash.com/photo-1568213816046-0ee1c42bd559?crop=entropy&cs=srgb&fm=jpg&q=85&w=800", "pairing_notes": "Pernil de cordeiro assado com alecrim e massas recheadas ao ragu.", "aromas": "Pimenta preta, amoras e café. Expressão magistral da dupla poda.", "service_temp": "16–18°C", "demo": True, "variants": [{"variant_id": "default", "vintage": "2020", "volume_ml": 750, "sku": "SKU-GUASPARI20", "price": 280.00, "discount_price": 249.00, "cost": 145, "stock": 40, "reserved": 0}]},
]

async def seed_db():
    # Proprietário: criado SEM senha. Primeiro acesso via "Esqueci minha senha" (e-mail)
    # ou via Google OAuth — ambos exigem configuração de MFA em seguida.
    exists = await db.users.find_one({"email": OWNER_EMAIL.lower()}, {"_id": 0})
    if not exists:
        await db.users.insert_one({
            "user_id": f"user_{uuid.uuid4().hex[:12]}",
            "email": OWNER_EMAIL.lower(),
            "name": "Proprietário da Adega",
            "role": "owner",
            "password_hash": None,
            "password_version": 1,
            "mfa_enabled": False,
            "marketing_opt_in": False,
            "created_at": now_utc().isoformat(),
        })
    else:
        await db.users.update_one({"email": OWNER_EMAIL.lower()}, {"$set": {"role": "owner"}})
    # Vinhos demo
    if await db.wines.count_documents({}) == 0:
        for w in SEED_WINES:
            doc = {**w, "wine_id": f"wine_{uuid.uuid4().hex[:10]}",
                   "created_at": now_utc().isoformat(), "archived": False}
            await db.wines.insert_one(doc)
            await sync_display_fields(doc["wine_id"])
    # Migração: vinhos antigos sem variants ganham variante default
    async for w in db.wines.find({"variants": {"$exists": False}}, {"_id": 0}):
        await db.wines.update_one({"wine_id": w["wine_id"]},
                                  {"$set": {"variants": [default_variant(w)]}})
        await sync_display_fields(w["wine_id"])
    # Cupom inicial
    if not await db.coupons.find_one({"code": "PRIMEIRAADEGA"}):
        await db.coupons.insert_one({"code": "PRIMEIRAADEGA", "percent": 10, "active": True,
                                     "valid_from": None, "valid_until": None, "max_uses": None,
                                     "uses_count": 0, "created_at": now_utc().isoformat()})
    # Regiões de entrega: marca como pendentes de confirmação caso venham de seed antigo
    await db.shipping_zones.update_many({"demo": True, "confirmed": {"$exists": False}},
                                        {"$set": {"confirmed": False}})
    # migração: zonas sem o campo confirmed (criadas antes desta versão) ficam pendentes
    await db.shipping_zones.update_many({"confirmed": {"$exists": False}}, {"$set": {"confirmed": False}})
    # Configurações
    if not await db.store_settings.find_one({"key": "store"}):
        await db.store_settings.insert_one({"key": "store", "store_name": "Casa da Barrica Wines",
                                            "tagline": "Curadoria, Histórias & Descobertas em cada vinho",
                                            "home": DEFAULT_HOME_CONTENT, "footer": DEFAULT_FOOTER_CONTENT,
                                            "payment_mode": "demo"})
    # TTL: tokens de recuperação expirados são removidos automaticamente (campo Date BSON)
    await db.mfa_recoveries.create_index("expires_at_dt", expireAfterSeconds=0)
    await db.password_resets.create_index("expires_at_dt", expireAfterSeconds=0)
    await db.analytics_events.create_index("created_dt", expireAfterSeconds=ANALYTICS_RETENTION_DAYS * 86400)
    await db.chat_messages.create_index("created_dt", expireAfterSeconds=CHAT_RETENTION_DAYS * 86400)

    # Unicidade de cadastro também no banco: e-mail (total) e CPF do Clube
    # (parcial — várias contas podem não ter CPF; vazio/ausente não é CPF).
    # Duplicidades pré-existentes BLOQUEIAM a criação do índice: o conflito é
    # logado para decisão manual do proprietário (nunca mesclar/excluir automático).
    try:
        dup_emails = await db.users.aggregate([
            {"$group": {"_id": "$email", "n": {"$sum": 1}}}, {"$match": {"n": {"$gt": 1}}}]).to_list(20)
        if dup_emails:
            logger.error("Índice único users.email NÃO criado: %d e-mail(s) duplicado(s): %s",
                         len(dup_emails), [(d["_id"] or "")[:3] + "***" for d in dup_emails])
        else:
            idx = await db.users.index_information()
            if "email_1" in idx and not idx["email_1"].get("unique"):
                await db.users.drop_index("email_1")  # substitui índice legado não-único
            await db.users.create_index("email", unique=True)
    except Exception as e:
        logger.error("Falha ao garantir índice único users.email: %s", e)
    try:
        dup_cpfs = await db.users.aggregate([
            {"$match": {"club.cpf": {"$type": "string"}}},
            {"$group": {"_id": "$club.cpf", "n": {"$sum": 1}}},
            {"$match": {"n": {"$gt": 1}}}]).to_list(20)
        if dup_cpfs:
            logger.error("Índice único users.club.cpf NÃO criado: %d CPF(s) duplicado(s): %s",
                         len(dup_cpfs), ["***" + (d["_id"] or "")[-2:] for d in dup_cpfs])
        else:
            await db.users.create_index("club.cpf", unique=True,
                                        partialFilterExpression={"club.cpf": {"$type": "string"}})
    except Exception as e:
        logger.error("Falha ao garantir índice único users.club.cpf: %s", e)

    # Migração de linguagem (uma vez, idempotente): frases promocionais com "IA"
    # viram "Sommelier (Virtual)" APENAS nos textos da home/rodapé — substituição
    # direcionada às frases antigas conhecidas; demais personalizações intactas.
    COPY_MIGRATION = {
        "Novo · Sommelier com IA": "Novo · Sommelier Virtual",
        "Sommelier com IA": "Sommelier Virtual",
        "Harmonizar com IA": "Harmonizar com Sommelier",
        "harmonização inteligente com IA": "harmonização inteligente com o Sommelier Virtual",
        "harmonização por IA": "harmonização com o Sommelier Virtual",
        "A IA indica": "O Sommelier Virtual indica",
    }
    sdoc = await db.store_settings.find_one({"key": "store"})
    if sdoc:
        changed = False
        for section in ("home", "footer"):
            sec = sdoc.get(section) or {}
            for k, v in list(sec.items()):
                if isinstance(v, str):
                    nv = v
                    for old, new in COPY_MIGRATION.items():
                        if old in nv:
                            nv = nv.replace(old, new)
                    if nv != v:
                        sec[k] = nv
                        changed = True
            sdoc[section] = sec
        if changed:
            await db.store_settings.update_one({"key": "store"},
                                               {"$set": {"home": sdoc["home"], "footer": sdoc["footer"]}})
    # Regiões de entrega (tabela própria — ajuste no painel Admin > Frete)
    if await db.shipping_zones.count_documents({}) == 0:
        await db.shipping_zones.insert_many([
            {"zone_id": "zone_sp_capital", "name": "São Paulo — Capital e Grande SP",
             "cep_prefixes": ["01", "02", "03", "04", "05", "06", "07", "08", "09"],
             "price": 29.90, "deadline": "1 a 3 dias úteis", "active": True,
             "confirmed": False, "demo": True,
             "note": "EXEMPLO — não representa condição comercial aprovada. Confirme no painel."},
            {"zone_id": "zone_sudeste", "name": "Capitais do Sudeste (RJ, MG, ES)",
             "cep_prefixes": ["20", "21", "22", "23", "24", "25", "26", "27", "28", "29", "30", "31", "32", "33", "34", "35", "36", "37", "38", "39"],
             "price": 49.90, "deadline": "3 a 6 dias úteis", "active": True,
             "confirmed": False, "demo": True,
             "note": "EXEMPLO — não representa condição comercial aprovada. Confirme no painel."},
        ])

@app.on_event("startup")
async def startup():
    await seed_db()
    await load_secrets()
    from subscriptions import seed_subscriptions
    await seed_subscriptions()
    try:
        await init_storage()
        logger.info("Object storage inicializado para backups externos")
    except Exception as e:
        logger.error("Falha ao inicializar object storage: %s", type(e).__name__)

@api_router.get("/")
async def root():
    return {"app": "Casa da Barrica Wines API", "status": "ok"}

import subscriptions  # registra as rotas de assinaturas no api_router (importar ANTES do include_router)

app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=[o for o in os.environ.get('CORS_ORIGINS', '').split(',') if o and o != '*'],
    allow_origin_regex=".*",
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
