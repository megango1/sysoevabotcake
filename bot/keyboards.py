from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton


def main_menu_keyboard(has_access: bool) -> ReplyKeyboardMarkup:
    if not has_access:
        buttons = [
            [KeyboardButton("💳 Оплатити підписку")],
            [KeyboardButton("💰 Вартість")],
        ]
    else:
        buttons = [
            [KeyboardButton("🍰 Рецепти"), KeyboardButton("🎂 3Д торти")],
            [KeyboardButton("🎨 Декор"), KeyboardButton("📌 Різне")],
            [KeyboardButton("📩 Зв'язок з автором")],
        ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def back_keyboard(callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data=callback)]])


def contact_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✉️ Написати @megango", url="https://t.me/megango")],
    ])


def payment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Оплатити — 200 грн/місяць", callback_data="pay_now")],
        [InlineKeyboardButton("❓ Детальніше про доступ", callback_data="access_info")],
    ])


def admin_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Додати підрозділ", callback_data="admin_add_section")],
        [InlineKeyboardButton("📋 Список підрозділів", callback_data="admin_list_sections")],
        [InlineKeyboardButton("👥 Користувачі", callback_data="admin_users")],
        [InlineKeyboardButton("🔓 Видати доступ", callback_data="admin_grant"),
         InlineKeyboardButton("🔒 Забрати доступ", callback_data="admin_revoke")],
    ])


def admin_sections_list_keyboard(sections: list[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for s in sections:
        label_map = {"recipes": "🍰", "cakes_3d": "🎂", "decor": "🎨", "misc": "📌"}
        cat = label_map.get(s["parent_key"], "📂")
        emoji = s.get("emoji", "")
        buttons.append([
            InlineKeyboardButton(f"{cat} {emoji} {s['title']}", callback_data=f"noop"),
            InlineKeyboardButton("🗑", callback_data=f"admin_del_{s['id']}"),
        ])
    buttons.append([InlineKeyboardButton("⬅️ Назад до панелі", callback_data="admin_back")])
    return InlineKeyboardMarkup(buttons)


def subsections_keyboard(items: list[dict], parent_key: str) -> InlineKeyboardMarkup:
    buttons = []
    for item in items:
        label = f"{item['emoji']} {item['title']}" if item.get("emoji") else item["title"]
        buttons.append([InlineKeyboardButton(label, callback_data=f"sub_{item['id']}")])
    return InlineKeyboardMarkup(buttons)


def choose_parent_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🍰 Рецепти", callback_data="add_to_recipes"),
         InlineKeyboardButton("🎂 3Д торти", callback_data="add_to_cakes_3d")],
        [InlineKeyboardButton("🎨 Декор", callback_data="add_to_decor"),
         InlineKeyboardButton("📌 Різне", callback_data="add_to_misc")],
        [InlineKeyboardButton("❌ Скасувати", callback_data="add_cancel")],
    ])


def skip_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустити", callback_data="skip")]])
