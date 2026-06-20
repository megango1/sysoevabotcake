import os
import warnings
import logging
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

from database import (
    init_db, upsert_user, check_access,
    grant_access, revoke_access, get_all_users, get_stats, get_access_until, ADMIN_ID, ADMIN_IDS,
    add_section, get_subsections, get_subsection,
    update_section, delete_section, get_all_sections,
)
from keyboards import (
    main_menu_keyboard, back_keyboard, payment_keyboard,
    contact_keyboard, cakes_submenu_keyboard, admin_main_keyboard,
    admin_subsections_menu_keyboard, admin_sections_pick_keyboard,
    admin_users_keyboard, admin_revoke_users_keyboard, admin_sections_list_keyboard,
    subsections_keyboard, choose_parent_keyboard, skip_keyboard, media_collect_keyboard, cancel_keyboard,
    contact_inline_keyboard,
)
from content import TEXTS, SECTION_LABELS, SECTION_KEYS, CAKE_SUBCATS, CAKE_SUBCAT_KEYS, ALL_SECTION_LABELS

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

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
    )


# ── /myid ─────────────────────────────────────────────────────────────────────

async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_html(
        f"🆔 Твій Telegram ID: <code>{user.id}</code>"
    )


# ── /admin ────────────────────────────────────────────────────────────────────

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_html(
        TEXTS["admin_panel"],
        reply_markup=admin_main_keyboard(),
    )


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
        reply_markup=skip_keyboard(),
    )
    return EDIT_PHOTO


async def edit_skip_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "📸 Надішли нове <b>фото</b> або пропусти (залишиться поточне):",
        parse_mode="HTML",
        reply_markup=skip_keyboard(),
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
            TEXTS["payment_info"], reply_markup=payment_keyboard()
        )
        return
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
        need_email=False,
    )


async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm the payment before Telegram processes it (must reply within 10 sec)."""
    query = update.pre_checkout_query
    await query.answer(ok=True)


async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Grant access automatically after successful payment."""
    user = update.effective_user
    payment = update.message.successful_payment
    await grant_access(user.id, days=SUBSCRIPTION_DAYS)
    logger.info(
        "Payment from user %s: %s %s",
        user.id,
        payment.total_amount / 100,
        payment.currency,
    )
    await update.message.reply_html(
        f"✅ <b>Оплата отримана!</b>\n\n"
        f"Доступ відкрито на <b>{SUBSCRIPTION_DAYS} днів</b>.\n"
        f"Дякуємо за підтримку! 🎉",
        reply_markup=main_menu_keyboard(True),
    )


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
            await update.message.reply_html(TEXTS["price_info"])
        else:
            await update.message.reply_html(
                TEXTS["welcome_no_access"], reply_markup=main_menu_keyboard(False)
            )
        return

    if text == "📖 Рецепти":
        await update.message.reply_html(
            "📖 <b>Рецепти</b>\n\nОбери підкатегорію:",
            reply_markup=cakes_submenu_keyboard(),
        )
    elif text in CAKE_SUBCAT_KEYS:
        parent_key = CAKE_SUBCAT_KEYS[text]
        label = CAKE_SUBCATS[parent_key]
        subsections = await get_subsections(parent_key)
        if not subsections:
            await update.message.reply_html(
                f"{label}\n\n🔜 Рецепти ще не додані.",
                reply_markup=cakes_submenu_keyboard(),
            )
        else:
            await update.message.reply_text(
                f"📂 {label}", reply_markup=subsections_keyboard(subsections, parent_key)
            )
    elif text == "◀️ Назад":
        await update.message.reply_html(
            TEXTS["welcome_access"], reply_markup=main_menu_keyboard(True)
        )
    elif text in SECTION_KEYS:
        parent_key = SECTION_KEYS[text]
        label = SECTION_LABELS[parent_key]
        subsections = await get_subsections(parent_key)
        if not subsections:
            await update.message.reply_html(f"{label}\n\n🔜 Підрозділи ще не додані.")
        else:
            await update.message.reply_text(
                f"📂 {label}", reply_markup=subsections_keyboard(subsections, parent_key)
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
        await update.message.reply_html(msg, reply_markup=main_menu_keyboard(True))
    elif text == "📩 Зв'язок з автором":
        await update.message.reply_html(TEXTS["contact_author"], reply_markup=contact_inline_keyboard())
    else:
        await update.message.reply_html(
            TEXTS["welcome_access"], reply_markup=main_menu_keyboard(True)
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
        caption = f"*{label}*\n\n{content}"
        kb = back_keyboard(f"back_section_{parent_key}")

        media_items = (
            [InputMediaPhoto(media=fid) for fid in photos] +
            [InputMediaVideo(media=fid) for fid in videos]
        )

        if media_items:
            media_items[0].caption = caption
            media_items[0].parse_mode = "Markdown"
            if len(media_items) == 1 and photos:
                await query.message.reply_photo(photo=photos[0], caption=caption, parse_mode="Markdown", reply_markup=kb)
            elif len(media_items) == 1 and videos:
                await query.message.reply_video(video=videos[0], caption=caption, parse_mode="Markdown", reply_markup=kb)
            else:
                await query.message.reply_media_group(media=media_items)
                await query.message.reply_text("⬆️", reply_markup=kb)
            await query.delete_message()
        else:
            await query.edit_message_text(caption, parse_mode="Markdown", reply_markup=kb)
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


# ── App setup ─────────────────────────────────────────────────────────────────

async def post_init(application: Application):
    await init_db()
    logger.info("Supabase ініціалізовано.")


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
        fallbacks=[CommandHandler("cancel", add_cancel)],
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
        fallbacks=[CommandHandler("cancel", edit_cancel_handler)],
        per_user=True,
    )

    app.add_handler(add_conv)
    app.add_handler(edit_conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid_command))
    app.add_handler(CommandHandler("admin", admin_command))
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
