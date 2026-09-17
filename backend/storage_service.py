"""Backup externo via Emergent Object Storage — backups sobrevivem à perda do pod."""
import os
import logging
import httpx

logger = logging.getLogger(__name__)

STORAGE_BASE = (os.environ.get("INTEGRATION_PROXY_URL") or "").strip() or "https://integrations.emergentagent.com"
STORAGE_URL = STORAGE_BASE.rstrip("/") + "/objstore/api/v1/storage"
EMERGENT_KEY = os.environ.get("EMERGENT_LLM_KEY")
APP_NAME = "minha-adega"

_storage_key = None


async def init_storage(force: bool = False) -> str:
    global _storage_key
    if _storage_key and not force:
        return _storage_key
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{STORAGE_URL}/init", json={"emergent_key": EMERGENT_KEY})
    r.raise_for_status()
    _storage_key = r.json()["storage_key"]
    return _storage_key


async def put_object(path: str, data: bytes, content_type: str) -> dict:
    key = await init_storage()
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.put(f"{STORAGE_URL}/objects/{path}",
                        headers={"X-Storage-Key": key, "Content-Type": content_type}, data=data)
    if r.status_code == 404:
        key = await init_storage(force=True)
        async with httpx.AsyncClient(timeout=300) as c:
            r = await c.put(f"{STORAGE_URL}/objects/{path}",
                            headers={"X-Storage-Key": key, "Content-Type": content_type}, data=data)
    r.raise_for_status()
    return r.json()


async def get_object(path: str) -> bytes:
    key = await init_storage()
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.get(f"{STORAGE_URL}/objects/{path}", headers={"X-Storage-Key": key})
    if r.status_code == 404:
        key = await init_storage(force=True)
        async with httpx.AsyncClient(timeout=300) as c:
            r = await c.get(f"{STORAGE_URL}/objects/{path}", headers={"X-Storage-Key": key})
    r.raise_for_status()
    return r.content
