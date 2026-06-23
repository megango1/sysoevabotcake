from telegram import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton, KeyboardButton
from content import SECTION_LABELS, CAKE_SUBCATS, ALL_SECTION_LABELS


# ── Reply keyboards ───────────────────────────────────────────────────────────

def main_menu_keyboard(has_access: bool) -> ReplyKeyboardMarkup:
    if has_access:
        buttons = [
            [KeyboardButton(label) for label in list(SECTION_LABELS.values())[:2]],
            [KeyboardButton(label) for label in list(SECTION_LABELS.values())[2:]],
            [KeyboardButton("📅 Моя підписка"), KeyboardButton("📩 Зв'язок з автором")],
        ]
    else:
        buttons = [
            [KeyboardButton("💳 Оплатити підписку")],
            [KeyboardButton("💰 Вартість")],
        ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📩 Зв'язок з автором")]],
        resize_keyboard=True,
    )


def cakes_submenu_keyboard() -> ReplyKeyboardMarkup:
    subcat_labels = list(CAKE_SUBCATS.values())
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(subcat_labels[0]), KeyboardButton(subcat_labels[1])],
            [KeyboardButton(subcat_labels[2]), KeyboardButton(subcat_labels[3])],
            [KeyboardButton("◀️ Назад")],
        ],
        resize_keyboard=True,
    )


# ── Inline keyboards ──────────────────────────────────────────────────────────

def back_keyboard(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data=callback_data)]])


def cancel_keyboard(callback_data: str = "add_cancel") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Скасувати", callback_data=callback_data)]])


def contact_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("💬 Заходьте в чат", url="https://t.me/+y9frhgypLlw2NmYy")]])


def payment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💳 Оплатити", callback_data="pay_now")],
            [InlineKeyboardButton("ℹ️ Що входить?", callback_data="access_info")],
        ]
    )


def subsections_keyboard(subsections: list[dict], parent_key: str) -> InlineKeyboardMarkup:
    buttons = []
    for s in subsections:
        label = f"{s['emoji']} {s['title']}" if s.get("emoji") else s["title"]
        buttons.append([InlineKeyboardButton(label, callback_data=f"sub_{s['id']}")])
    return InlineKeyboardMarkup(buttons)


def choose_parent_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for key, label in ALL_SECTION_LABELS.items():
        buttons.append([InlineKeyboardButton(label, callback_data=f"add_to_{key}")])
    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="add_cancel")])
    return InlineKeyboardMarkup(buttons)


def skip_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустити", callback_data="skip")]])


def media_collect_keyboard(count: int, media_type: str = "фото") -> InlineKeyboardMarkup:
    if count == 0:
        label = "⏭ Пропустити"
    else:
        label = f"✅ Далі ({count} {media_type} додано)"
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="media_next")]])


# ── Admin keyboards ───────────────────────────────────────────────────────────

def admin_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📂 Підрозділи", callback_data="admin_subsections_menu")],
            [InlineKeyboardButton("👥 Користувачі", callback_data="admin_users")],
            [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton("💰 Платежі", callback_data="admin_payments")],
        ]
    )


def admin_subsections_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Додати", callback_data="admin_add_section")],
            [InlineKeyboardButton("✏️ Редагувати", callback_data="admin_edit_section")],
            [InlineKeyboardButton("🗑 Видалити", callback_data="admin_list_sections")],
            [InlineKeyboardButton("◀️ Назад", callback_data="admin_back")],
        ]
    )


def admin_sections_pick_keyboard(sections: list[dict], prefix: str = "edit_pick_") -> InlineKeyboardMarkup:
    buttons = []
    for s in sections:
        emoji = s.get("emoji", "")
        label = f"{emoji} {s['title']}".strip()
        buttons.append([InlineKeyboardButton(label, callback_data=f"{prefix}{s['id']}")])
    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="edit_cancel")])
    return InlineKeyboardMarkup(buttons)


def admin_users_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Надати доступ", callback_data="admin_grant")],
            [InlineKeyboardButton("❌ Забрати доступ", callback_data="admin_revoke")],
            [InlineKeyboardButton("◀️ Назад", callback_data="admin_back")],
        ]
    )


def admin_revoke_users_keyboard(users: list[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for u in users:
        name = u.get("full_name") or u.get("username") or str(u["user_id"])
        label = f"❌ {name} ({u['user_id']})"
        buttons.append([InlineKeyboardButton(label, callback_data=f"revoke_user_{u['user_id']}")])
    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_users")])
    return InlineKeyboardMarkup(buttons)


def admin_payments_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]]
    )


def admin_sections_list_keyboard(sections: list[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for s in sections:
        emoji = s.get("emoji", "")
        label = f"🗑 {emoji} {s['title']} (#{s['id']})"
        buttons.append([InlineKeyboardButton(label, callback_data=f"admin_del_{s['id']}")])
    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_subsections_menu")])
    return InlineKeyboardMarkup(buttons)
