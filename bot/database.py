import os
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client

logger = logging.getLogger(__name__)

def _parse_admin_ids() -> set[int]:
    raw = os.environ.get("ADMIN_ID", "0")
    return {int(x.strip()) for x in raw.split(",") if x.strip().isdigit()}

ADMIN_IDS: set[int] = _parse_admin_ids()
ADMIN_ID: int = next(iter(ADMIN_IDS), 0)

_supabase: Client | None = None


def get_db() -> Client:
    global _supabase
    if _supabase is None:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")
        if not url or not key:
            missing = []
            if not url:
                missing.append("SUPABASE_URL")
            if not key:
                missing.append("SUPABASE_SERVICE_KEY")
            raise RuntimeError(
                f"Відсутні змінні середовища: {', '.join(missing)}\n"
                "Додай їх у .env або в налаштуваннях контейнера."
            )
        _supabase = create_client(url, key)
    return _supabase


async def _run(fn):
    """Run a blocking Supabase call in a thread so the event loop stays free."""
    return await asyncio.to_thread(fn)


async def init_db() -> None:
    try:
        db = get_db()
        await _run(lambda: db.table("users").select("user_id").limit(1).execute())
        logger.info("Supabase connection OK.")
    except RuntimeError:
        raise
    except Exception as exc:
        logger.error("Supabase connection failed: %s", exc)
        raise


# ── Users ─────────────────────────────────────────────────────────────────────

async def upsert_user(user_id: int, username: str | None, full_name: str | None) -> None:
    db = get_db()
    await _run(lambda: db.table("users").upsert(
        {"user_id": user_id, "username": username, "full_name": full_name},
        on_conflict="user_id",
        ignore_duplicates=False,
    ).execute())


async def check_access(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
    db = get_db()
    res = await _run(lambda: db.table("users")
        .select("has_access, access_until")
        .eq("user_id", user_id)
        .single()
        .execute())
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
            await _run(lambda: db.table("users")
                .update({"has_access": False})
                .eq("user_id", user_id)
                .execute())
            return False
    return True


async def grant_access(user_id: int, days: int = 30) -> None:
    db = get_db()
    access_until = (datetime.now(tz=timezone.utc) + timedelta(days=days)).isoformat()
    await _run(lambda: db.table("users").upsert(
        {
            "user_id": user_id,
            "has_access": True,
            "access_until": access_until,
            "notified_7d": False,
            "notified_3d": False,
            "notified_1d": False,
        },
        on_conflict="user_id",
    ).execute())


async def get_access_until(user_id: int):
    db = get_db()
    res = await _run(lambda: db.table("users")
        .select("access_until")
        .eq("user_id", user_id)
        .single()
        .execute())
    if not res.data:
        return None
    val = res.data.get("access_until")
    if not val:
        return None
    dt = datetime.fromisoformat(val)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def revoke_access(user_id: int) -> None:
    db = get_db()
    await _run(lambda: db.table("users")
        .update({"has_access": False, "access_until": None})
        .eq("user_id", user_id)
        .execute())


async def get_all_users() -> list[dict]:
    db = get_db()
    res = await _run(lambda: db.table("users")
        .select("user_id, username, full_name, has_access, access_until")
        .order("user_id")
        .execute())
    return res.data or []


async def get_stats() -> dict:
    db = get_db()
    now = datetime.now(tz=timezone.utc).isoformat()

    users_res = await _run(lambda: db.table("users")
        .select("user_id, has_access, access_until")
        .execute())
    users = users_res.data or []

    total = len(users)
    active = 0
    expired = 0
    no_access = 0

    for u in users:
        if u.get("has_access"):
            until = u.get("access_until")
            if until:
                expiry = datetime.fromisoformat(until)
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if expiry >= datetime.now(tz=timezone.utc):
                    active += 1
                else:
                    expired += 1
            else:
                active += 1
        else:
            no_access += 1

    sections_res = await _run(lambda: db.table("sections")
        .select("id, is_active")
        .execute())
    sections = sections_res.data or []
    total_sections = len(sections)
    active_sections = sum(1 for s in sections if s.get("is_active"))

    return {
        "total_users": total,
        "active_users": active,
        "expired_users": expired,
        "no_access_users": no_access,
        "total_sections": total_sections,
        "active_sections": active_sections,
    }


# ── Reminders ─────────────────────────────────────────────────────────────────

async def get_users_to_notify(days: int, flag_col: str) -> list[dict]:
    """Return users whose subscription expires in ~`days` days and haven't been notified yet."""
    db = get_db()
    now = datetime.now(tz=timezone.utc)
    target = now + timedelta(days=days)
    lower = (target - timedelta(hours=12)).isoformat()
    upper = (target + timedelta(hours=12)).isoformat()
    res = await _run(lambda: db.table("users")
        .select("user_id, full_name, access_until")
        .eq("has_access", True)
        .eq(flag_col, False)
        .gte("access_until", lower)
        .lte("access_until", upper)
        .execute())
    return res.data or []


async def mark_notified(user_id: int, flag_col: str) -> None:
    """Mark a specific reminder as sent for a user."""
    db = get_db()
    await _run(lambda: db.table("users")
        .update({flag_col: True})
        .eq("user_id", user_id)
        .execute())


# ── Payments ──────────────────────────────────────────────────────────────────

async def save_payment(
    user_id: int,
    full_name: str | None,
    username: str | None,
    amount_uah: float,
    currency: str,
    email: str | None,
    days: int,
) -> None:
    db = get_db()
    await _run(lambda: db.table("payments").insert({
        "user_id": user_id,
        "full_name": full_name,
        "username": username,
        "amount_uah": amount_uah,
        "currency": currency,
        "email": email,
        "days": days,
        "paid_at": datetime.now(tz=timezone.utc).isoformat(),
    }).execute())


async def get_recent_payments(limit: int = 20) -> list[dict]:
    db = get_db()
    res = await _run(lambda: db.table("payments")
        .select("user_id, full_name, username, amount_uah, currency, email, days, paid_at")
        .order("paid_at", desc=True)
        .limit(limit)
        .execute())
    return res.data or []


# ── Payment requests (manual screenshot approval) ─────────────────────────────

async def save_payment_request(
    user_id: int,
    username: str | None,
    full_name: str | None,
    screenshot_file_id: str,
) -> None:
    db = get_db()
    now = datetime.now(tz=timezone.utc).isoformat()
    await _run(lambda: db.table("payment_requests").upsert(
        {
            "user_id": user_id,
            "username": username,
            "full_name": full_name,
            "screenshot_file_id": screenshot_file_id,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
        },
        on_conflict="user_id",
    ).execute())


async def get_pending_requests() -> list[dict]:
    db = get_db()
    res = await _run(lambda: db.table("payment_requests")
        .select("user_id, username, full_name, screenshot_file_id, status, created_at")
        .eq("status", "pending")
        .order("created_at", desc=False)
        .execute())
    return res.data or []


async def update_payment_request_status(user_id: int, status: str) -> None:
    db = get_db()
    await _run(lambda: db.table("payment_requests")
        .update({"status": status, "updated_at": datetime.now(tz=timezone.utc).isoformat()})
        .eq("user_id", user_id)
        .execute())


async def get_all_payment_requests(limit: int = 30) -> list[dict]:
    db = get_db()
    res = await _run(lambda: db.table("payment_requests")
        .select("user_id, username, full_name, status, created_at")
        .order("created_at", desc=True)
        .limit(limit)
        .execute())
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
    res = await _run(lambda: db.table("sections").insert({
        "parent_key": parent_key,
        "title": title,
        "emoji": emoji,
        "content": content,
        "photo_file_id": photo_file_id,
        "video_file_id": video_file_id,
        "is_active": True,
    }).execute())
    return res.data[0]["id"]


async def get_subsections(parent_key: str) -> list[dict]:
    db = get_db()
    res = await _run(lambda: db.table("sections")
        .select("id, title, emoji, is_active")
        .eq("parent_key", parent_key)
        .eq("is_active", True)
        .order("id")
        .execute())
    return res.data or []


async def get_subsection(section_id: int) -> dict | None:
    db = get_db()
    res = await _run(lambda: db.table("sections")
        .select("*")
        .eq("id", section_id)
        .single()
        .execute())
    return res.data


async def update_section(section_id: int, **fields) -> None:
    db = get_db()
    await _run(lambda: db.table("sections").update(fields).eq("id", section_id).execute())


async def delete_section(section_id: int) -> None:
    db = get_db()
    await _run(lambda: db.table("sections").delete().eq("id", section_id).execute())


async def get_all_sections() -> list[dict]:
    db = get_db()
    res = await _run(lambda: db.table("sections")
        .select("id, parent_key, title, emoji, is_active")
        .order("parent_key")
        .order("id")
        .execute())
    return res.data or []
