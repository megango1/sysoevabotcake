import os
import warnings
import logging
from dotenv import load_dotenv

load_dotenv()

warnings.filterwarnings("ignore", message=".*per_message=False.*", category=UserWarning)

from telegram import Update, LabeledPrice
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
SUBSCRIPTION_PRICE_UAH = 299
SUBSCRIPTION_DAYS = 30

from database import (
    init_db, upsert_user, check_access,
    grant_access, revoke_access, get_all_users, ADMIN_ID,
    add_section, get_subsections, get_subsection,
    delete_section, get_all_sections,
)
from keyboards import (
    main_menu_keyboard, back_keyboard, payment_keyboard,
    contact_keyboard, admin_main_keyboard, admin_sections_list_keyboard,
    subsections_keyboard, choose_parent_keyboard, skip_keyboard,
)
from content import TEXTS, SECTION_LABELS, SECTION_KEYS

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ConversationHandler states
ASK_TITLE, ASK_EMOJI, ASK_CONTENT, ASK_PHOTO, ASK_VIDEO = range(5)


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
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_html(
        TEXTS["admin_panel"],
        reply_markup=admin_main_keyboard(),
    )


# ── /add — add subsection (ConversationHandler) ───────────────────────────────

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_user.id != ADMIN_ID:
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
    label = SECTION_LABELS.get(parent_key, parent_key)
    await query.edit_message_text(
        f"✅ Розділ: <b>{label}</b>\n\n✏️ Напиши <b>назву кнопки</b> (наприклад: Медовик):",
        parse_mode="HTML",
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
    await update.message.reply_html(
        "📸 Надішли <b>фото</b> для цього підрозділу (або пропусти):",
        reply_markup=skip_keyboard(),
    )
    return ASK_PHOTO


async def add_got_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photo = update.message.photo[-1]
    context.user_data["new_section"]["photo_file_id"] = photo.file_id
    await update.message.reply_html(
        "🎬 Надішли <b>відео</b> для цього підрозділу (або пропусти):",
        reply_markup=skip_keyboard(),
    )
    return ASK_VIDEO


async def add_skip_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    context.user_data["new_section"]["photo_file_id"] = None
    await update.callback_query.edit_message_text(
        "🎬 Надішли <b>відео</b> для цього підрозділу (або пропусти):",
        parse_mode="HTML",
        reply_markup=skip_keyboard(),
    )
    return ASK_VIDEO


async def add_got_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    video = update.message.video
    context.user_data["new_section"]["video_file_id"] = video.file_id
    return await _save_section(update, context)


async def add_skip_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    context.user_data["new_section"]["video_file_id"] = None
    return await _save_section(update, context, via_callback=True)


async def _save_section(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    via_callback: bool = False,
) -> int:
    data = context.user_data.pop("new_section", {})
    parent_key = data.get("parent_key", "")
    label = SECTION_LABELS.get(parent_key, parent_key)
    emoji = data.get("emoji", "")
    title = data.get("title", "")

    section_id = await add_section(
        parent_key=parent_key,
        title=title,
        emoji=emoji,
        content=data.get("content", ""),
        photo_file_id=data.get("photo_file_id"),
        video_file_id=data.get("video_file_id"),
    )

    summary = (
        f"✅ <b>Підрозділ додано!</b>\n\n"
        f"🆔 ID: <code>{section_id}</code>\n"
        f"📂 Розділ: {label}\n"
        f"🔘 Кнопка: {emoji} {title}\n"
        f"📸 Фото: {'так' if data.get('photo_file_id') else 'немає'}\n"
        f"🎬 Відео: {'так' if data.get('video_file_id') else 'немає'}"
    )

    if via_callback:
        await update.callback_query.edit_message_text(summary, parse_mode="HTML")
    else:
        await update.message.reply_html(summary)
    return ConversationHandler.END


async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("new_section", None)
    if update.message:
        await update.message.reply_text("❌ Скасовано.")
    return ConversationHandler.END


# ── /list — list all subsections ──────────────────────────────────────────────

async def list_sections(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
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
            lines.append(f"\n{SECTION_LABELS.get(current_key, current_key)}:")
        active = "✅" if s["is_active"] else "❌"
        emoji = s.get("emoji", "")
        lines.append(f"  {active} <code>{s['id']}</code> — {emoji} {s['title']}")

    lines.append("\n🗑 Видалити: /del <code>ID</code>")
    await update.message.reply_html("\n".join(lines))


# ── /del — delete subsection ──────────────────────────────────────────────────

async def del_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
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
        title="Підписка на 30 днів",
        description="Повний доступ до всіх матеріалів на 30 днів",
        payload=f"sub_{update.effective_user.id}_{SUBSCRIPTION_DAYS}d",
        provider_token=PAYMENTS_TOKEN,
        currency="UAH",
        prices=[LabeledPrice("Підписка 30 днів", SUBSCRIPTION_PRICE_UAH * 100)],
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

    if text in SECTION_KEYS:
        parent_key = SECTION_KEYS[text]
        label = SECTION_LABELS[parent_key]
        subsections = await get_subsections(parent_key)
        if not subsections:
            await update.message.reply_html(f"{label}\n\n🔜 Підрозділи ще не додані.")
        else:
            await update.message.reply_text(
                f"📂 {label}", reply_markup=subsections_keyboard(subsections, parent_key)
            )
    elif text == "📩 Зв'язок з автором":
        await update.message.reply_html(TEXTS["contact_author"], reply_markup=main_menu_keyboard(True))
    else:
        await update.message.reply_html(
            TEXTS["welcome_access"], reply_markup=main_menu_keyboard(True)
        )


# ── Admin text input for grant/revoke ─────────────────────────────────────────

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
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
        await grant_access(target_id, days=30)
        await update.message.reply_html(
            f"✅ Доступ надано <code>{target_id}</code> на 30 днів.",
            reply_markup=admin_main_keyboard(),
        )
    elif action == "revoke":
        await revoke_access(target_id)
        await update.message.reply_html(
            f"❌ Доступ забрано у <code>{target_id}</code>.",
            reply_markup=admin_main_keyboard(),
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
        label = SECTION_LABELS.get(parent_key, "Розділ")
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
        photo_fid = section.get("photo_file_id")
        video_fid = section.get("video_file_id")
        caption = f"*{label}*\n\n{content}"
        kb = back_keyboard(f"back_section_{parent_key}")

        if video_fid:
            await query.message.reply_video(video=video_fid, caption=caption, parse_mode="Markdown", reply_markup=kb)
            await query.delete_message()
        elif photo_fid:
            await query.message.reply_photo(photo=photo_fid, caption=caption, parse_mode="Markdown", reply_markup=kb)
            await query.delete_message()
        else:
            await query.edit_message_text(caption, parse_mode="Markdown", reply_markup=kb)
        return

    # Admin-only callbacks
    if user.id != ADMIN_ID:
        return

    if data == "noop":
        return

    if data == "admin_back":
        await query.edit_message_text(
            TEXTS["admin_panel"], parse_mode="HTML", reply_markup=admin_main_keyboard()
        )
        return

    if data == "admin_list_sections":
        sections = await get_all_sections()
        if not sections:
            await query.edit_message_text(
                "📋 Підрозділів ще немає.\n\nНатисни ➕ Додати підрозділ.",
                reply_markup=admin_main_keyboard(),
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
                reply_markup=admin_main_keyboard(),
            )
        return

    if data == "admin_users":
        users = await get_all_users()
        if not users:
            await query.edit_message_text("Користувачів ще немає.", reply_markup=admin_main_keyboard())
            return
        lines = ["👥 <b>Користувачі:</b>\n"]
        for u in users:
            status = "✅" if u["has_access"] else "❌"
            name = u["full_name"] or u["username"] or "—"
            lines.append(f"{status} <code>{u['user_id']}</code> — {name}")
        await query.edit_message_text(
            "\n".join(lines), parse_mode="HTML", reply_markup=admin_main_keyboard()
        )
    elif data == "admin_grant":
        context.user_data["admin_action"] = "grant"
        await query.message.reply_html(TEXTS["admin_grant_prompt"])
    elif data == "admin_revoke":
        context.user_data["admin_action"] = "revoke"
        await query.message.reply_html(TEXTS["admin_revoke_prompt"])


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
                CallbackQueryHandler(add_skip_photo, pattern="^skip$"),
                MessageHandler(filters.PHOTO, add_got_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_got_content),
            ],
            ASK_VIDEO: [
                CallbackQueryHandler(add_skip_video, pattern="^skip$"),
                MessageHandler(filters.VIDEO, add_got_video),
            ],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
        per_user=True,
    )

    app.add_handler(add_conv)
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
            filters.TEXT & ~filters.COMMAND & filters.User(ADMIN_ID),
            handle_admin_input,
        )
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Бот запущено!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
