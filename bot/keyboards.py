from telegram import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton, KeyboardButton
from content import SECTION_LABELS, CAKE_SUBCATS, ALL_SECTION_LABELS


# ── Reply keyboards ───────────────────────────────────────────────────────────

def main_menu_keyboard(has_access: bool) -> ReplyKeyboardMarkup:
    if has_access:
        buttons = [
            [KeyboardButton(label) for label in list(SECTION_LABELS.values())[:2]],
            [KeyboardButton(label) for label in list(SECTION_LABELS.values())[2:]],
            [KeyboardButton("📩 Зв'язок з автором")],
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
    buttons.append([InlineKeyboardButton("❌ Скасувати", callback_data="add_cancel")])
    return InlineKeyboardMarkup(buttons)


def skip_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустити", callback_data="skip")]])


def admin_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Додати підрозділ", callback_data="admin_add_section")],
            [InlineKeyboardButton("📋 Список підрозділів", callback_data="admin_list_sections")],
            [InlineKeyboardButton("👥 Користувачі", callback_data="admin_users")],
            [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        ]
    )


def admin_users_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Надати доступ", callback_data="admin_grant")],
            [InlineKeyboardButton("❌ Забрати доступ", callback_data="admin_revoke")],
            [InlineKeyboardButton("◀️ Назад", callback_data="admin_back")],
        ]
    )


def admin_sections_list_keyboard(sections: list[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for s in sections:
        emoji = s.get("emoji", "")
        label = f"🗑 {emoji} {s['title']} (#{s['id']})"
        buttons.append([InlineKeyboardButton(label, callback_data=f"admin_del_{s['id']}")])
    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_back")])
    return InlineKeyboardMarkup(buttons)
