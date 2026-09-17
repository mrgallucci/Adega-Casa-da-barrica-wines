import os
import re
import ipaddress
import logging
import httpx
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / '.env')
logger = logging.getLogger(__name__)

EMAIL_BASE_URL = "https://integrations.emergentagent.com"
EMAIL_KEY = os.environ.get("EMERGENT_EMAIL_KEY", "")
EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "Casa da Barrica Wines")
EMAIL_REPLY_TO = os.environ.get("EMAIL_REPLY_TO")
PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL", "").rstrip("/")

_SHORTENERS = ("bit.ly", "tinyurl.com", "t.co", "is.gd", "cutt.ly", "goo.gl", "rebrand.ly")
_CRED_ASK = ("reply with your password", "reply with the code", "send your password", "cvv",
             "send us your password", "enter your password below", "confirm your card number",
             "your full card number", "seed phrase", "recovery phrase", "verify your card",
             "social security number", "confirm your bank details")
_HOSTISH = re.compile(r"\b(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})", re.I)


def _host_ok(host: str) -> bool:
    if not host or "xn--" in host:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    return not any(host == s or host.endswith("." + s) for s in _SHORTENERS)


def _same_site(shown: str, real: str) -> bool:
    return shown == real or real.endswith("." + shown) or shown.endswith("." + real)


class _EmailScan(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.urls, self.anchors = set(), [], []
        self._href, self._text = None, []

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag.lower())
        self.urls += [v for k, v in attrs if k.lower() in ("href", "src") and v]
        if tag.lower() == "a":
            self._href = dict((k.lower(), v) for k, v in attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.anchors.append((self._href, "".join(self._text)))
            self._href, self._text = None, []


def _assert_safe_email(subject: str, html: str) -> None:
    scan = _EmailScan()
    scan.feed(html)
    if scan.tags & {"form", "input", "textarea", "select"}:
        raise ValueError("No forms or input fields in email (G2)")
    body = f"{subject}\n{html}".lower()
    for p in _CRED_ASK:
        if p in body:
            raise ValueError(f"Email asks recipient for credentials: {p!r} (G2)")
    for url in scan.urls:
        low = url.strip().lower()
        if low.startswith(("mailto:", "tel:", "cid:", "#")):
            continue
        if not low.startswith("https://"):
            raise ValueError(f"Email links/assets must be absolute https: {url!r} (G3)")
        host = urlparse(low).hostname or ""
        if not _host_ok(host) or urlparse(low).username is not None:
            raise ValueError(f"Shortened, numeric-host or credential-bearing URL: {url!r} (G3)")
    for href, text in scan.anchors:
        real = urlparse(href.strip().lower()).hostname or ""
        if not real:
            continue
        for m in _HOSTISH.finditer(text):
            if not _same_site(m.group(1).lower(), real):
                raise ValueError(f"Anchor text {m.group(1)!r} != real link host {real!r} (G3)")


def email_configured() -> bool:
    return bool(EMAIL_KEY)


async def send_email(*, to: str, subject: str, html: str, reply_to: str | None = None) -> str | None:
    """Envia e-mail transacional. Nunca lança exceção — falha é registrada e retorna None
    para não interromper fluxos de pedido/cobrança."""
    if not email_configured():
        logger.warning("E-mail não configurado (EMERGENT_EMAIL_KEY ausente). Envio ignorado: %s", subject)
        return None
    try:
        _assert_safe_email(subject, html)
    except ValueError as e:
        logger.error("E-mail bloqueado pelo gate de segurança: %s", e)
        return None
    payload = {"to": [to], "subject": subject, "html": html, "from_name": EMAIL_FROM_NAME}
    if reply_to or EMAIL_REPLY_TO:
        payload["contact_email"] = reply_to or EMAIL_REPLY_TO
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{EMAIL_BASE_URL}/api/v1/email/send",
                headers={"X-Email-Key": EMAIL_KEY},
                json=payload,
            )
        resp.raise_for_status()
        return resp.json().get("id")
    except httpx.HTTPStatusError as e:
        logger.error("Falha no envio de e-mail '%s': %s %s", subject, e.response.status_code, e.response.text[:200])
        return None
    except Exception as e:
        logger.error("Falha no envio de e-mail '%s': %s", subject, type(e).__name__)
        return None


def _wrap(content: str) -> str:
    return (
        '<table role="presentation" width="100%" style="background:#120F0D;padding:24px 0">'
        '<tr><td align="center"><table role="presentation" width="560" style="background:#1C1814;'
        'border:1px solid #C28D58;border-radius:12px;padding:32px;font-family:Arial,sans-serif;'
        'color:#F7F2EB">'
        f'<tr><td><div style="font-size:20px;font-weight:bold;color:#C28D58;margin-bottom:16px">{escape(EMAIL_FROM_NAME)}</div>'
        f'{content}'
        f'<p style="font-size:12px;color:#A89B8C;margin-top:28px;border-top:1px solid #3a322a;padding-top:16px">'
        f'Enviado por {escape(EMAIL_FROM_NAME)}. Nunca pedimos sua senha ou dados de cartão por e-mail.</p>'
        '</td></tr></table></td></tr></table>'
    )


async def send_welcome(to: str, name: str):
    html = _wrap(
        f'<p style="font-size:16px">Olá, {escape(name)}!</p>'
        f'<p>Sua conta na {escape(EMAIL_FROM_NAME)} foi criada com sucesso. Você já pode explorar o catálogo, '
        f'receber sugestões de harmonização e acompanhar seus pedidos.</p>'
        f'<p><a href="{PUBLIC_APP_URL}/catalogo" style="color:#D8A36E">Explorar o catálogo</a></p>'
    )
    return await send_email(to=to, subject=f"Bem-vindo à {EMAIL_FROM_NAME}", html=html)


async def send_password_reset(to: str, name: str, reset_url: str):
    html = _wrap(
        f'<p style="font-size:16px">Olá, {escape(name)}.</p>'
        f'<p>Recebemos uma solicitação para redefinir a senha da sua conta. O link abaixo é de uso único '
        f'e expira em 30 minutos. Se você não solicitou, ignore este e-mail.</p>'
        f'<p><a href="{reset_url}" style="display:inline-block;background:#8A2436;color:#F7F2EB;'
        f'padding:12px 24px;border-radius:999px;text-decoration:none">Redefinir minha senha</a></p>'
    )
    return await send_email(to=to, subject="Redefinição de senha — Casa da Barrica Wines", html=html)


async def send_order_received(to: str, name: str, order_id: str, total_fmt: str):
    html = _wrap(
        f'<p style="font-size:16px">Olá, {escape(name)}!</p>'
        f'<p>Recebemos seu pedido <strong>#{escape(order_id[-6:].upper())}</strong> no valor de '
        f'<strong>{escape(total_fmt)}</strong>. O pagamento ainda está em processamento — você receberá '
        f'um novo e-mail quando for confirmado.</p>'
        f'<p><a href="{PUBLIC_APP_URL}/conta" style="color:#D8A36E">Acompanhar pedido</a></p>'
    )
    return await send_email(to=to, subject=f"Pedido recebido #{order_id[-6:].upper()} — Casa da Barrica Wines", html=html)


async def send_payment_approved(to: str, name: str, order_id: str, total_fmt: str):
    html = _wrap(
        f'<p style="font-size:16px">Olá, {escape(name)}!</p>'
        f'<p>O pagamento do pedido <strong>#{escape(order_id[-6:].upper())}</strong> ({escape(total_fmt)}) '
        f'foi <strong>aprovado</strong>. Suas garrafas serão preparadas para envio climatizado.</p>'
        f'<p><a href="{PUBLIC_APP_URL}/conta" style="color:#D8A36E">Ver detalhes</a></p>'
    )
    return await send_email(to=to, subject=f"Pagamento aprovado #{order_id[-6:].upper()} — Casa da Barrica Wines", html=html)


async def send_order_shipped(to: str, name: str, order_id: str):
    html = _wrap(
        f'<p style="font-size:16px">Olá, {escape(name)}!</p>'
        f'<p>Seu pedido <strong>#{escape(order_id[-6:].upper())}</strong> foi enviado. '
        f'<strong>Importante:</strong> na entrega, um adulto maior de 18 anos deve apresentar documento com foto.</p>'
        f'<p><a href="{PUBLIC_APP_URL}/conta" style="color:#D8A36E">Acompanhar entrega</a></p>'
    )
    return await send_email(to=to, subject=f"Pedido enviado #{order_id[-6:].upper()} — Casa da Barrica Wines", html=html)


async def send_order_cancelled(to: str, name: str, order_id: str, reason: str = ""):
    html = _wrap(
        f'<p style="font-size:16px">Olá, {escape(name)}.</p>'
        f'<p>Seu pedido <strong>#{escape(order_id[-6:].upper())}</strong> foi cancelado.'
        + (f' Motivo: {escape(reason)}.' if reason else "")
        + ' Se já houve cobrança, o estorno será processado pelo meio de pagamento.</p>'
    )
    return await send_email(to=to, subject=f"Pedido cancelado #{order_id[-6:].upper()} — Casa da Barrica Wines", html=html)


async def send_mfa_recovery(to: str, name: str, recovery_url: str):
    html = _wrap(
        f'<p style="font-size:16px">Olá, {escape(name)}.</p>'
        f'<p>Recebemos uma solicitação de recuperação do segundo fator (MFA) da sua conta administrativa. '
        f'O link abaixo é de uso único e expira em 30 minutos. Após confirmar, todas as sessões serão '
        f'encerradas e você precisará entrar com sua senha e cadastrar um novo autenticador.</p>'
        f'<p>Se você não solicitou, ignore este e-mail — sua proteção atual permanece ativa.</p>'
        f'<p><a href="{recovery_url}" style="display:inline-block;background:#8A2436;color:#F7F2EB;'
        f'padding:12px 24px;border-radius:999px;text-decoration:none">Recuperar acesso MFA</a></p>'
    )
    return await send_email(to=to, subject="Recuperação de MFA — Casa da Barrica Wines", html=html)
