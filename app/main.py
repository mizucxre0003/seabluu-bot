# app/main.py
import logging
import re
from typing import List, Tuple

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from . import sheets
from .config import ADMIN_IDS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------- Константы и утилиты ----------------------

STATUSES = [
    "выкуплен", "едет на адрес", "приехал на адрес (Китай)", "приехал на адрес (Корея)",
    "ожидает отправку в Казахстан", "отправлен в Казахстан (из Китая)", "отправлен в Казахстан (из Кореи)",
    "приехал к владельцу шопа в Астане", "сборка заказа по Казахстану",
    "собран и готов на доставку по Казахстану", "отправлен по Казахстану", "доставлен", "получен",
    "доставка не оплачена",
]

ORDER_ID_RE = re.compile(r"([A-ZА-Я]{1,3})[ \-–—]?\s?(\d{3,})", re.IGNORECASE)
USERNAME_RE = re.compile(r"@([A-Za-z0-9_]{5,})")

def extract_order_id(s: str) -> str | None:
    if not s:
        return None
    m = ORDER_ID_RE.search(s.strip())
    if not m:
        return None
    return f"{m.group(1).upper()}-{m.group(2)}"

def is_valid_status(s: str, statuses: list[str]) -> bool:
    return bool(s) and s.strip().lower() in {x.lower() for x in statuses}

def _is_admin(uid) -> bool:
    return uid in ADMIN_IDS or str(uid) in {str(x) for x in ADMIN_IDS}

# ---------------------- Клавиатуры ----------------------

MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton("Отследить разбор")],
        [KeyboardButton("Мои адреса"), KeyboardButton("Мои подписки")],
        [KeyboardButton("Отмена")],
    ],
    resize_keyboard=True,
)

ADMIN_MENU_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton("Добавить разбор"), KeyboardButton("Отследить разбор")],
        [KeyboardButton("Админ: Рассылка"), KeyboardButton("Админ: Адреса")],
        [KeyboardButton("Выйти из админ-панели")],
    ],
    resize_keyboard=True,
)

BROADCAST_MENU_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton("Уведомления всем должникам")],
        [KeyboardButton("Уведомления по ID разбора")],
        [KeyboardButton("Назад, в админ-панель")],
    ],
    resize_keyboard=True,
)

def status_keyboard(cols: int = 2) -> InlineKeyboardMarkup:
    rows, row = [], []
    for i, s in enumerate(STATUSES):
        row.append(InlineKeyboardButton(s, callback_data=f"adm:pick_status_id:{i}"))
        if len(row) == cols:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

# ------- participants UI -------

def _slice_page(items: List, page: int, per_page: int) -> Tuple[List, int]:
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    return items[start:start + per_page], total_pages

def build_participants_text(order_id: str, participants: List[dict], page: int, per_page: int) -> str:
    slice_, total_pages = _slice_page(participants, page, per_page)
    lines = [f"*Разбор* `{order_id}` — участники ({page+1}/{total_pages}):"]
    if not slice_:
        lines.append("_Список участников пуст._")
    for p in slice_:
        mark = "✅" if p["paid"] else "❌"
        lines.append(f"{mark} @{p['username']}")
    return "\n".join(lines)

def build_participants_kb(order_id: str, participants: List[dict], page: int, per_page: int) -> InlineKeyboardMarkup:
    slice_, total_pages = _slice_page(participants, page, per_page)
    rows = []
    for p in slice_:
        mark = "✅" if p["paid"] else "❌"
        rows.append([
            InlineKeyboardButton(f"{mark} @{p['username']}", callback_data=f"pp:toggle:{order_id}:{p['username']}")
        ])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("« Назад", callback_data=f"pp:page:{order_id}:{page-1}"))
    nav.append(InlineKeyboardButton("Обновить", callback_data=f"pp:refresh:{order_id}:{page}"))
    if (page + 1) * per_page < len(participants):
        nav.append(InlineKeyboardButton("Вперёд »", callback_data=f"pp:page:{order_id}:{page+1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)

# ---------------------- Команды ----------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await (update.message or update.callback_query.message).reply_text(
        "Привет! Я бот SEABLUU для отслеживания заказов и адресов.",
        reply_markup=MAIN_KB,
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Команды:\n"
        "• Отследить заказ — статус по номеру\n"
        "• Мои адреса — добавить/изменить адрес\n"
        "• Мои подписки — список подписок\n"
        "• /admin — админ-панель (для админов)"
    )

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    for k in ("adm_mode", "adm_buf", "awaiting_unpaid_order_id"):
        context.user_data.pop(k, None)
    await (update.message or update.callback_query.message).reply_text(
        "Админ-панель:", reply_markup=ADMIN_MENU_KB
    )

# ---------------------- Пользовательские сценарии ----------------------

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()
    text = raw.lower()

    # ===== ADMIN FLOW =====
    if _is_admin(update.effective_user.id):

        if text == "выйти из админ-панели":
            context.user_data.clear()
            await update.message.reply_text("Ок, вышли из админ-панели.", reply_markup=MAIN_KB)
            return

        if text == "добавить разбор":
            context.user_data["adm_mode"] = "add_order_id"
            context.user_data["adm_buf"] = {}
            await update.message.reply_text("Введи *order_id* (например: CN-12345):", parse_mode="Markdown")
            return

        if text == "админ: рассылка":
            await update.message.reply_text("Раздел «Рассылка»", reply_markup=BROADCAST_MENU_KB)
            return

        if text == "назад, в админ-панель":
            await admin_menu(update, context)
            return

        if text == "уведомления всем должникам":
            await broadcast_all_unpaid_text(update, context)
            return

        if text == "уведомления по id разбора":
            context.user_data["awaiting_unpaid_order_id"] = True
            context.user_data["adm_mode"] = "adm_remind_unpaid_order"
            await update.message.reply_text("Введи *order_id* для рассылки неплательщикам:", parse_mode="Markdown")
            return

        if text == "админ: адреса":
            context.user_data["adm_mode"] = "adm_addr_usernames"
            await update.message.reply_text("Пришли @username или несколько через пробел/запятую/новую строку.")
            return

        if text == "отследить разбор" and (context.user_data.get("adm_mode") is None):
            context.user_data["adm_mode"] = "find_order"
            await update.message.reply_text("Введи *order_id* для поиска:", parse_mode="Markdown")
            return

        # --- ветки мастеров ---
        a_mode = context.user_data.get("adm_mode")

        # Добавление заказа
        if a_mode == "add_order_id":
            context.user_data["adm_buf"] = {"order_id": raw}
            context.user_data["adm_mode"] = "add_order_client"
            await update.message.reply_text("Имя клиента (можно несколько @username):")
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
                # участники из client_name
                usernames = [m.group(1) for m in USERNAME_RE.finditer(buf.get("client_name", ""))]
                if usernames:
                    sheets.ensure_participants(buf["order_id"], usernames)
                await update.message.reply_text(f"Заказ *{buf['order_id']}* добавлен ✅", parse_mode="Markdown")
            except Exception as e:
                await update.message.reply_text(f"Ошибка: {e}")
            finally:
                for k in ("adm_mode", "adm_buf"):
                    context.user_data.pop(k, None)
            return

        # Поиск полной карточки заказа + управление оплатами
        if a_mode == "find_order":
            parsed_id = extract_order_id(raw) or raw
            order = sheets.get_order(parsed_id)
            if not order:
                await update.message.reply_text("Заказ не найден.")
                context.user_data.pop("adm_mode", None)
                return

            # карточка
            order_id = order.get("order_id", parsed_id)
            client_name = order.get("client_name", "—")
            status = order.get("status", "—")
            note = order.get("note", "—")
            country = order.get("country", order.get("origin", "—"))
            origin = order.get("origin")
            updated_at = order.get("updated_at")

            head = [
                f"*order_id:* `{order_id}`",
                f"*client_name:* {client_name}",
                f"*status:* {status}",
                f"*note:* {note}",
                f"*country:* {country}",
            ]
            if origin and origin != country:
                head.append(f"*origin:* {origin}")
            if updated_at:
                head.append(f"*updated_at:* {updated_at}")
            head_text = "\n".join(head)

            # участники
            participants = sheets.get_participants(order_id)
            page = 0
            per_page = 8
            part_text = build_participants_text(order_id, participants, page, per_page)
            kb = build_participants_kb(order_id, participants, page, per_page)

            await update.message.reply_markdown(head_text)
            await update.message.reply_markdown(part_text, reply_markup=kb)
            context.user_data.pop("adm_mode", None)
            return

        # Адреса по username
        if a_mode == "adm_addr_usernames":
            usernames = [m.group(1) for m in USERNAME_RE.finditer(raw)]
            if not usernames:
                await update.message.reply_text("Пришли @username или несколько через пробел/запятую/новую строку.")
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

        # Ручная рассылка по одному order_id
        if a_mode == "adm_remind_unpaid_order" and context.user_data.get("awaiting_unpaid_order_id"):
            parsed_id = extract_order_id(raw) or raw
            ok = await remind_unpaid_for_order(context.application, parsed_id)
            if ok:
                await update.message.reply_text(f"Рассылка по заказу *{parsed_id}* отправлена ✅", parse_mode="Markdown")
            else:
                await update.message.reply_text("Либо заказ не найден, либо нет получателей.")
            for k in ("adm_mode", "awaiting_unpaid_order_id"):
                context.user_data.pop(k, None)
            return

    # ===== USER FLOW =====
    if text in {"отмена", "cancel"}:
        context.user_data["mode"] = None
        await update.message.reply_text("Ок, отменил. Что дальше?", reply_markup=MAIN_KB)
        return

    if text == "отследить разбор":
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
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔕 Отписаться", callback_data=f"unsub:{order_id}")]])
    else:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔔 Подписаться на обновления", callback_data=f"sub:{order_id}")]])
    await update.message.reply_markdown(txt, reply_markup=kb)
    context.user_data["mode"] = None

async def show_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    addrs = sheets.list_addresses(update.effective_user.id)
    if not addrs:
        await update.message.reply_text(
            "У вас пока нет адреса. Хотите добавить?",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Добавить адрес", callback_data="addr:add")]]),
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

# ---------- Рассылки ----------

async def remind_unpaid_for_order(application, order_id: str) -> bool:
    order = sheets.get_order(order_id)
    if not order:
        return False
    unpaid_usernames = sheets.get_unpaid_usernames(order_id)
    if not unpaid_usernames:
        return False
    user_ids = sheets.get_user_ids_by_usernames([u.lower() for u in unpaid_usernames])
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
                    f"Пожалуйста, оплатите доставку. Если уже оплатили — проигнорируйте."
                ),
                parse_mode="Markdown",
            )
            sent += 1
        except Exception as e:
            logger.warning(f"payment reminder fail to {uid}: {e}")
    return sent > 0

async def broadcast_all_unpaid_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    grouped = sheets.get_all_unpaid_grouped()
    total_orders = len(grouped)
    total_ok = 0
    total_fail = 0
    report_lines: List[str] = []
    for order_id, users in grouped.items():
        user_ids = sheets.get_user_ids_by_usernames([u.lower() for u in users])
        ok = 0; fail = 0
        for uid in user_ids:
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text=f"Напоминание по доставке: неоплаченный разбор {order_id}. Пожалуйста, оплатите."
                )
                ok += 1
            except Exception:
                fail += 1
        total_ok += ok; total_fail += fail
        report_lines.append(f"{order_id}: ✅ {ok} ❌ {fail}")
    summary = "\n".join([
        "📣 Уведомления всем должникам — итог",
        f"Разборов: {total_orders}",
        f"Успешно: {total_ok}",
        f"Ошибок: {total_fail}",
        "",
        *report_lines,
    ])
    await context.bot.send_message(chat_id=update.effective_chat.id, text=summary)

# ---------- CallbackQuery ----------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    # адреса
    if data == "addr:add":
        context.user_data["mode"] = "add_address_fullname"
        await q.message.reply_text("Давайте добавим/обновим адрес.\nФИО:")
        return

    if data == "addr:del":
        ok = sheets.delete_address(update.effective_user.id)
        await q.message.reply_text("Адрес удалён ✅" if ok else "Удалять нечего — адрес не найден.")
        return

    # выбор стартового статуса при добавлении
    if data.startswith("adm:pick_status_id:"):
        try:
            idx = int(data.split("adm:pick_status_id:", 1)[1])
            status = STATUSES[idx]
        except Exception:
            await q.message.reply_text("Некорректный статус."); return
        context.user_data.setdefault("adm_buf", {})["status"] = status
        context.user_data["adm_mode"] = "add_order_note"
        await q.message.reply_text("Примечание (или '-' если нет):")
        return

    # подписка/отписка у клиента
    if data.startswith("sub:"):
        order_id = data.split(":", 1)[1]
        sheets.subscribe(update.effective_user.id, order_id)
        try:
            await q.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔕 Отписаться", callback_data=f"unsub:{order_id}")]])
            )
        except Exception:
            pass
        await q.message.reply_text("Готово! Буду присылать обновления по этому заказу 🔔")
        return

    if data.startswith("unsub:"):
        order_id = data.split(":", 1)[1]
        sheets.unsubscribe(update.effective_user.id, order_id)
        await q.message.reply_text("Отписка выполнена.")
        try:
            await q.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔔 Подписаться на обновления", callback_data=f"sub:{order_id}")]])
            )
        except Exception:
            pass
        return

    # ---- управление оплатой участников ----
    if data.startswith("pp:toggle:"):
        # pp:toggle:{order_id}:{username}
        _, _, order_id, username = data.split(":", 3)
        sheets.toggle_participant_paid(order_id, username)
        # обновить список
        participants = sheets.get_participants(order_id)
        # текущая страница из подписи сообщения (передаем в refresh ниже), попробуем получить из data в кнопке 'refresh'
        # упростим: всегда рисуем с page=0
        page = 0
        per_page = 8
        txt = build_participants_text(order_id, participants, page, per_page)
        kb = build_participants_kb(order_id, participants, page, per_page)
        try:
            await q.message.edit_text(txt, reply_markup=kb, parse_mode="Markdown")
        except Exception:
            # если не удалось отредактировать (например, слишком старое сообщение) — просто отправим новое
            await q.message.reply_markdown(txt, reply_markup=kb)
        return

    if data.startswith("pp:refresh:"):
        # pp:refresh:{order_id}:{page}
        parts = data.split(":")
        order_id = parts[2]
        page = int(parts[3]) if len(parts) > 3 else 0
        participants = sheets.get_participants(order_id)
        per_page = 8
        txt = build_participants_text(order_id, participants, page, per_page)
        kb = build_participants_kb(order_id, participants, page, per_page)
        await q.message.edit_text(txt, reply_markup=kb, parse_mode="Markdown")
        return

    if data.startswith("pp:page:"):
        # pp:page:{order_id}:{page}
        _, _, order_id, page_s = data.split(":")
        page = int(page_s)
        participants = sheets.get_participants(order_id)
        per_page = 8
        txt = build_participants_text(order_id, participants, page, per_page)
        kb = build_participants_kb(order_id, participants, page, per_page)
        await q.message.edit_text(txt, reply_markup=kb, parse_mode="Markdown")
        return

# ---------------------- Регистрация ----------------------

def register_handlers(application):
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))

def register_admin_ui(application):
    # no-op: всё обрабатывает handle_text
    return
