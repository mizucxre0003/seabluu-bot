import logging
import re
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from . import sheets
from .config import ADMIN_IDS  # только для проверки прав админа

logging.basicConfig(level=logging.INFO)

# Единый справочник статусов (правь при необходимости)
STATUSES = [
    "выкуплен",
    "едет на адрес",
    "приехал на адрес (Китай)",
    "приехал на адрес (Корея)",
    "сборка на доставку",
    "отправлен в Казахстан (из Китая)",
    "отправлен в Казахстан (из Кореи)",
    "приехал к владельцу шопа в Астане",
    "сборка заказа по Казахстану",
    "собран и готов на доставку по Казахстану",
    "отправлен по Казахстану",
    "доставлен",
    "получен",
]

MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton("Отследить заказ")],
        [KeyboardButton("Мои адреса"), KeyboardButton("Мои подписки")],
        [KeyboardButton("Отмена")],
    ],
    resize_keyboard=True,
)

# ====== помощники для order_id и валидности статуса ======
# Поймаем KR-12345 / KR12345 / KR 12345 / KR-12345 — CN
ORDER_ID_RE = re.compile(r"([A-ZА-Я]{1,3})[ \-–—]?\s?(\d{3,})", re.IGNORECASE)

def extract_order_id(s: str) -> str | None:
    if not s:
        return None
    m = ORDER_ID_RE.search(s.strip())
    if not m:
        return None
    prefix = m.group(1).upper()
    num = m.group(2)
    return f"{prefix}-{num}"

def is_valid_status(s: str, statuses: list[str]) -> bool:
    return bool(s) and s.strip().lower() in {x.lower() for x in statuses}

# ========= БАЗОВЫЕ КОМАНДЫ ПОЛЬЗОВАТЕЛЯ =========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот SEABLUU для отслеживания заказов и адресов. Выберите действие ниже.",
        reply_markup=MAIN_KB,
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Команды:\n"
        "• Отследить заказ — статус по номеру\n"
        "• Мои адреса — добавить/изменить адрес\n"
        "• Мои подписки — список подписок на заказы\n"
        "• /admin — админ-панель (только для админов)"
    )


# ========= ТЕКСТЫ/РЕЖИМЫ =========

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()
    text = raw.lower()

    # --- ADMIN FLOW (самый верх, чтобы перехватывать шаги админа) ---
    if update.effective_user.id in ADMIN_IDS:
        mode = context.user_data.get("adm_mode")

        # Добавление заказа: order_id -> client_name -> country -> status -> note -> save
        if mode == "add_order_id":
            context.user_data["adm_buf"] = {"order_id": raw}
            context.user_data["adm_mode"] = "add_order_client"
            await update.message.reply_text("Имя клиента:")
            return

        if mode == "add_order_client":
            context.user_data["adm_buf"]["client_name"] = raw
            context.user_data["adm_mode"] = "add_order_country"
            await update.message.reply_text("Страна/склад (CN или KR):")
            return

        if mode == "add_order_country":
            country = raw.upper()
            if country not in ("CN", "KR"):
                await update.message.reply_text("Введи 'CN' (Китай) или 'KR' (Корея):")
                return
            context.user_data["adm_buf"]["country"] = country
            context.user_data["adm_mode"] = "add_order_status"
            buttons = [[InlineKeyboardButton(s, callback_data=f"adm:pick_status:{s}")] for s in STATUSES[:6]]
            await update.message.reply_text(
                "Выбери стартовый статус кнопкой ниже или напиши точный текст статуса:",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        if mode == "add_order_status":
            # Строгая проверка: разрешаем только из STATUSES
            if not is_valid_status(raw, STATUSES):
                buttons = [[InlineKeyboardButton(s, callback_data=f"adm:pick_status:{s}")] for s in STATUSES[:6]]
                await update.message.reply_text(
                    "Пожалуйста, выбери статус кнопкой ниже (или напиши точный текст статуса из списка):",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
                return
            context.user_data["adm_buf"]["status"] = raw.strip()
            context.user_data["adm_mode"] = "add_order_note"
            await update.message.reply_text("Примечание (или '-' если нет):")
            return

        if mode == "add_order_note":
            buf = context.user_data.get("adm_buf", {})
            buf["note"] = raw if raw != "-" else ""
            try:
                # address_id не используем
                sheets.add_order({
                    "order_id": buf["order_id"],
                    "client_name": buf.get("client_name", ""),
                    "country": buf.get("country", ""),
                    "status": buf.get("status", "выкуплен"),
                    "note": buf.get("note", ""),
                })
                await update.message.reply_text(
                    f"Заказ *{buf['order_id']}* добавлен ✅",
                    parse_mode="Markdown",
                )
            except Exception as e:
                await update.message.reply_text(f"Ошибка: {e}")
            finally:
                context.user_data.pop("adm_mode", None)
                context.user_data.pop("adm_buf", None)
            return

        # Смена статуса: примем order_id даже без явного режима — извлечём из строки
        parsed_id = extract_order_id(raw)
        if mode == "upd_order_id" or (mode is None and parsed_id):
            if not parsed_id:
                await update.message.reply_text("Пришли номер заказа, например: KR-12345")
                return
            context.user_data.setdefault("adm_buf", {})["order_id"] = parsed_id
            context.user_data["adm_mode"] = "upd_pick_status"
            buttons = [[InlineKeyboardButton(s, callback_data=f"adm:set_status:{s}")] for s in STATUSES]
            await update.message.reply_text(
                f"Заказ *{parsed_id}*. Выбери новый статус:",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="Markdown",
            )
            return

        if mode == "find_order":
            rec = sheets.get_order(raw)
            if not rec:
                await update.message.reply_text("Не найдено.")
            else:
                t = (
                    f"*{rec.get('order_id')}*\n"
                    f"Клиент: {rec.get('client_name','')}\n"
                    f"Страна: {rec.get('country','')}\n"
                    f"Статус: {rec.get('status','')}\n"
                    f"Прим.: {rec.get('note','')}"
                )
                await update.message.reply_text(t, parse_mode="Markdown")
            context.user_data.pop("adm_mode", None)
            return

    # --- КОМАНДЫ ПОЛЬЗОВАТЕЛЯ ---
    if text in {"отмена", "cancel"}:
        context.user_data["mode"] = None
        await update.message.reply_text("Ок, отменил. Что дальше?", reply_markup=MAIN_KB)
        return

    if text == "отследить заказ":
        context.user_data["mode"] = "track"
        await update.message.reply_text("Отправьте номер заказа (например: CN-12345):")
        return

    if text == "мои адреса":
        context.user_data["mode"] = None
        await show_addresses(update, context)
        return

    if text == "мои подписки":
        context.user_data["mode"] = None
        await show_subscriptions(update, context)
        return

    # --- РЕЖИМЫ ВВОДА ---
    mode = context.user_data.get("mode")

    if mode == "track":
        order_id = raw
        await query_status(update, context, order_id)
        return

    if mode == "add_address_fullname":
        context.user_data["full_name"] = raw
        await update.message.reply_text("Телефон (пример: 87001234567):")
        context.user_data["mode"] = "add_address_phone"
        return

    if mode == "add_address_phone":
        normalized = raw.strip().replace(" ", "").replace("-", "")
        if normalized.startswith("+7"):
            normalized = "8" + normalized[2:]
        elif normalized.startswith("7"):
            normalized = "8" + normalized[1:]
        if not (normalized.isdigit() and len(normalized) == 11 and normalized.startswith("8")):
            await update.message.reply_text(
                "Похоже на неверный номер. Нужно 11 цифр и обязательно с 8.\n"
                "Пример: 87001234567\n"
                "Введи номер ещё раз или нажми «Отмена»:"
            )
            return
        context.user_data["phone"] = normalized
        await update.message.reply_text("Город (пример: Астана):")
        context.user_data["mode"] = "add_address_city"
        return

    if mode == "add_address_city":
        context.user_data["city"] = raw
        await update.message.reply_text(
            "Адрес (можно в свободной форме):\nПример: Туран 34А, 6 подъезд, 8 этаж, кв. 12"
        )
        context.user_data["mode"] = "add_address_address"
        return

    if mode == "add_address_address":
        context.user_data["address"] = raw
        await update.message.reply_text("Почтовый индекс (пример: 010000):")
        context.user_data["mode"] = "add_address_postcode"
        return

    if mode == "add_address_postcode":
        if not (raw.isdigit() and 5 <= len(raw) <= 6):
            await update.message.reply_text("Индекс выглядит странно. Пример: 010000\nВведи индекс ещё раз или нажми «Отмена».")
            return
        context.user_data["postcode"] = raw
        await save_address(update, context)
        return

    # если не команда и не режим — подсказка
    await update.message.reply_text(
        "Не понял. Нажмите кнопку ниже или введите номер заказа. Для выхода — «Отмена».",
        reply_markup=MAIN_KB,
    )


# ========= БИЗНЕС-ФУНКЦИИ =========

async def query_status(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str):
    order = sheets.get_order(order_id)
    if not order:
        await update.message.reply_text("Такой заказ не найден. Проверьте номер или повторите позже.")
        return

    status = order.get("status") or "статус не указан"
    origin = order.get("origin") or ""

    txt = f"Заказ *{order_id}*\nСтатус: *{status}*"
    if origin:
        txt += f"\nСтрана/источник: {origin}"

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔔 Подписаться на обновления", callback_data=f"sub:{order_id}")
    ]])
    await update.message.reply_markdown(txt, reply_markup=kb)
    context.user_data["mode"] = None


async def show_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    addrs = sheets.list_addresses(update.effective_user.id)
    if not addrs:
        await update.message.reply_text(
            "У вас пока нет адреса. Хотите добавить?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Добавить адрес", callback_data="addr:add")]
            ]),
        )
        return

    lines = []
    for a in addrs:
        lines.append(f"• {a['full_name']}, {a['phone']}, {a['city']}, {a['address']}, {a['postcode']}")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Изменить адрес", callback_data="addr:add")],
        [InlineKeyboardButton("🗑 Удалить адрес", callback_data="addr:del")],
    ])
    await update.message.reply_text("Ваш адрес доставки:\n" + "\n".join(lines), reply_markup=kb)


async def save_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    sheets.upsert_address(
        user_id=u.id,
        full_name=context.user_data.get("full_name", ""),
        phone=context.user_data.get("phone", ""),
        city=context.user_data.get("city", ""),
        address=context.user_data.get("address", ""),
        postcode=context.user_data.get("postcode", ""),
    )
    context.user_data["mode"] = None
    msg = (
        "Адрес сохранён ✅\n\n"
        f"ФИО: {context.user_data.get('full_name','')}\n"
        f"Телефон: {context.user_data.get('phone','')}\n"
        f"Город: {context.user_data.get('city','')}\n"
        f"Адрес: {context.user_data.get('address','')}\n"
        f"Индекс: {context.user_data.get('postcode','')}"
    )
    await update.message.reply_text(msg, reply_markup=MAIN_KB)


async def show_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subs = sheets.list_subscriptions(update.effective_user.id)
    if not subs:
        await update.message.reply_text("Подписок пока нет. Отследите заказ и нажмите «Подписаться».")
        return
    txt = "\n".join([f"• {s['order_id']} (последний статус: {s.get('last_sent_status','—')})" for s in subs])
    await update.message.reply_text("Ваши подписки:\n" + txt)


# ========= CALLBACKS (кнопки) =========

def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить заказ", callback_data="adm:add")],
        [InlineKeyboardButton("✏️ Изменить статус", callback_data="adm:update")],
        [InlineKeyboardButton("🗂 Последние заказы", callback_data="adm:list")],
        [InlineKeyboardButton("🔍 Найти заказ", callback_data="adm:find")],
        [InlineKeyboardButton("↩️ Выйти", callback_data="adm:back")],
    ])


async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await (update.message or update.callback_query.message).reply_text(
        "Админ-панель SEABLUU:", reply_markup=admin_kb()
    )
    context.user_data.pop("adm_mode", None)
    context.user_data.pop("adm_buf", None)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Единый обработчик нажатий inline-кнопок."""
    q = update.callback_query
    await q.answer()
    data = q.data

    # --- админ-меню (вход/навигация) ---
    if data == "adm:back":
        context.user_data.pop("adm_mode", None)
        context.user_data.pop("adm_buf", None)
        await q.message.edit_text("Готово. Вы вышли из админ-панели.")
        return

    if data == "adm:add":
        if update.effective_user.id in ADMIN_IDS:
            context.user_data["adm_mode"] = "add_order_id"
            context.user_data["adm_buf"] = {}
            await q.message.reply_text("Введи *order_id* (например: CN-12345):", parse_mode="Markdown")
        return

    if data == "adm:update":
        if update.effective_user.id in ADMIN_IDS:
            context.user_data["adm_mode"] = "upd_order_id"
            await q.message.reply_text("Введи *order_id* для изменения статуса:", parse_mode="Markdown")
        return

    if data == "adm:list":
        if update.effective_user.id in ADMIN_IDS:
            orders = sheets.list_recent_orders(10)
            if not orders:
                await q.message.reply_text("Список пуст.")
            else:
                txt = "\n".join([f"• {o.get('order_id')} — {o.get('status','')}" for o in orders])
                await q.message.reply_text(f"*Последние заказы:*\n{txt}", parse_mode="Markdown")
        return

    if data == "adm:find":
        if update.effective_user.id in ADMIN_IDS:
            context.user_data["adm_mode"] = "find_order"
            await q.message.reply_text("Введи *order_id* для поиска:", parse_mode="Markdown")
        return

    # --- выбор статуса (админ) ---
    if data.startswith("adm:pick_status:"):
        if update.effective_user.id not in ADMIN_IDS:
            return
        status = data.split("adm:pick_status:", 1)[1]
        context.user_data.setdefault("adm_buf", {})["status"] = status
        context.user_data["adm_mode"] = "add_order_note"
        await q.message.reply_text("Примечание (или '-' если нет):")
        return

    if data.startswith("adm:set_status:"):
        if update.effective_user.id not in ADMIN_IDS:
            return
        status = data.split("adm:set_status:", 1)[1]
        order_id = context.user_data.get("adm_buf", {}).get("order_id")
        ok = sheets.update_order_status(order_id, status)
        if ok:
            await q.message.reply_text(
                f"Статус *{order_id}* обновлён на: _{status}_ ✅",
                parse_mode="Markdown",
            )
        else:
            await q.message.reply_text("Заказ не найден.")
        context.user_data.pop("adm_mode", None)
        context.user_data.pop("adm_buf", None)
        return

    # --- пользовательские кнопки ---
    if data.startswith("sub:"):
        order_id = data.split(":", 1)[1]
        sheets.subscribe(update.effective_user.id, order_id)
        await q.edit_message_reply_markup(reply_markup=None)
        await q.message.reply_text("Готово! Буду присылать обновления по этому заказу 🔔")
        return

    if data == "addr:add":
        await q.message.reply_text("Давайте добавим/обновим адрес.\nФИО:")
        context.user_data["mode"] = "add_address_fullname"
        return

    if data == "addr:del":
        ok = sheets.delete_address(update.effective_user.id)
        await q.message.reply_text("Адрес удалён." if ok else "Удалять нечего — адреса нет.")
        return

    await q.message.reply_text("Действие не поддерживается.")


# ========= Регистрация хендлеров (используется в webhook.py) =========

def register_handlers(application):
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
