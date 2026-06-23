import os
import html
import warnings
import logging
import httpx
from dotenv import load_dotenv

load_dotenv()

warnings.filterwarnings("ignore", message=".*per_message=False.*", category=UserWarning)

from telegram import Update, LabeledPrice, InputMediaPhoto, InputMediaVideo
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    filters,
)

PAYMENTS_TOKEN: str = os.environ.get("PAYMENTS_TOKEN", "")
PAYMENT_CURRENCY: str = os.environ.get("PAYMENT_CURRENCY", "UAH")
SUBSCRIPTION_PRICE: int = int(os.environ.get("SUBSCRIPTION_PRICE", "299"))
SUBSCRIPTION_DAYS: int = int(os.environ.get("SUBSCRIPTION_DAYS", "30"))

CHECKBOX_LOGIN: str = os.environ.get("CHECKBOX_LOGIN", "")
CHECKBOX_PASSWORD: str = os.environ.get("CHECKBOX_PASSWORD", "")
CHECKBOX_LICENSE_KEY: str = os.environ.get("CHECKBOX_LICENSE_KEY", "")
CHECKBOX_API: str = "https://api.checkbox.in.ua/api/v1"

from database import (
    init_db, upsert_user, check_access,
    grant_access, revoke_access, get_all_users, get_stats, get_access_until, ADMIN_ID, ADMIN_IDS,
    add_section, get_subsections, get_subsection,
    update_section, delete_section, get_all_sections,
    get_users_to_notify, mark_notified,
    save_payment, get_recent_payments,
)
from keyboards import (
    main_menu_keyboard, back_keyboard, payment_keyboard,
    contact_keyboard, cakes_submenu_keyboard, admin_main_keyboard,
    admin_subsections_menu_keyboard, admin_sections_pick_keyboard,
    admin_users_keyboard, admin_revoke_users_keyboard, admin_sections_list_keyboard,
    subsections_keyboard, choose_parent_keyboard, skip_keyboard, media_collect_keyboard, cancel_keyboard,
    contact_inline_keyboard, admin_payments_keyboard,
)
from content import TEXTS, SECTION_LABELS, SECTION_KEYS, CAKE_SUBCATS, CAKE_SUBCAT_KEYS, ALL_SECTION_LABELS

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ── Checkbox fiscalization ─────────────────────────────────────────────────────

async def _checkbox_get_token_and_open_shift(client: httpx.AsyncClient) -> str | None:
    """
    Try two auth flows and return a valid token with an open shift, or None on failure.
    Flow A: license-key direct signin (no password needed).
    Flow B: login/password → license-key binding.
    """
    # ── Flow A: direct license-key signin ──────────────────────────────────────
    if CHECKBOX_LICENSE_KEY:
        lk_resp = await client.post(
            f"{CHECKBOX_API}/cashier/signin/license-key",
            json={"license_key": CHECKBOX_LICENSE_KEY},
        )
        if lk_resp.status_code in (200, 201):
            token = lk_resp.json().get("access_token", "")
            if token:
                logger.info("Checkbox: Flow A (direct license-key signin) succeeded.")
                headers = {"Authorization": f"Bearer {token}"}
                s = await client.post(f"{CHECKBOX_API}/shifts", headers=headers)
                if s.status_code in (200, 201, 422):
                    return token
                logger.warning("Checkbox Flow A: shift open failed %s %s", s.status_code, s.text)
        else:
            logger.warning("Checkbox Flow A: %s %s", lk_resp.status_code, lk_resp.text)

    # ── Flow B: login/password → bind license key ───────────────────────────────
    if CHECKBOX_LOGIN and CHECKBOX_PASSWORD:
        r = await client.post(
            f"{CHECKBOX_API}/cashier/signin",
            json={"login": CHECKBOX_LOGIN, "password": CHECKBOX_PASSWORD},
        )
        if r.status_code != 200:
            logger.error("Checkbox Flow B signin failed: %s %s", r.status_code, r.text)
            return None
        token = r.json().get("access_token", "")
        if not token:
            return None
        headers = {"Authorization": f"Bearer {token}"}

        if CHECKBOX_LICENSE_KEY:
            lk = await client.post(
                f"{CHECKBOX_API}/cashier/signin/license-key",
                json={"license_key": CHECKBOX_LICENSE_KEY},
                headers=headers,
            )
            if lk.status_code in (200, 201):
                new_tok = lk.json().get("access_token", "")
                if new_tok:
                    token = new_tok
                    headers = {"Authorization": f"Bearer {token}"}
                logger.info("Checkbox Flow B: license-key bound.")
            else:
                logger.warning("Checkbox Flow B: license-key bind %s %s", lk.status_code, lk.text)

        s = await client.post(f"{CHECKBOX_API}/shifts", headers=headers)
        if s.status_code in (200, 201):
            logger.info("Checkbox: Flow B succeeded, new shift opened.")
            return token
        if s.status_code == 422:
            # Shift already open — this is fine, just use the existing token
            logger.info("Checkbox: shift already open — using existing shift.")
            return token
        else:
            logger.error("Checkbox Flow B: shift open failed %s %s", s.status_code, s.text)

    return None


async def checkbox_issue_receipt(email: str, amount_uah: float, description: str) -> tuple[bool, str]:
    """Issue a fiscal receipt via Checkbox and send it to the customer's email.
    Returns (True, "") on success, (False, error_message) on failure.
    """
    if not CHECKBOX_LICENSE_KEY and not all([CHECKBOX_LOGIN, CHECKBOX_PASSWORD]):
        logger.info("Checkbox not configured — skipping receipt.")
        return False, "Checkbox не налаштовано"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            token = await _checkbox_get_token_and_open_shift(client)
            if not token:
                msg = "Не вдалось отримати токен Checkbox (перевірте логін/пароль/ключ)"
                logger.error("Checkbox: %s", msg)
                return False, msg
            headers = {"Authorization": f"Bearer {token}"}
            amount_kopecks = int(round(amount_uah * 100))
            if amount_kopecks <= 0:
                msg = f"Некоректна сума: {amount_uah} грн → {amount_kopecks} коп."
                logger.error("Checkbox: %s", msg)
                return False, msg
            payload = {
                "goods": [
                    {
                        "good": {
                            "code": "subscription_001",
                            "name": description,
                            "price": amount_kopecks,
                            "unit_code": "PIECE",
                        },
                        "quantity": 1000,
                    }
                ],
                "payments": [{"type": "CASHLESS", "value": amount_kopecks}],
                "delivery": {"email": email, "phones": []},
            }
            receipt_resp = await client.post(
                f"{CHECKBOX_API}/receipts/sell",
                json=payload,
                headers=headers,
            )
            if receipt_resp.status_code not in (200, 201):
                msg = f"HTTP {receipt_resp.status_code}: {receipt_resp.text[:300]}"
                logger.error("Checkbox receipt failed: %s", msg)
                return False, msg
            logger.info("Checkbox receipt issued for %s.", email)
            return True, ""
    except Exception as exc:
        msg = str(exc)
        logger.error("Checkbox API error: %s", msg)
        return False, msg


# ConversationHandler states — add flow
ASK_TITLE, ASK_EMOJI, ASK_CONTENT, ASK_PHOTO, ASK_VIDEO = range(5)
# ConversationHandler states — edit flow
EDIT_PICK, EDIT_TITLE, EDIT_EMOJI, EDIT_CONTENT, EDIT_PHOTO, EDIT_VIDEO = range(5, 11)


# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await upsert_user(user.id, user.username, user.full_name)
    has_access = await check_access(user.id)
    await update.message.reply_html(
        TEXTS["welcome_access"] if has_access else TEXTS["welcome_no_access"],
        reply_markup=main_menu_keyboard(has_access),
        protect_content=True,
    )


# ── /myid ─────────────────────────────────────────────────────────────────────

async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_html(
        f"🆔 Твій Telegram ID: <code>{user.id}</code>",
        protect_content=True,
    )


# ── /admin ────────────────────────────────────────────────────────────────────

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_html(
        TEXTS["admin_panel"],
        reply_markup=admin_main_keyboard(),
    )


async def test_checkbox_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command: /test_checkbox [email] — runs each Checkbox API step and reports results."""
    if update.effective_user.id not in ADMIN_IDS:
        return

    args = context.args or []
    test_email = args[0] if args else None

    lines: list[str] = ["🔍 <b>Тест Checkbox API</b>\n"]
    lines.append(f"   LOGIN: <code>{CHECKBOX_LOGIN or '—'}</code>")
    lines.append(f"   KEY:   <code>{(CHECKBOX_LICENSE_KEY[:8] + '…') if CHECKBOX_LICENSE_KEY else '—'}</code>\n")

    async with httpx.AsyncClient(timeout=20) as client:
        token: str | None = None

        # ── Flow A: direct license-key signin (no password) ───────────────────
        lines.append("<b>Флоу A</b> (ліцензійний ключ без логіну):")
        try:
            r = await client.post(
                f"{CHECKBOX_API}/cashier/signin/license-key",
                json={"license_key": CHECKBOX_LICENSE_KEY},
            )
            if r.status_code in (200, 201):
                token = r.json().get("access_token", "")
                lines.append(f"  ✅ Вхід по ключу: OK")
            else:
                lines.append(f"  ❌ Вхід по ключу: {r.status_code} <code>{r.text[:200]}</code>")
        except Exception as e:
            lines.append(f"  ❌ Вхід по ключу: помилка <code>{e}</code>")

        # ── Flow B: login/password → license-key bind ─────────────────────────
        if not token:
            lines.append("\n<b>Флоу B</b> (логін/пароль → ключ):")
            try:
                r = await client.post(
                    f"{CHECKBOX_API}/cashier/signin",
                    json={"login": CHECKBOX_LOGIN, "password": CHECKBOX_PASSWORD},
                )
                if r.status_code == 200:
                    token = r.json().get("access_token", "")
                    lines.append(f"  ✅ Вхід касира: OK")
                    headers_b = {"Authorization": f"Bearer {token}"}
                    lk = await client.post(
                        f"{CHECKBOX_API}/cashier/signin/license-key",
                        json={"license_key": CHECKBOX_LICENSE_KEY},
                        headers=headers_b,
                    )
                    if lk.status_code in (200, 201):
                        new_tok = lk.json().get("access_token", "")
                        if new_tok:
                            token = new_tok
                        lines.append(f"  ✅ Прив'язка до каси: OK")
                    else:
                        lines.append(f"  ⚠️ Прив'язка до каси: {lk.status_code} <code>{lk.text[:200]}</code>")
                else:
                    lines.append(f"  ❌ Вхід касира: {r.status_code} <code>{r.text[:200]}</code>")
                    token = None
            except Exception as e:
                lines.append(f"  ❌ Flow B помилка: <code>{e}</code>")
                token = None

        if not token:
            lines.append("\n❌ <b>Не вдалось отримати токен. Перевірте дані в .env</b>")
            await update.message.reply_html("\n".join(lines))
            return

        headers = {"Authorization": f"Bearer {token}"}

        # ── Cashier info & PRRO ───────────────────────────────────────────────
        try:
            me = await client.get(f"{CHECKBOX_API}/cashier/me", headers=headers)
            if me.status_code == 200:
                me_data = me.json()
                cashier_name = me_data.get("full_name") or me_data.get("login") or "—"
                prro = me_data.get("prro") or {}
                prro_id = prro.get("id", "—")
                prro_key = prro.get("license_key", "—")
                prro_status = prro.get("status", "—")
                lines.append(
                    f"\n👤 <b>Касир:</b> {cashier_name}\n"
                    f"🏦 <b>ПРРО ID:</b> <code>{prro_id}</code>\n"
                    f"🔑 <b>ПРРО ключ:</b> <code>{prro_key}</code>\n"
                    f"📊 <b>Статус ПРРО:</b> {prro_status}"
                )
                if prro_key and prro_key != "—" and prro_key != CHECKBOX_LICENSE_KEY:
                    lines.append(
                        f"\n⚠️ <b>Ключ у .env відрізняється від ПРРО!</b>\n"
                        f"У .env: <code>{CHECKBOX_LICENSE_KEY}</code>\n"
                        f"Має бути: <code>{prro_key}</code>"
                    )
            else:
                lines.append(f"\n⚠️ /cashier/me: {me.status_code} <code>{me.text[:150]}</code>")
        except Exception as e:
            lines.append(f"\n⚠️ /cashier/me помилка: <code>{e}</code>")

        # ── Shift ─────────────────────────────────────────────────────────────
        lines.append("")
        try:
            r = await client.post(f"{CHECKBOX_API}/shifts", headers=headers)
            if r.status_code in (200, 201):
                lines.append(f"✅ Зміна: відкрито нову")
            elif r.status_code == 422:
                # Could be "already open" or "no PRRO" — read the detail
                try:
                    detail = r.json()
                    detail_msg = detail.get("message") or detail.get("detail") or r.text[:200]
                except Exception:
                    detail_msg = r.text[:200]
                # "already open" is OK; anything else is an error
                low = detail_msg.lower()
                if any(w in low for w in ("already", "відкрита", "exist", "open")):
                    lines.append(f"✅ Зміна: вже відкрита (використовуємо існуючу)")
                else:
                    lines.append(
                        f"❌ Зміна: 422 — <code>{detail_msg}</code>\n"
                        f"💡 Схоже що касир не має зареєстрованого ПРРО.\n"
                        f"Зайдіть на my.checkbox.in.ua → ПРРО → Додати та прив'яжіть касира."
                    )
                    await update.message.reply_html("\n".join(lines))
                    return
            else:
                lines.append(f"❌ Зміна: {r.status_code} <code>{r.text[:300]}</code>")
                await update.message.reply_html("\n".join(lines))
                return
        except Exception as e:
            lines.append(f"❌ Зміна: помилка <code>{e}</code>")
            await update.message.reply_html("\n".join(lines))
            return

        # ── Receipt ───────────────────────────────────────────────────────────
        if test_email:
            try:
                payload = {
                    "goods": [{
                        "good": {
                            "code": "test_001",
                            "name": "Тест підписки",
                            "price": 100,
                            "unit_code": "PIECE",
                        },
                        "quantity": 1000,
                    }],
                    "payments": [{"type": "CASHLESS", "value": 100}],
                    "delivery": {"email": test_email, "phones": []},
                }
                r = await client.post(f"{CHECKBOX_API}/receipts/sell", json=payload, headers=headers)
                if r.status_code in (200, 201):
                    lines.append(f"✅ Чек: надіслано на <code>{test_email}</code> 🎉")
                else:
                    lines.append(f"❌ Чек: {r.status_code}\n<code>{r.text[:400]}</code>")
            except Exception as e:
                lines.append(f"❌ Чек: помилка <code>{e}</code>")
        else:
            lines.append(f"\n💡 Щоб надіслати тестовий чек:\n/test_checkbox ваш@email.com")

    await update.message.reply_html("\n".join(lines))


# ── /add — add subsection (ConversationHandler) ───────────────────────────────

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_user.id not in ADMIN_IDS:
        return ConversationHandler.END
    text = "📂 <b>Додати підрозділ</b>\n\nОберіть розділ, куди додати:"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, parse_mode="HTML", reply_markup=choose_parent_keyboard()
        )
    else:
        await update.message.reply_html(text, reply_markup=choose_parent_keyboard())
    return ASK_TITLE


async def add_chose_parent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "add_cancel":
        await query.edit_message_text("❌ Скасовано.")
        return ConversationHandler.END

    parent_key = data.replace("add_to_", "")
    context.user_data["new_section"] = {"parent_key": parent_key}
    label = ALL_SECTION_LABELS.get(parent_key, parent_key)
    await query.edit_message_text(
        f"✅ Розділ: <b>{label}</b>\n\n✏️ Напиши <b>назву кнопки</b> (наприклад: Медівник):",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("add_cancel"),
    )
    return ASK_TITLE


async def add_got_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    title = update.message.text.strip()
    context.user_data["new_section"]["title"] = title
    await update.message.reply_html(
        f"✅ Назва: <b>{title}</b>\n\n✏️ Напиши <b>емодзі</b> для кнопки (наприклад: 🎂):",
        reply_markup=skip_keyboard(),
    )
    return ASK_EMOJI


async def add_got_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    emoji = update.message.text.strip() if update.message else ""
    context.user_data["new_section"]["emoji"] = emoji
    await _ask_content(update, context)
    return ASK_CONTENT


async def add_skip_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    context.user_data["new_section"]["emoji"] = ""
    await _ask_content(update, context)
    return ASK_CONTENT


async def _ask_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "✏️ Напиши <b>текст</b>, який побачать користувачі в цьому підрозділі:"
    if update.message:
        await update.message.reply_html(text)
    else:
        await update.callback_query.edit_message_text(text, parse_mode="HTML")


async def add_got_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    content = update.message.text.strip()
    context.user_data["new_section"]["content"] = content
    context.user_data["new_section"].setdefault("photos", [])
    await update.message.reply_html(
        "📸 Надішли <b>фото</b> для цього підрозділу.\nМожна надіслати кілька — по одному:",
        reply_markup=media_collect_keyboard(0, "фото"),
    )
    return ASK_PHOTO


async def add_got_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photo = update.message.photo[-1]
    photos = context.user_data["new_section"].setdefault("photos", [])
    photos.append(photo.file_id)
    count = len(photos)
    await update.message.reply_html(
        f"✅ Фото {count} додано! Надішли ще або натисни <b>Далі</b>:",
        reply_markup=media_collect_keyboard(count, "фото"),
    )
    return ASK_PHOTO


async def add_next_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    context.user_data["new_section"].setdefault("videos", [])
    count = len(context.user_data["new_section"].get("photos", []))
    await update.callback_query.edit_message_text(
        "🎬 Надішли <b>відео</b> для цього підрозділу.\nМожна надіслати кілька — по одному:"
        if count == 0 else
        f"✅ {count} фото збережено.\n\n🎬 Надішли <b>відео</b> або натисни <b>Далі</b>:",
        parse_mode="HTML",
        reply_markup=media_collect_keyboard(0, "відео"),
    )
    return ASK_VIDEO


async def add_got_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    video = update.message.video
    videos = context.user_data["new_section"].setdefault("videos", [])
    videos.append(video.file_id)
    count = len(videos)
    await update.message.reply_html(
        f"✅ Відео {count} додано! Надішли ще або натисни <b>Далі</b>:",
        reply_markup=media_collect_keyboard(count, "відео"),
    )
    return ASK_VIDEO


async def add_next_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    return await _save_section(update, context, via_callback=True)


async def _save_section(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    via_callback: bool = False,
) -> int:
    data = context.user_data.pop("new_section", {})
    parent_key = data.get("parent_key", "")
    label = ALL_SECTION_LABELS.get(parent_key, parent_key)
    emoji = data.get("emoji", "")
    title = data.get("title", "")

    photos = data.get("photos", [])
    videos = data.get("videos", [])
    photo_file_id = "|||".join(photos) if photos else None
    video_file_id = "|||".join(videos) if videos else None

    section_id = await add_section(
        parent_key=parent_key,
        title=title,
        emoji=emoji,
        content=data.get("content", ""),
        photo_file_id=photo_file_id,
        video_file_id=video_file_id,
    )

    summary = (
        f"✅ <b>Підрозділ додано!</b>\n\n"
        f"🆔 ID: <code>{section_id}</code>\n"
        f"📂 Розділ: {label}\n"
        f"🔘 Кнопка: {emoji} {title}\n"
        f"📸 Фото: {len(photos) if photos else 'немає'}\n"
        f"🎬 Відео: {len(videos) if videos else 'немає'}"
    )

    if via_callback:
        await update.callback_query.edit_message_text(summary, parse_mode="HTML")
    else:
        await update.message.reply_html(summary)
    return ConversationHandler.END


async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("new_section", None)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("◀️ Скасовано.")
    elif update.message:
        await update.message.reply_text("◀️ Скасовано.")
    return ConversationHandler.END


# ── Edit section (ConversationHandler) ────────────────────────────────────────

async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_user.id not in ADMIN_IDS:
        return ConversationHandler.END
    await update.callback_query.answer()
    sections = await get_all_sections()
    if not sections:
        await update.callback_query.edit_message_text(
            "📋 Підрозділів ще немає.",
            reply_markup=back_keyboard("admin_subsections_menu"),
        )
        return ConversationHandler.END
    await update.callback_query.edit_message_text(
        "✏️ <b>Редагувати підрозділ</b>\n\nОбери підрозділ:",
        parse_mode="HTML",
        reply_markup=admin_sections_pick_keyboard(sections),
    )
    return EDIT_PICK


async def edit_picked(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "edit_cancel":
        await query.edit_message_text("◀️ Скасовано.")
        return ConversationHandler.END
    section_id = int(query.data.replace("edit_pick_", ""))
    section = await get_subsection(section_id)
    if not section:
        await query.edit_message_text("❌ Підрозділ не знайдено.")
        return ConversationHandler.END
    context.user_data["edit_section"] = {"id": section_id, "original": section, "updates": {}}
    title = section.get("title", "")
    emoji = section.get("emoji", "")
    await query.edit_message_text(
        f"✏️ Редагуєш: <b>{emoji} {title}</b>\n\n"
        f"Поточна назва: <b>{title}</b>\n\nНапиши нову назву або пропусти:",
        parse_mode="HTML",
        reply_markup=skip_keyboard(),
    )
    return EDIT_TITLE


async def edit_got_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["edit_section"]["updates"]["title"] = update.message.text.strip()
    orig = context.user_data["edit_section"]["original"]
    await update.message.reply_html(
        f"Поточне емодзі: <b>{orig.get('emoji') or '—'}</b>\n\nНапиши нове або пропусти:",
        reply_markup=skip_keyboard(),
    )
    return EDIT_EMOJI


async def edit_skip_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    orig = context.user_data["edit_section"]["original"]
    await update.callback_query.edit_message_text(
        f"Поточне емодзі: <b>{orig.get('emoji') or '—'}</b>\n\nНапиши нове або пропусти:",
        parse_mode="HTML",
        reply_markup=skip_keyboard(),
    )
    return EDIT_EMOJI


async def edit_got_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["edit_section"]["updates"]["emoji"] = update.message.text.strip()
    orig = context.user_data["edit_section"]["original"]
    preview = (orig.get("content") or "")[:100]
    await update.message.reply_html(
        f"Поточний текст:\n<i>{preview}{'...' if len(orig.get('content',''))>100 else ''}</i>\n\nНапиши новий або пропусти:",
        reply_markup=skip_keyboard(),
    )
    return EDIT_CONTENT


async def edit_skip_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    orig = context.user_data["edit_section"]["original"]
    preview = (orig.get("content") or "")[:100]
    await update.callback_query.edit_message_text(
        f"Поточний текст:\n<i>{preview}{'...' if len(orig.get('content',''))>100 else ''}</i>\n\nНапиши новий або пропусти:",
        parse_mode="HTML",
        reply_markup=skip_keyboard(),
    )
    return EDIT_CONTENT


async def edit_got_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["edit_section"]["updates"]["content"] = update.message.text.strip()
    await update.message.reply_html(
        "📸 Надішли нове <b>фото</b> або пропусти (залишиться поточне):",
        reply_markup=media_collect_keyboard(0, "фото"),
    )
    return EDIT_PHOTO


async def edit_skip_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "📸 Надішли нове <b>фото</b> або пропусти (залишиться поточне):",
        parse_mode="HTML",
        reply_markup=media_collect_keyboard(0, "фото"),
    )
    return EDIT_PHOTO


async def edit_got_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photos = context.user_data["edit_section"].setdefault("new_photos", [])
    photos.append(update.message.photo[-1].file_id)
    count = len(photos)
    await update.message.reply_html(
        f"✅ Фото {count} додано! Надішли ще або натисни <b>Далі</b>:",
        reply_markup=media_collect_keyboard(count, "фото"),
    )
    return EDIT_PHOTO


async def edit_next_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    new_photos = context.user_data["edit_section"].get("new_photos", [])
    if new_photos:
        context.user_data["edit_section"]["updates"]["photo_file_id"] = "|||".join(new_photos)
    count = len(new_photos)
    await update.callback_query.edit_message_text(
        "🎬 Надішли нове <b>відео</b> або натисни <b>Далі</b> (залишиться поточне):"
        if count == 0 else
        f"✅ {count} фото збережено.\n\n🎬 Надішли <b>відео</b> або натисни <b>Далі</b>:",
        parse_mode="HTML",
        reply_markup=media_collect_keyboard(0, "відео"),
    )
    return EDIT_VIDEO


async def edit_got_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    videos = context.user_data["edit_section"].setdefault("new_videos", [])
    videos.append(update.message.video.file_id)
    count = len(videos)
    await update.message.reply_html(
        f"✅ Відео {count} додано! Надішли ще або натисни <b>Далі</b>:",
        reply_markup=media_collect_keyboard(count, "відео"),
    )
    return EDIT_VIDEO


async def edit_next_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    new_videos = context.user_data["edit_section"].get("new_videos", [])
    if new_videos:
        context.user_data["edit_section"]["updates"]["video_file_id"] = "|||".join(new_videos)
    return await _save_edit(update, context, via_callback=True)


async def _save_edit(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    via_callback: bool = False,
) -> int:
    data = context.user_data.pop("edit_section", {})
    section_id = data.get("id")
    updates = data.get("updates", {})
    if updates and section_id:
        await update_section(section_id, **updates)
        section = await get_subsection(section_id)
        emoji = section.get("emoji", "") if section else ""
        title = section.get("title", str(section_id)) if section else str(section_id)
        msg = f"✅ <b>Підрозділ оновлено!</b>\n\n🔘 {emoji} {title}"
    else:
        msg = "ℹ️ Змін не було."
    if via_callback:
        await update.callback_query.edit_message_text(msg, parse_mode="HTML")
    else:
        await update.message.reply_html(msg)
    return ConversationHandler.END


async def edit_cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("edit_section", None)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("◀️ Скасовано.")
    elif update.message:
        await update.message.reply_text("◀️ Скасовано.")
    return ConversationHandler.END


# ── /list — list all subsections ──────────────────────────────────────────────

async def list_sections(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    sections = await get_all_sections()
    if not sections:
        await update.message.reply_text("Підрозділів ще немає. Додай через /add")
        return

    lines = ["📋 <b>Усі підрозділи:</b>\n"]
    current_key = None
    for s in sections:
        if s["parent_key"] != current_key:
            current_key = s["parent_key"]
            lines.append(f"\n{ALL_SECTION_LABELS.get(current_key, current_key)}:")
        active = "✅" if s["is_active"] else "❌"
        emoji = s.get("emoji", "")
        lines.append(f"  {active} <code>{s['id']}</code> — {emoji} {s['title']}")

    lines.append("\n🗑 Видалити: /del <code>ID</code>")
    await update.message.reply_html("\n".join(lines))


# ── /del — delete subsection ──────────────────────────────────────────────────

async def del_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_html("Використання: /del <code>ID</code>\nID дивись у /list")
        return
    section_id = int(args[0])
    section = await get_subsection(section_id)
    if not section:
        await update.message.reply_text(f"❌ Підрозділ #{section_id} не знайдено.")
        return
    await delete_section(section_id)
    emoji = section.get("emoji", "")
    await update.message.reply_html(
        f"🗑 Видалено: <code>{section_id}</code> — {emoji} {section['title']}"
    )


# ── Payments ──────────────────────────────────────────────────────────────────

async def send_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a Telegram invoice to the user."""
    if not PAYMENTS_TOKEN:
        await update.message.reply_html(
            TEXTS["payment_info"], reply_markup=payment_keyboard(),
            protect_content=True,
        )
        return
    try:
        await update.message.reply_invoice(
            title=f"Підписка на {SUBSCRIPTION_DAYS} днів",
            description=f"Повний доступ до всіх матеріалів на {SUBSCRIPTION_DAYS} днів",
            payload=f"sub_{update.effective_user.id}_{SUBSCRIPTION_DAYS}d",
            provider_token=PAYMENTS_TOKEN,
            currency=PAYMENT_CURRENCY,
            prices=[LabeledPrice(f"Підписка {SUBSCRIPTION_DAYS} днів", SUBSCRIPTION_PRICE * 100)],
            start_parameter="subscribe",
            photo_url=None,
            need_name=False,
            need_phone_number=False,
            need_email=True,
            send_email_to_provider=False,
            protect_content=True,
        )
    except Exception as exc:
        logger.error("send_invoice failed: %s", exc)
        err_text = str(exc)
        hint = ""
        if "CURRENCY_TOTAL_AMOUNT_INVALID" in err_text or "currency" in err_text.lower():
            hint = (
                "\n\n⚠️ <b>Підказка для тестового режиму:</b>\n"
                "Тестовий токен BotFather підтримує лише <b>USD</b>.\n"
                "Встанови у .env:\n"
                "<code>PAYMENT_CURRENCY=USD\nSUBSCRIPTION_PRICE=1</code>"
            )
        await update.message.reply_html(
            f"❌ Не вдалось створити рахунок.\n<code>{html.escape(err_text[:200])}</code>{hint}",
            protect_content=True,
        )


async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm the payment before Telegram processes it (must reply within 10 sec)."""
    query = update.pre_checkout_query
    await query.answer(ok=True)


async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Grant access automatically after successful payment and issue fiscal receipt."""
    user = update.effective_user
    payment = update.message.successful_payment
    await grant_access(user.id, days=SUBSCRIPTION_DAYS)
    amount_uah = payment.total_amount / 100
    logger.info(
        "Payment from user %s: %s %s",
        user.id,
        amount_uah,
        payment.currency,
    )
    await update.message.reply_html(
        f"✅ <b>Оплата отримана!</b>\n\n"
        f"Доступ відкрито на <b>{SUBSCRIPTION_DAYS} днів</b>.\n"
        f"Дякуємо за підтримку! 🎉",
        reply_markup=main_menu_keyboard(True),
        protect_content=True,
    )
    email = (
        payment.order_info.email
        if payment.order_info and payment.order_info.email
        else None
    )
    try:
        await save_payment(
            user_id=user.id,
            full_name=user.full_name,
            username=user.username,
            amount_uah=amount_uah,
            currency=payment.currency,
            email=email,
            days=SUBSCRIPTION_DAYS,
        )
    except Exception as exc:
        logger.error("Failed to save payment record: %s", exc)

    name_display = html.escape(user.full_name or user.username or str(user.id))
    username_display = f" (@{html.escape(user.username)})" if user.username else ""
    email_display = html.escape(email) if email else "не вказано"
    notify_text = (
        f"💰 <b>Нова оплата!</b>\n\n"
        f"👤 {name_display}{username_display}\n"
        f"🆔 <code>{user.id}</code>\n"
        f"💳 <b>{amount_uah:.2f} {payment.currency}</b>\n"
        f"📅 Підписка на {SUBSCRIPTION_DAYS} днів\n"
        f"📧 Email: {email_display}"
    )
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=notify_text, parse_mode="HTML")
        except Exception as exc:
            logger.warning("Could not notify admin %s: %s", admin_id, exc)

    if email:
        description = f"Підписка на {SUBSCRIPTION_DAYS} днів"
        receipt_ok, receipt_err = await checkbox_issue_receipt(email, amount_uah, description)
        if receipt_ok:
            await update.message.reply_html(
                f"🧾 Фіскальний чек надіслано на <b>{html.escape(email)}</b>.",
                protect_content=True,
            )
        else:
            logger.warning("Checkbox receipt failed for user %s email %s: %s", user.id, email, receipt_err)
            err_admin_text = (
                f"⚠️ <b>Checkbox: чек не видано!</b>\n"
                f"👤 <code>{user.id}</code> ({html.escape(user.full_name or '')})\n"
                f"📧 {html.escape(email)}\n"
                f"💳 {amount_uah:.2f} {payment.currency}\n"
                f"❌ Помилка: <code>{html.escape(receipt_err[:300])}</code>"
            )
            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(chat_id=admin_id, text=err_admin_text, parse_mode="HTML")
                except Exception:
                    pass
    else:
        logger.warning("No email from user %s — Checkbox receipt skipped", user.id)
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"⚠️ <b>Checkbox: email не отримано!</b>\n"
                        f"👤 <code>{user.id}</code> ({html.escape(user.full_name or '')})\n"
                        f"💳 {amount_uah:.2f} {payment.currency}\n"
                        f"Чек не видано — користувач не вказав email при оплаті."
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                pass


# ── Text messages ─────────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    await upsert_user(user.id, user.username, user.full_name)
    has_access = await check_access(user.id)

    if not has_access:
        if text == "💳 Оплатити підписку":
            await send_invoice(update, context)
        elif text == "💰 Вартість":
            await update.message.reply_html(TEXTS["price_info"], protect_content=True)
        else:
            await update.message.reply_html(
                TEXTS["welcome_no_access"], reply_markup=main_menu_keyboard(False),
                protect_content=True,
            )
        return

    if text == "📖 Рецепти":
        await update.message.reply_html(
            "📖 <b>Рецепти</b>\n\nОбери підкатегорію:",
            reply_markup=cakes_submenu_keyboard(),
            protect_content=True,
        )
    elif text in CAKE_SUBCAT_KEYS:
        parent_key = CAKE_SUBCAT_KEYS[text]
        label = CAKE_SUBCATS[parent_key]
        subsections = await get_subsections(parent_key)
        if not subsections:
            await update.message.reply_html(
                f"{label}\n\n🔜 Рецепти ще не додані.",
                reply_markup=cakes_submenu_keyboard(),
                protect_content=True,
            )
        else:
            await update.message.reply_text(
                f"📂 {label}", reply_markup=subsections_keyboard(subsections, parent_key),
                protect_content=True,
            )
    elif text == "◀️ Назад":
        await update.message.reply_html(
            TEXTS["welcome_access"], reply_markup=main_menu_keyboard(True),
            protect_content=True,
        )
    elif text in SECTION_KEYS:
        parent_key = SECTION_KEYS[text]
        label = SECTION_LABELS[parent_key]
        subsections = await get_subsections(parent_key)
        if not subsections:
            await update.message.reply_html(f"{label}\n\n🔜 Підрозділи ще не додані.", protect_content=True)
        else:
            await update.message.reply_text(
                f"📂 {label}", reply_markup=subsections_keyboard(subsections, parent_key),
                protect_content=True,
            )
    elif text == "📅 Моя підписка":
        from datetime import timezone as _tz
        access_until = await get_access_until(user.id)
        if access_until is None:
            msg = "📅 <b>Моя підписка</b>\n\n❌ Термін підписки не встановлено."
        else:
            from datetime import datetime as _dt
            now = _dt.now(tz=_tz.utc)
            days_left = (access_until - now).days
            date_str = access_until.strftime("%d.%m.%Y")
            if days_left > 0:
                msg = (
                    f"📅 <b>Моя підписка</b>\n\n"
                    f"✅ Активна до: <b>{date_str}</b>\n"
                    f"⏳ Залишилось днів: <b>{days_left}</b>"
                )
            else:
                msg = (
                    f"📅 <b>Моя підписка</b>\n\n"
                    f"❌ Підписка закінчилась: <b>{date_str}</b>"
                )
        await update.message.reply_html(msg, reply_markup=main_menu_keyboard(True), protect_content=True)
    elif text == "📩 Зв'язок з автором":
        await update.message.reply_html(TEXTS["contact_author"], reply_markup=contact_inline_keyboard(), protect_content=True)
    else:
        await update.message.reply_html(
            TEXTS["welcome_access"], reply_markup=main_menu_keyboard(True),
            protect_content=True,
        )


# ── Admin text input for grant/revoke ─────────────────────────────────────────

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        return
    action = context.user_data.get("admin_action")
    if not action:
        await handle_message(update, context)
        return
    text = update.message.text.strip()
    try:
        target_id = int(text)
    except ValueError:
        await update.message.reply_text("❌ Невірний ID. Введіть число.")
        return
    if action == "grant":
        await grant_access(target_id, days=SUBSCRIPTION_DAYS)
        await update.message.reply_html(
            f"✅ Доступ надано <code>{target_id}</code> на {SUBSCRIPTION_DAYS} днів.",
            reply_markup=back_keyboard("admin_users"),
        )
    context.user_data.pop("admin_action", None)


# ── Inline callbacks ──────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    await query.answer()
    data = query.data
    has_access = await check_access(user.id)

    if data == "pay_now":
        await query.edit_message_text(
            TEXTS["pay_now"], parse_mode="HTML", reply_markup=back_keyboard("back_pay")
        )
        return
    if data == "access_info":
        await query.edit_message_text(
            TEXTS["access_info"], parse_mode="HTML", reply_markup=back_keyboard("back_pay")
        )
        return
    if data == "back_pay":
        await query.edit_message_text(
            TEXTS["payment_info"], parse_mode="HTML", reply_markup=payment_keyboard()
        )
        return

    if data.startswith("back_section_"):
        parent_key = data[len("back_section_"):]

        # delete media messages that were sent alongside the section text
        chat_id = query.message.chat_id
        for mid in context.user_data.pop("section_media_msgs", []):
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=mid)
            except Exception:
                pass

        label = ALL_SECTION_LABELS.get(parent_key, "Розділ")
        subsections = await get_subsections(parent_key)
        if subsections:
            await query.edit_message_text(
                f"📂 {label}", reply_markup=subsections_keyboard(subsections, parent_key)
            )
        else:
            await query.edit_message_text(f"{label}\n\n🔜 Підрозділи ще не додані.")
        return

    if data.startswith("sub_"):
        await query.answer()
        if not has_access:
            await query.edit_message_text(
                TEXTS["welcome_no_access"], parse_mode="HTML", reply_markup=payment_keyboard()
            )
            return
        section_id = int(data[4:])
        section = await get_subsection(section_id)
        if not section:
            await query.edit_message_text("❌ Підрозділ не знайдено.")
            return

        label = f"{section['emoji']} {section['title']}" if section.get("emoji") else section["title"]
        content = section.get("content") or "🔜 Контент скоро буде додано..."
        parent_key = section.get("parent_key", "")
        photo_raw = section.get("photo_file_id") or ""
        video_raw = section.get("video_file_id") or ""
        photos = [p for p in photo_raw.split("|||") if p]
        videos = [v for v in video_raw.split("|||") if v]
        caption = f"<b>{html.escape(label)}</b>\n\n{html.escape(content)}"
        kb = back_keyboard(f"back_section_{parent_key}")

        try:
            await query.delete_message()
        except Exception:
            pass

        media_msg_ids: list[int] = []
        try:
            if len(photos) == 1:
                m = await query.message.reply_photo(photo=photos[0], protect_content=True)
                media_msg_ids.append(m.message_id)
            elif len(photos) > 1:
                msgs = await query.message.reply_media_group(
                    media=[InputMediaPhoto(media=fid) for fid in photos],
                    protect_content=True,
                )
                media_msg_ids.extend(m.message_id for m in msgs)

            if len(videos) == 1:
                m = await query.message.reply_video(video=videos[0], protect_content=True)
                media_msg_ids.append(m.message_id)
            elif len(videos) > 1:
                msgs = await query.message.reply_media_group(
                    media=[InputMediaVideo(media=fid) for fid in videos],
                    protect_content=True,
                )
                media_msg_ids.extend(m.message_id for m in msgs)

            text_msg = await query.message.reply_html(caption, reply_markup=kb, protect_content=True)
            context.user_data["section_media_msgs"] = media_msg_ids
            context.user_data["section_text_msg_id"] = text_msg.message_id
        except Exception as e:
            logger.error("Error sending section %s: %s", section_id, e)
            await query.message.reply_html(caption, reply_markup=kb, protect_content=True)
        return

    # Admin-only callbacks
    if user.id not in ADMIN_IDS:
        return

    if data == "noop":
        return

    if data == "admin_back":
        await query.edit_message_text(
            TEXTS["admin_panel"], parse_mode="HTML", reply_markup=admin_main_keyboard()
        )
        return

    if data == "admin_subsections_menu":
        await query.edit_message_text(
            "📂 <b>Підрозділи</b>\n\nОбери дію:",
            parse_mode="HTML",
            reply_markup=admin_subsections_menu_keyboard(),
        )
        return

    if data == "admin_list_sections":
        sections = await get_all_sections()
        if not sections:
            await query.edit_message_text(
                "📋 Підрозділів ще немає.",
                reply_markup=admin_subsections_menu_keyboard(),
            )
            return
        await query.edit_message_text(
            "📋 <b>Усі підрозділи</b>\n\nНатисни 🗑 щоб видалити:",
            parse_mode="HTML",
            reply_markup=admin_sections_list_keyboard(sections),
        )
        return

    if data.startswith("admin_del_"):
        section_id = int(data[len("admin_del_"):])
        section = await get_subsection(section_id)
        if section:
            await delete_section(section_id)
        sections = await get_all_sections()
        emoji = section.get("emoji", "") if section else ""
        title = section["title"] if section else str(section_id)
        if sections:
            await query.edit_message_text(
                f"🗑 Видалено: {emoji} <b>{title}</b>\n\n📋 <b>Усі підрозділи:</b>",
                parse_mode="HTML",
                reply_markup=admin_sections_list_keyboard(sections),
            )
        else:
            await query.edit_message_text(
                f"🗑 Видалено: {emoji} <b>{title}</b>\n\nПідрозділів більше немає.",
                parse_mode="HTML",
                reply_markup=admin_subsections_menu_keyboard(),
            )
        return

    if data == "admin_payments":
        payments = await get_recent_payments(limit=20)
        if not payments:
            await query.edit_message_text(
                "💰 <b>Платежі</b>\n\nПлатежів ще немає.",
                parse_mode="HTML",
                reply_markup=back_keyboard("admin_back"),
            )
            return
        from datetime import datetime as _dt, timezone as _tz
        lines = []
        total = sum(float(p["amount_uah"]) for p in payments)
        for p in payments:
            name = p.get("full_name") or p.get("username") or str(p["user_id"])
            uname = f" (@{p['username']})" if p.get("username") else ""
            email = p.get("email") or "—"
            amount = float(p["amount_uah"])
            paid_at_raw = p.get("paid_at", "")
            try:
                dt = _dt.fromisoformat(paid_at_raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_tz.utc)
                date_str = dt.strftime("%d.%m.%Y %H:%M")
            except Exception:
                date_str = paid_at_raw[:16] if paid_at_raw else "—"
            lines.append(
                f"📅 <b>{date_str}</b>\n"
                f"👤 {html.escape(name)}{html.escape(uname)}\n"
                f"💳 {amount:.2f} UAH · 📧 {html.escape(email)}"
            )
        text = (
            f"💰 <b>Останні платежі</b> (макс. 20)\n"
            f"Загалом: <b>{total:.2f} UAH</b>\n\n"
            + "\n\n".join(lines)
        )
        await query.edit_message_text(
            text, parse_mode="HTML",
            reply_markup=back_keyboard("admin_back"),
        )
        return

    if data == "admin_stats":
        s = await get_stats()
        text = (
            "📊 <b>Статистика</b>\n\n"
            f"👥 <b>Всього користувачів:</b> {s['total_users']}\n"
            f"✅ <b>Активна підписка:</b> {s['active_users']}\n"
            f"⏰ <b>Підписка прострочена:</b> {s['expired_users']}\n"
            f"🚫 <b>Без доступу:</b> {s['no_access_users']}\n\n"
            f"📂 <b>Підрозділів всього:</b> {s['total_sections']}\n"
            f"🟢 <b>Активних підрозділів:</b> {s['active_sections']}\n"
        )
        await query.edit_message_text(
            text, parse_mode="HTML",
            reply_markup=back_keyboard("admin_back"),
        )
        return

    if data == "admin_users":
        users = await get_all_users()
        if not users:
            await query.edit_message_text(
                "👥 Користувачів ще немає.", reply_markup=admin_users_keyboard()
            )
            return
        from datetime import datetime as _dt, timezone as _tz
        lines = ["👥 <b>Користувачі:</b>\n"]
        for u in users:
            name = u.get("full_name") or u.get("username") or "—"
            access_until = u.get("access_until")
            if u["has_access"] and access_until:
                try:
                    dt = _dt.fromisoformat(access_until)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=_tz.utc)
                    date_str = dt.strftime("%d.%m.%Y")
                    status = f"✅ до {date_str}"
                except Exception:
                    status = "✅"
            elif u["has_access"]:
                status = "✅"
            else:
                status = "❌"
            lines.append(f"{status} | <code>{u['user_id']}</code> — {name}")
        await query.edit_message_text(
            "\n".join(lines), parse_mode="HTML", reply_markup=admin_users_keyboard()
        )
    elif data == "admin_grant":
        context.user_data["admin_action"] = "grant"
        await query.edit_message_text(
            TEXTS["admin_grant_prompt"], parse_mode="HTML", reply_markup=back_keyboard("admin_users")
        )
    elif data == "admin_revoke":
        all_users = await get_all_users()
        active = [u for u in all_users if u.get("has_access")]
        if not active:
            await query.edit_message_text(
                "👥 Немає користувачів з активним доступом.",
                reply_markup=admin_users_keyboard(),
            )
            return
        await query.edit_message_text(
            "❌ <b>Забрати доступ</b>\n\nОбери користувача:",
            parse_mode="HTML",
            reply_markup=admin_revoke_users_keyboard(active),
        )
    elif data.startswith("revoke_user_"):
        target_id = int(data.replace("revoke_user_", ""))
        await revoke_access(target_id)
        all_users = await get_all_users()
        active = [u for u in all_users if u.get("has_access")]
        if active:
            await query.edit_message_text(
                f"✅ Доступ забрано у <code>{target_id}</code>.\n\n❌ <b>Забрати доступ</b>\n\nОбери користувача:",
                parse_mode="HTML",
                reply_markup=admin_revoke_users_keyboard(active),
            )
        else:
            await query.edit_message_text(
                f"✅ Доступ забрано у <code>{target_id}</code>.\n\nБільше немає користувачів з доступом.",
                parse_mode="HTML",
                reply_markup=admin_users_keyboard(),
            )


# ── Subscription reminders ────────────────────────────────────────────────────

import asyncio as _asyncio

async def _send_reminders(bot) -> None:
    """Check and send reminders at 7d, 3d, 24h before expiry."""
    reminders = [
        (7, "notified_7d", "7 днів"),
        (3, "notified_3d", "3 дні"),
        (1, "notified_1d", "24 години"),
    ]
    for days, flag_col, label in reminders:
        users = await get_users_to_notify(days, flag_col)
        for user in users:
            try:
                await bot.send_message(
                    chat_id=user["user_id"],
                    text=(
                        f"⏰ <b>Нагадування про підписку</b>\n\n"
                        f"Ваша підписка закінчується через <b>{label}</b>!\n\n"
                        f"Щоб не втратити доступ до матеріалів — оформіть нову підписку."
                    ),
                    parse_mode="HTML",
                    protect_content=True,
                    reply_markup=payment_keyboard(),
                )
                await mark_notified(user["user_id"], flag_col)
                logger.info("Sent %s reminder to user %s", label, user["user_id"])
            except Exception as e:
                logger.error("Failed to send reminder to user %s: %s", user["user_id"], e)


async def _reminder_loop(bot) -> None:
    """Background loop: check for reminders every hour."""
    await _asyncio.sleep(60)
    while True:
        try:
            await _send_reminders(bot)
        except Exception as e:
            logger.error("Reminder loop error: %s", e)
        await _asyncio.sleep(3600)


# ── App setup ─────────────────────────────────────────────────────────────────

async def post_init(application: Application):
    await init_db()
    logger.info("Supabase ініціалізовано.")
    _asyncio.create_task(_reminder_loop(application.bot))


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не встановлено!")

    app = Application.builder().token(token).post_init(post_init).build()

    # ConversationHandler for /add
    add_conv = ConversationHandler(
        entry_points=[
            CommandHandler("add", add_start),
            CallbackQueryHandler(add_start, pattern="^admin_add_section$"),
        ],
        per_message=False,
        allow_reentry=True,
        states={
            ASK_TITLE: [
                CallbackQueryHandler(add_chose_parent, pattern="^add_to_"),
                CallbackQueryHandler(add_cancel, pattern="^add_cancel$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_got_title),
            ],
            ASK_EMOJI: [
                CallbackQueryHandler(add_skip_emoji, pattern="^skip$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_got_emoji),
            ],
            ASK_CONTENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_got_content),
            ],
            ASK_PHOTO: [
                CallbackQueryHandler(add_next_photo, pattern="^media_next$"),
                MessageHandler(filters.PHOTO, add_got_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_got_content),
            ],
            ASK_VIDEO: [
                CallbackQueryHandler(add_next_video, pattern="^media_next$"),
                MessageHandler(filters.VIDEO, add_got_video),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", add_cancel),
            CallbackQueryHandler(add_cancel, pattern="^admin_back$"),
        ],
        per_user=True,
    )

    # ConversationHandler for editing sections
    edit_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(edit_start, pattern="^admin_edit_section$"),
        ],
        per_message=False,
        states={
            EDIT_PICK: [
                CallbackQueryHandler(edit_picked, pattern="^edit_pick_\\d+$"),
                CallbackQueryHandler(edit_cancel_handler, pattern="^edit_cancel$"),
            ],
            EDIT_TITLE: [
                CallbackQueryHandler(edit_skip_title, pattern="^skip$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_got_title),
            ],
            EDIT_EMOJI: [
                CallbackQueryHandler(edit_skip_emoji, pattern="^skip$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_got_emoji),
            ],
            EDIT_CONTENT: [
                CallbackQueryHandler(edit_skip_content, pattern="^skip$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_got_content),
            ],
            EDIT_PHOTO: [
                CallbackQueryHandler(edit_next_photo, pattern="^media_next$"),
                MessageHandler(filters.PHOTO, edit_got_photo),
            ],
            EDIT_VIDEO: [
                CallbackQueryHandler(edit_next_video, pattern="^media_next$"),
                MessageHandler(filters.VIDEO, edit_got_video),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", edit_cancel_handler),
            CallbackQueryHandler(edit_cancel_handler, pattern="^admin_back$"),
        ],
        per_user=True,
    )

    app.add_handler(add_conv)
    app.add_handler(edit_conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("test_checkbox", test_checkbox_command))
    app.add_handler(CommandHandler("list", list_sections))
    app.add_handler(CommandHandler("del", del_section))
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.User(list(ADMIN_IDS)),
            handle_admin_input,
        )
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Бот запущено!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
