import os
from datetime import datetime, timezone, timedelta

import httpx

ADMIN_ID = 1030723047

_SUPABASE_URL: str = ""
_SUPABASE_KEY: str = ""
_headers: dict = {}


def _init_config():
    global _SUPABASE_URL, _SUPABASE_KEY, _headers
    if not _SUPABASE_URL:
        _SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
        _SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
        _headers = {
            "apikey": _SUPABASE_KEY,
            "Authorization": f"Bearer {_SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }


def _url(table: str) -> str:
    return f"{_SUPABASE_URL}/rest/v1/{table}"


async def init_db():
    _init_config()
    # Verify connection works
    async with httpx.AsyncClient() as client:
        r = await client.get(_url("users"), headers=_headers, params={"limit": "1"})
        r.raise_for_status()


# ── Users ─────────────────────────────────────────────────────────────────────

async def upsert_user(user_id: int, username: str, full_name: str):
    _init_config()
    async with httpx.AsyncClient() as client:
        await client.post(
            _url("users"),
            headers={**_headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"user_id": user_id, "username": username or "", "full_name": full_name or ""},
        )


async def check_access(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    _init_config()
    async with httpx.AsyncClient() as client:
        r = await client.get(
            _url("users"),
            headers=_headers,
            params={"user_id": f"eq.{user_id}", "select": "has_access,access_until"},
        )
    rows = r.json()
    if not rows:
        return False
    row = rows[0]
    if not row.get("has_access"):
        return False
    access_until = row.get("access_until")
    if not access_until:
        return True
    until_dt = datetime.fromisoformat(access_until.replace("Z", "+00:00"))
    return until_dt > datetime.now(timezone.utc)


async def grant_access(user_id: int, days: int = 30):
    _init_config()
    until = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    async with httpx.AsyncClient() as client:
        await client.patch(
            _url("users"),
            headers={**_headers, "Prefer": "return=minimal"},
            params={"user_id": f"eq.{user_id}"},
            json={"has_access": True, "access_until": until},
        )


async def revoke_access(user_id: int):
    _init_config()
    async with httpx.AsyncClient() as client:
        await client.patch(
            _url("users"),
            headers={**_headers, "Prefer": "return=minimal"},
            params={"user_id": f"eq.{user_id}"},
            json={"has_access": False},
        )


async def get_all_users() -> list[dict]:
    _init_config()
    async with httpx.AsyncClient() as client:
        r = await client.get(
            _url("users"),
            headers=_headers,
            params={"order": "joined_at.desc"},
        )
    return r.json() or []


# ── Sections ──────────────────────────────────────────────────────────────────

async def add_section(
    parent_key: str,
    title: str,
    emoji: str,
    content: str,
    photo_file_id: str | None = None,
    video_file_id: str | None = None,
) -> int:
    _init_config()
    async with httpx.AsyncClient() as client:
        # Get next order_index
        r = await client.get(
            _url("sections"),
            headers=_headers,
            params={"parent_key": f"eq.{parent_key}", "select": "order_index", "order": "order_index.desc", "limit": "1"},
        )
        rows = r.json()
        idx = (rows[0]["order_index"] + 1) if rows else 1

        ins = await client.post(
            _url("sections"),
            headers=_headers,
            json={
                "parent_key": parent_key,
                "title": title,
                "emoji": emoji,
                "content": content,
                "photo_file_id": photo_file_id,
                "video_file_id": video_file_id,
                "order_index": idx,
                "is_active": True,
            },
        )
    return ins.json()[0]["id"]


async def get_subsections(parent_key: str) -> list[dict]:
    _init_config()
    async with httpx.AsyncClient() as client:
        r = await client.get(
            _url("sections"),
            headers=_headers,
            params={"parent_key": f"eq.{parent_key}", "is_active": "eq.true", "order": "order_index.asc"},
        )
    return r.json() or []


async def get_subsection(section_id: int) -> dict | None:
    _init_config()
    async with httpx.AsyncClient() as client:
        r = await client.get(
            _url("sections"),
            headers=_headers,
            params={"id": f"eq.{section_id}"},
        )
    rows = r.json()
    return rows[0] if rows else None


async def delete_section(section_id: int) -> bool:
    _init_config()
    async with httpx.AsyncClient() as client:
        r = await client.delete(
            _url("sections"),
            headers={**_headers, "Prefer": "return=representation"},
            params={"id": f"eq.{section_id}"},
        )
    return len(r.json()) > 0


async def get_all_sections() -> list[dict]:
    _init_config()
    async with httpx.AsyncClient() as client:
        r = await client.get(
            _url("sections"),
            headers=_headers,
            params={"order": "parent_key.asc,order_index.asc"},
        )
    return r.json() or []
