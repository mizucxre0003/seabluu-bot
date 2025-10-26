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
from .config import ADMIN_IDS

logging.basicConfig(level=logging.INFO)

# ---------------------- Константы и утилиты ----------------------

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
    "доставка не оплачена",   # <--- НОВЫЙ СТАТУС
]

UNPAID_STATUS = "доставка не оплачена"

MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton("Отследить заказ")],
        [KeyboardButton("Мои адреса"), KeyboardButton("Мои подписки")],
        [KeyboardButton("Отмена")],
    ],
    resize_keyboard=True,
)

# корректный номер заказа, типа KR-12345 / CN12345
ORDER_ID_RE = re.compile(r"([A-ZА-Я]{1,3})[ \-–—]?\s?(\d{3,})", re.IGNORECASE)
# username строго с символом @
USERNAME_RE = re.compile(r"@([A-Za-z0-9_]{5,})")


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


def status_keyboard(cols: int = 2) -> InlineKeyboardMarkup:
    rows, row = [], []
    for i, s in enumerate(STATUSES):
        row.append(InlineKeyboardButton(s, callback_data=f"adm:pick_status_id:{i}"))
        if len(row) == cols:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Добавить заказ", callback_data="adm:add")],
            [InlineKeyboardButton("✏️ Изменить статус", callback_data="adm:update")],
            [InlineKeyboardButton("🗂 Последние заказы", callback_data="adm:list")],
            [InlineKeyboardButton("🔍 Найти заказ", callback_data="adm:find")],
            [InlineKeyboardButton("🔎 Адрес по username", callback_data="adm:addrbyuser")],
            [InlineKeyboardButton("📣 Напомнить об оплате", callback_data="adm:remind_unpaid")],  # НОВОЕ
            [InlineKeyboardButton("↩️ Выйти", callback_data="adm:back")],
        ]
    )

# ---------------------- Команды ----------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот SEABLUU для отслеживания заказов и адресов.",
        reply_markup=MAIN_KB,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Команды:\n"
        "• Отследить заказ — статус по номеру\n"
        "• Мои адреса — добавить/изменить адрес\n"
        "• Мои подписки — список подписок\n"
        "• /admin — админ-панель (для админов)\n"
        "• /adminoff — выйти из админ-режима\n"
        "• В админ-режиме можно прислать @username или список @username — пришлю адрес(а)."
    )


async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await (update.message or update.callback_query.message).reply_text(
        "Админ-панель SEABLUU:", reply_markup=admin_kb()
    )
    context.user_data.pop("adm_mode", None)
    context.user_data.pop("adm_buf", None)


async def admin_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("adm_mode", None)
    context.user_data.pop("adm_buf", None)
    await update.message.reply_text("Админ-режим выключен.", reply_markup=MAIN_KB)


# ---------------------- Пользовательские сценарии ----------------------


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()
    text = raw.lower()

    # ===== ADMIN FLOW =====
    if update.effective_user.id in ADMIN_IDS:
        # быстрый выход
        if text in {"отмена", "cancel", "/cancel", "/adminoff"}:
            context.user_data.pop("adm_mode", None)
            context.user_data.pop("adm_buf", None)
            await update.message.reply_text("Ок, вышли из админ-режима.", reply_markup=MAIN_KB)
            return

        a_mode = context.user_data.get("adm_mode")

        # --- Добавление заказа (мастер) ---
        if a_mode == "add_order_id":
            context.user_data["adm_buf"] = {"order_id": raw}
            context.user_data["adm_mode"] = "add_order_client"
            await update.message.reply_text("Имя клиента:")
            return

        if a_mode == "add_order_client":
            context.user_data["adm_buf"]["client_name"] = raw
            context.user_data["adm_mode"] = "add_order_country"
            await update.message.reply_text("Страна/склад (CN или KR):")
            return

        if a_mode == "add_order_country":
            country = raw.upper()
            if country not in ("CN", "KR"):
                await update.message.reply_text("Введи 'CN' (Китай) или 'KR' (Корея):")
                return
            context.user_data["adm_buf"]["country"] = country
            context.user_data["adm_mode"] = "add_order_status"
            await update.message.reply_text(
                "Выбери стартовый статус кнопкой ниже или напиши точный:",
                reply_markup=status_keyboard(2),
            )
            return

        if a_mode == "add_order_status":
            if not is_valid_status(raw, STATUSES):
                await update.message.reply_text(
                    "Выбери статус кнопкой ниже или напиши точный:",
                    reply_markup=status_keyboard(2),
                )
                return
            context.user_data["adm_buf"]["status"] = raw.strip()
            context.user_data["adm_mode"] = "add_order_note"
            await update.message.reply_text("Примечание (или '-' если нет):")
            return

        if a_mode == "add_order_note":
            buf = context.user_data.get("adm_buf", {})
            buf["note"] = raw if raw != "-" else ""
            try:
                sheets.add_order(
                    {
                        "order_id": buf["order_id"],
                        "client_name": buf.get("client_name", ""),
                        "country": buf.get("country", ""),
                        "status": buf.get("status", "выкуплен"),
                        "note": buf.get("note", ""),
                    }
                )
                await update.message.reply_text(
                    f"Заказ *{buf['order_id']}* добавлен ✅", parse_mode="Markdown"
                )
            except Exception as e:
                await update.message.reply_text(f"Ошибка: {e}")
            finally:
                context.user_data.pop("adm_mode", None)
                context.user_data.pop("adm_buf", None)
            return

        # --- Изменение статуса (ввод order_id) ---
        if a_mode == "upd_order_id":
            parsed_id = extract_order_id(raw)
            if not parsed_id:
                await update.message.reply_text("Не похоже на номер. Пример: KR-12345")
                return
            if not sheets.get_order(parsed_id):
                await update.message.reply_text("Заказ не найден.")
                context.user_data.pop("adm_mode", None)
                context.user_data.pop("adm_buf", None)
                return
            context.user_data.setdefault("adm_buf", {})["order_id"] = parsed_id
            context.user_data["adm_mode"] = "upd_pick_status"
            rows = [
                [InlineKeyboardButton(s, callback_data=f"adm:set_status_id:{i}")]
                for i, s in enumerate(STATUSES)
            ]
            await update.message.reply_text("Выберите статус:", reply_markup=InlineKeyboardMarkup(rows))
            return

        # --- Поиск и вывод полной карточки заказа ---
        if a_mode == "find_order":
            parsed_id = extract_order_id(raw) or raw
            order = sheets.get_order(parsed_id)
            if not order:
                await update.message.reply_text("Заказ не найден.")
                context.user_data.pop("adm_mode", None)
                return

            order_id = order.get("order_id", parsed_id)
            client_name = order.get("client_name", "—")
            status = order.get("status", "—")
            note = order.get("note", "—")
            country = order.get("country", order.get("origin", "—"))
            origin = order.get("origin")
            updated_at = order.get("updated_at")

            lines = [
                f"*order_id:* `{order_id}`",
                f"*client_name:* {client_name}",
                f"*status:* {status}",
                f"*note:* {note}",
                f"*country:* {country}",
            ]
            if origin and origin != country:
                lines.append(f"*origin:* {origin}")
            if updated_at:
                lines.append(f"*updated_at:* {updated_at}")

            await update.message.reply_markdown("\n".join(lines))
            context.user_data.pop("adm_mode", None)
            return

        # --- Поиск адресов по username (ввод списка) ---
        if a_mode == "adm_addr_usernames":
            usernames = [m.group(1) for m in USERNAME_RE.finditer(raw)]
            if not usernames:
                await update.message.reply_text(
                    "Пришли @username или несколько через пробел/запятую/новую строку."
                )
                return
            rows = sheets.get_addresses_by_usernames(usernames)
            by_user = {str(r.get("username", "")).lower(): r for r in rows}
            reply = []
            for u in usernames:
                rec = by_user.get(u.lower())
                if not rec:
                    reply.append(f"@{u}: адрес не найден")
                else:
                    reply.append(
                        f"@{u}\n"
                        f"ФИО: {rec.get('full_name','')}\n"
                        f"Телефон: {rec.get('phone','')}\n"
                        f"Город: {rec.get('city','')}\n"
                        f"Адрес: {rec.get('address','')}\n"
                        f"Индекс: {rec.get('postcode','')}"
                    )
            await update.message.reply_text("\n\n".join(reply))
            context.user_data.pop("adm_mode", None)
            return

        # --- Ручная рассылка «Напомнить об оплате» (ввод order_id) ---
        if a_mode == "adm_remind_unpaid_order":
            parsed_id = extract_order_id(raw) or raw
            ok = await remind_unpaid_for_order(context.application, parsed_id)
            if ok:
                await update.message.reply_text(f"Рассылка по заказу *{parsed_id}* отправлена ✅", parse_mode="Markdown")
            else:
                await update.message.reply_text("Либо заказ не найден, либо нет получателей.")
            context.user_data.pop("adm_mode", None)
            return

        # --- Быстрый адрес по @username (вне мастеров) ---
        if "@" in raw and USERNAME_RE.search(raw) and not a_mode and not context.user_data.get("mode"):
            usernames = [m.group(1) for m in USERNAME_RE.finditer(raw)]
            rows = sheets.get_addresses_by_usernames(usernames)
            by_user = {str(r.get("username", "")).lower(): r for r in rows}
            reply = []
            for u in usernames:
                rec = by_user.get(u.lower())
                if not rec:
                    reply.append(f"@{u}: адрес не найден")
                else:
                    reply.append(
                        f"@{u}\n"
                        f"ФИО: {rec.get('full_name','')}\n"
                        f"Телефон: {rec.get('phone','')}\n"
                        f"Город: {rec.get('city','')}\n"
                        f"Адрес: {rec.get('address','')}\n"
                        f"Индекс: {rec.get('postcode','')}"
                    )
            await update.message.reply_text("\n\n".join(reply))
            return

        if a_mode:
            await update.message.reply_text("Жду действие в админ-режиме.", reply_markup=admin_kb())
            return

    # ===== USER FLOW =====
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

    mode = context.user_data.get("mode")
    if mode == "track":
        await query_status(update, context, raw)
        return

    # --- Мастер адреса ---
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
                "Нужно 11 цифр и обязательно с 8. Пример: 87001234567\n"
                "Введи номер ещё раз или нажми «Отмена»:"
            )
            return
        context.user_data["phone"] = normalized
        await update.message.reply_text("Город (пример: Астана):")
        context.user_data["mode"] = "add_address_city"
        return

    if mode == "add_address_city":
        context.user_data["city"] = raw
        await update.message.reply_text("Адрес (свободный формат):")
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

    # Ничего не подошло
    await update.message.reply_text(
        "Не понял. Нажмите кнопку ниже или введите номер заказа. Для выхода — «Отмена».",
        reply_markup=MAIN_KB,
    )


# ---------------------- Карточки / подписки / адреса ----------------------


async def query_status(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str):
    order_id = extract_order_id(order_id) or order_id
    order = sheets.get_order(order_id)
    if not order:
        await update.message.reply_text("Такой заказ не найден. Проверьте номер или повторите позже.")
        return
    status = order.get("status") or "статус не указан"
    origin = order.get("origin") or ""
    txt = f"Заказ *{order_id}*\nСтатус: *{status}*"
    if origin:
        txt += f"\nСтрана/источник: {origin}"

    if sheets.is_subscribed(update.effective_user.id, order_id):
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔕 Отписаться", callback_data=f"unsub:{order_id}")]]
        )
    else:
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔔 Подписаться на обновления", callback_data=f"sub:{order_id}")]]
        )
    await update.message.reply_markdown(txt, reply_markup=kb)
    context.user_data["mode"] = None


async def show_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    addrs = sheets.list_addresses(update.effective_user.id)
    if not addrs:
        await update.message.reply_text(
            "У вас пока нет адреса. Хотите добавить?",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("➕ Добавить адрес", callback_data="addr:add")]]
            ),
        )
        return
    lines = []
    for a in addrs:
        lines.append(
            f"• {a['full_name']}, {a['phone']}, {a['city']}, {a['address']}, {a['postcode']}"
        )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✏️ Изменить адрес", callback_data="addr:add")],
            [InlineKeyboardButton("🗑 Удалить адрес", callback_data="addr:del")],
        ]
    )
    await update.message.reply_text("Ваш адрес доставки:\n" + "\n".join(lines), reply_markup=kb)


async def save_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    sheets.upsert_address(
        user_id=u.id,
        username=u.username or "",
        full_name=context.user_data.get("full_name", ""),
        phone=context.user_data.get("phone", ""),
        city=context.user_data.get("city", ""),
        address=context.user_data.get("address", ""),
        postcode=context.user_data.get("postcode", ""),
    )
    # автоподписка: если пользователь фигурирует в note каких-то заказов
    try:
        username = (u.username or "").strip()
        if username:
            rel_orders = sheets.find_orders_for_username(username)
            for oid in rel_orders:
                try:
                    sheets.subscribe(u.id, oid)
                except Exception:
                    pass
    except Exception as e:
        logging.warning(f"auto-subscribe on address save failed: {e}")

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
    txt_lines, kb_rows = [], []
    for s in subs:
        last = s.get("last_sent_status", "—")
        order_id = s["order_id"]
        txt_lines.append(f"• {order_id} (последний статус: {last})")
        kb_rows.append([InlineKeyboardButton(f"🗑 Отписаться от {order_id}", callback_data=f"unsub:{order_id}")])
    await update.message.reply_text("Ваши подписки:\n" + "\n".join(txt_lines), reply_markup=InlineKeyboardMarkup(kb_rows))


async def notify_subscribers(application, order_id: str, new_status: str):
    """
    Стандартная рассылка подписчикам при любом изменении статуса.
    Плюс, если статус == 'доставка не оплачена' — отдельная рассылка всем @username из примечания.
    """
    subs = sheets.get_all_subscriptions()
    if subs:
        targets = [s for s in subs if str(s.get("order_id")) == str(order_id)]
        for s in targets:
            uid = int(s["user_id"])
            try:
                await application.bot.send_message(
                    chat_id=uid,
                    text=f"Обновление по заказу *{order_id}*\nНовый статус: *{new_status}*",
                    parse_mode="Markdown",
                )
                sheets.set_last_sent_status(uid, order_id, new_status)
            except Exception as e:
                logging.warning(f"notify_subscribers fail to {uid}: {e}")

    # Дополнительно: если статус = "доставка не оплачена" — пингуем всех из примечания
    if (new_status or "").strip().lower() == UNPAID_STATUS:
        await remind_unpaid_for_order(application, order_id)


# ---------- Напоминания об оплате ----------

def _usernames_from_note(note: str) -> list[str]:
    return re.findall(r"@([A-Za-z0-9_]{5,})", note or "")

async def remind_unpaid_for_order(application, order_id: str) -> bool:
    """
    Собирает всех @user из note заказа, находит их user_id по листу addresses,
    подписывает на заказ (если надо) и шлёт личное сообщение «оплатите доставку».
    Возвращает True, если были получатели.
    """
    order = sheets.get_order(order_id)
    if not order:
        return False
    note = order.get("note") or ""
    usernames = _usernames_from_note(note)
    if not usernames:
        return False

    user_ids = sheets.get_user_ids_by_usernames(usernames)
    if not user_ids:
        return False

    sent = 0
    for uid in user_ids:
        try:
            sheets.subscribe(uid, order_id)
        except Exception:
            pass
        try:
            await application.bot.send_message(
                chat_id=int(uid),
                text=(
                    f"Заказ *{order_id}*\n"
                    f"Статус: *Доставка не оплачена*\n\n"
                    f"Пожалуйста, оплатите доставку. "
                    f"Если уже оплатили — проигнорируйте сообщение или свяжитесь с админом."
                ),
                parse_mode="Markdown",
            )
            sent += 1
        except Exception as e:
            logging.warning(f"payment reminder fail to {uid}: {e}")
    return sent > 0

async def remind_unpaid_daily(application) -> int:
    """
    Ежедневная рассылка по всем заказам, у которых статус == 'доставка не оплачена'.
    Возвращает количество заказов, по которым были отправки.
    """
    orders = sheets.list_orders_by_status(UNPAID_STATUS)
    total_orders = 0
    for o in orders:
        oid = o.get("order_id")
        if not oid:
            continue
        ok = await remind_unpaid_for_order(application, oid)
        if ok:
            total_orders += 1
    return total_orders

def register_daily_unpaid_job(application):
    """
    Заготовка для ежедневной рассылки. Вызовите эту функцию один раз на старте (напр., из webhook.on_startup).
    Требует APScheduler в зависимостях.
    """
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        scheduler = AsyncIOScheduler()
        # раз в сутки; first_run через ~1 час после старта
        scheduler.add_job(lambda: remind_unpaid_daily(application), "interval", days=1)
        scheduler.start()
        logging.info("Daily unpaid reminder job registered.")
    except Exception as e:
        logging.warning(f"Daily job not started: {e}")


# ---------------------- Callback Query ----------------------


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    # --- Кнопки адресов ---
    if data == "addr:add":
        context.user_data["mode"] = "add_address_fullname"
        await q.message.reply_text("Давайте добавим/обновим адрес.\nФИО:")
        return

    if data == "addr:del":
        ok = sheets.delete_address(update.effective_user.id)
        if ok:
            await q.message.reply_text("Адрес удалён ✅")
        else:
            await q.message.reply_text("Удалять нечего — адрес не найден.")
        return

    # --- Админ-меню ---
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

    if data == "adm:addrbyuser":
        if update.effective_user.id in ADMIN_IDS:
            context.user_data["adm_mode"] = "adm_addr_usernames"
            await q.message.reply_text("Пришли @username или несколько через пробел/запятую/новую строку.")
        return

    # НОВОЕ: ручной пуш должникам
    if data == "adm:remind_unpaid":
        if update.effective_user.id in ADMIN_IDS:
            context.user_data["adm_mode"] = "adm_remind_unpaid_order"
            await q.message.reply_text("Введи *order_id* для рассылки неплательщикам:", parse_mode="Markdown")
        return

    # --- Подбор стартового статуса при добавлении заказа ---
    if data.startswith("adm:pick_status_id:"):
        if update.effective_user.id not in ADMIN_IDS:
            return
        try:
            idx = int(data.split("adm:pick_status_id:", 1)[1])
            status = STATUSES[idx]
        except Exception:
            await q.message.reply_text("Некорректный статус.")
            return
        context.user_data.setdefault("adm_buf", {})["status"] = status
        context.user_data["adm_mode"] = "add_order_note"
        await q.message.reply_text("Примечание (или '-' если нет):")
        return

    # --- Установка статуса при изменении ---
    if data.startswith("adm:set_status_id:"):
        if update.effective_user.id not in ADMIN_IDS:
            return
        try:
            idx = int(data.split("adm:set_status_id:", 1)[1])
            status = STATUSES[idx]
        except Exception:
            await q.message.reply_text("Некорректный статус.")
            return
        order_id = context.user_data.get("adm_buf", {}).get("order_id")
        ok = sheets.update_order_status(order_id, status)
        if ok:
            await q.message.reply_text(
                f"Статус *{order_id}* обновлён на: _{status}_ ✅", parse_mode="Markdown"
            )
            await notify_subscribers(context.application, order_id, status)
        else:
            await q.message.reply_text("Заказ не найден.")
        context.user_data.pop("adm_mode", None)
        context.user_data.pop("adm_buf", None)
        return

    # --- Подписка / отписка ---
    if data.startswith("sub:"):
        order_id = data.split(":", 1)[1]
        sheets.subscribe(update.effective_user.id, order_id)
        await q.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔕 Отписаться", callback_data=f"unsub:{order_id}")]]
            )
        )
        await q.message.reply_text("Готово! Буду присылать обновления по этому заказу 🔔")
        return

    if data.startswith("unsub:"):
        order_id = data.split(":", 1)[1]
        ok = sheets.unsubscribe(update.effective_user.id, order_id)
        await q.message.reply_text("Отписка выполнена." if ok else "Вы и так не были подписаны.")
        try:
            await q.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔔 Подписаться на обновления", callback_data=f"sub:{order_id}")]]
                )
            )
        except Exception:
            pass
        return


# ---------------------- Регистрация хендлеров ----------------------


def register_handlers(application):
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(CommandHandler("adminoff", admin_off))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
