import os
import logging
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client

logger = logging.getLogger(__name__)

ADMIN_ID: int = int(os.environ.get("ADMIN_ID", "0"))

_supabase: Client | None = None


def get_db() -> Client:
    global _supabase
    if _supabase is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        _supabase = create_client(url, key)
    return _supabase


async def init_db() -> None:
    """Verify connection to Supabase on startup."""
    try:
        db = get_db()
        db.table("users").select("user_id").limit(1).execute()
        logger.info("Supabase connection OK.")
    except Exception as exc:
        logger.error("Supabase connection failed: %s", exc)
        raise


# ── Users ─────────────────────────────────────────────────────────────────────

async def upsert_user(user_id: int, username: str | None, full_name: str | None) -> None:
    db = get_db()
    db.table("users").upsert(
        {
            "user_id": user_id,
            "username": username,
            "full_name": full_name,
        },
        on_conflict="user_id",
        ignore_duplicates=False,
    ).execute()


async def check_access(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    db = get_db()
    res = db.table("users").select("has_access, access_until").eq("user_id", user_id).single().execute()
    if not res.data:
        return False
    row = res.data
    if not row.get("has_access"):
        return False
    access_until = row.get("access_until")
    if access_until:
        expiry = datetime.fromisoformat(access_until)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry < datetime.now(tz=timezone.utc):
            db.table("users").update({"has_access": False}).eq("user_id", user_id).execute()
            return False
    return True


async def grant_access(user_id: int, days: int = 30) -> None:
    db = get_db()
    access_until = (datetime.now(tz=timezone.utc) + timedelta(days=days)).isoformat()
    db.table("users").upsert(
        {
            "user_id": user_id,
            "has_access": True,
            "access_until": access_until,
        },
        on_conflict="user_id",
    ).execute()


async def revoke_access(user_id: int) -> None:
    db = get_db()
    db.table("users").update({"has_access": False, "access_until": None}).eq("user_id", user_id).execute()


async def get_all_users() -> list[dict]:
    db = get_db()
    res = db.table("users").select("user_id, username, full_name, has_access, access_until").order("user_id").execute()
    return res.data or []


# ── Sections ──────────────────────────────────────────────────────────────────

async def add_section(
    parent_key: str,
    title: str,
    emoji: str,
    content: str,
    photo_file_id: str | None,
    video_file_id: str | None,
) -> int:
    db = get_db()
    res = (
        db.table("sections")
        .insert(
            {
                "parent_key": parent_key,
                "title": title,
                "emoji": emoji,
                "content": content,
                "photo_file_id": photo_file_id,
                "video_file_id": video_file_id,
                "is_active": True,
            }
        )
        .execute()
    )
    return res.data[0]["id"]


async def get_subsections(parent_key: str) -> list[dict]:
    db = get_db()
    res = (
        db.table("sections")
        .select("id, title, emoji, is_active")
        .eq("parent_key", parent_key)
        .eq("is_active", True)
        .order("id")
        .execute()
    )
    return res.data or []


async def get_subsection(section_id: int) -> dict | None:
    db = get_db()
    res = (
        db.table("sections")
        .select("*")
        .eq("id", section_id)
        .single()
        .execute()
    )
    return res.data


async def delete_section(section_id: int) -> None:
    db = get_db()
    db.table("sections").delete().eq("id", section_id).execute()


async def get_all_sections() -> list[dict]:
    db = get_db()
    res = (
        db.table("sections")
        .select("id, parent_key, title, emoji, is_active")
        .order("parent_key")
        .order("id")
        .execute()
    )
    return res.data or []
