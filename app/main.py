# app/main.py
import logging
import re
from typing import List, Tuple, Dict

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
    "выкуплен",
    "едет на адрес",
    "приехал на адрес (Китай)",
    "приехал на адрес (Корея)",
    "ожидает отправку в Казахстан",
    "отправлен в Казахстан (из Китая)",
    "отправлен в Казахстан (из Кореи)",
    "приехал к владельцу шопа в Астане",
    "сборка заказа по Казахстану",
    "собран и готов на доставку по Казахстану",
    "отправлен по Казахстану",
    "доставлен",
    "получен",
    "доставка не оплачена",
]
UNPAID_STATUS = "доставка не оплачена"

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
        [KeyboardButton("Отчёты"), KeyboardButton("Выйти из админ-панели")],
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

ADMIN_ADDR_MENU_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton("Выгрузить адреса")],
        [KeyboardButton("Изменить адрес по username")],
        [KeyboardButton("Назад, в админ-панель")],
    ],
    resize_keyboard=True,
)

REPORTS_MENU_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton("Выгрузить разборы админа")],
        [KeyboardButton("Отчёт по должникам")],
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

# ------- participants UI (список с переключателями) -------

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
        mark = "✅" if p.get("paid") else "❌"
        lines.append(f"{mark} @{p.get('username')}")
    return "\n".join(lines)

def build_participants_kb(order_id: str, participants: List[dict], page: int, per_page: int) -> InlineKeyboardMarkup:
    slice_, total_pages = _slice_page(participants, page, per_page)
    rows = []
    for p in slice_:
        mark = "✅" if p.get("paid") else "❌"
        rows.append([InlineKeyboardButton(f"{mark} @{p.get('username')}", callback_data=f"pp:toggle:{order_id}:{p.get('username')}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("« Назад", callback_data=f"pp:page:{order_id}:{page-1}"))
    nav.append(InlineKeyboardButton("Обновить", callback_data=f"pp:refresh:{order_id}:{page}"))
    if (page + 1) * per_page < len(participants):
        nav.append(InlineKeyboardButton("Вперёд »", callback_data=f"pp:page:{order_id}:{page+1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)

def order_card_kb(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✏️ Изменить статус", callback_data=f"adm:status_menu:{order_id}")],
        ]
    )

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

        if text == "отчёты":
            await update.message.reply_text("Раздел «Отчёты»", reply_markup=REPORTS_MENU_KB)
            return

        if text == "админ: рассылка":
            await update.message.reply_text("Раздел «Рассылка»", reply_markup=BROADCAST_MENU_KB)
            return

        if text == "админ: адреса":
            await update.message.reply_text("Раздел «Адреса»", reply_markup=ADMIN_ADDR_MENU_KB)
            return

        if text == "назад, в админ-панель":
            await admin_menu(update, context)
            return

        # --- Рассылка
        if text == "уведомления всем должникам":
            await broadcast_all_unpaid_text(update, context)
            return

        if text == "уведомления по id разбора":
            context.user_data["adm_mode"] = "adm_remind_unpaid_order"
            await update.message.reply_text("Введи *order_id* для рассылки неплательщикам:", parse_mode="Markdown")
            return

        # --- Адреса (подменю)
        if text == "выгрузить адреса":
            context.user_data["adm_mode"] = "adm_export_addrs"
            await update.message.reply_text("Пришли список @username (через пробел/запятую/новые строки):")
            return

        if text == "изменить адрес по username":
            context.user_data["adm_mode"] = "adm_edit_addr_username"
            await update.message.reply_text("Пришли @username пользователя, чей адрес нужно изменить:")
            return

        # --- Отчёты (подменю)
        if text == "выгрузить разборы админа":
            context.user_data["adm_mode"] = "adm_export_orders_by_note"
            await update.message.reply_text("Пришли метку/слово из *note*, по которому помечены твои разборы:", parse_mode="Markdown")
            return

        if text == "отчёт по должникам":
            await report_unpaid(update, context)
            return

        # --- Отследить разбор
        if text == "отследить разбор" and (context.user_data.get("adm_mode") is None):
            context.user_data["adm_mode"] = "find_order"
            await update.message.reply_text("Введи *order_id* для поиска:", parse_mode="Markdown")
            return

        # --- Мастера/вводы ---
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
            await update.message.reply_text("Выбери стартовый статус кнопкой ниже или напиши точный:", reply_markup=status_keyboard(2))
            return

        if a_mode == "add_order_status":
            if not is_valid_status(raw, STATUSES):
                await update.message.reply_text("Выбери статус кнопкой ниже или напиши точный:", reply_markup=status_keyboard(2))
                return
            context.user_data["adm_buf"]["status"] = raw.strip()
            context.user_data["adm_mode"] = "add_order_note"
            await update.message.reply_text("Примечание (или '-' если нет):")
            return

        if a_mode == "add_order_note":
            buf = context.user_data.get("adm_buf", {})
            buf["note"] = raw if raw != "-" else ""
            try:
                sheets.add_order({
                    "order_id": buf["order_id"],
                    "client_name": buf.get("client_name", ""),
                    "country": buf.get("country", ""),
                    "status": buf.get("status", "выкуплен"),
                    "note": buf.get("note", ""),
                })
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

        # Поиск и карточка + участники + кнопка смены статуса
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

            await update.message.reply_markdown("\n".join(head), reply_markup=order_card_kb(order_id))

            # участники
            participants = sheets.get_participants(order_id)
            page = 0; per_page = 8
            part_text = build_participants_text(order_id, participants, page, per_page)
            kb = build_participants_kb(order_id, participants, page, per_page)
            await update.message.reply_markdown(part_text, reply_markup=kb)

            context.user_data.pop("adm_mode", None)
            return

        # Ручная рассылка по одному order_id
        if a_mode == "adm_remind_unpaid_order":
            parsed_id = extract_order_id(raw) or raw
            ok = await remind_unpaid_for_order(context.application, parsed_id)
            await update.message.reply_text(
                f"Рассылка по заказу *{parsed_id}* отправлена ✅" if ok else "Либо заказ не найден, либо нет получателей.",
                parse_mode="Markdown",
            )
            context.user_data.pop("adm_mode", None)
            return

        # Выгрузить адреса (по списку username)
        if a_mode == "adm_export_addrs":
            usernames = [m.group(1) for m in USERNAME_RE.finditer(raw)]
            if not usernames:
                await update.message.reply_text("Пришли список @username.")
                return
            rows = sheets.get_addresses_by_usernames(usernames)
            if not rows:
                await update.message.reply_text("Адреса не найдены.")
            else:
                lines = []
                for r in rows:
                    lines.append(
                        f"@{r.get('username','')}\n"
                        f"ФИО: {r.get('full_name','')}\n"
                        f"Телефон: {r.get('phone','')}\n"
                        f"Город: {r.get('city','')}\n"
                        f"Адрес: {r.get('address','')}\n"
                        f"Индекс: {r.get('postcode','')}\n"
                        "—"
                    )
                await update.message.reply_text("\n".join(lines))
            context.user_data.pop("adm_mode", None)
            return

        # Изменить адрес по username — шаг 1: получить username и загрузить текущие поля
        if a_mode == "adm_edit_addr_username":
            usernames = [m.group(1) for m in USERNAME_RE.finditer(raw)]
            if not usernames:
                await update.message.reply_text("Пришли @username.")
                return
            uname = usernames[0].lower()
            ids = sheets.get_user_ids_by_usernames([uname])
            if not ids:
                await update.message.reply_text("Пользователь не найден по username (нет записи в адресах).")
                context.user_data.pop("adm_mode", None)
                return
            context.user_data["adm_mode"] = "adm_edit_addr_fullname"
            context.user_data["adm_buf"] = {"edit_user_id": ids[0], "edit_username": uname}
            await update.message.reply_text("ФИО (новое значение):")
            return

        if a_mode == "adm_edit_addr_fullname":
            context.user_data.setdefault("adm_buf", {})["full_name"] = raw
            context.user_data["adm_mode"] = "adm_edit_addr_phone"
            await update.message.reply_text("Телефон:")
            return

        if a_mode == "adm_edit_addr_phone":
            context.user_data["adm_buf"]["phone"] = raw
            context.user_data["adm_mode"] = "adm_edit_addr_city"
            await update.message.reply_text("Город:")
            return

        if a_mode == "adm_edit_addr_city":
            context.user_data["adm_buf"]["city"] = raw
            context.user_data["adm_mode"] = "adm_edit_addr_address"
            await update.message.reply_text("Адрес:")
            return

        if a_mode == "adm_edit_addr_address":
            context.user_data["adm_buf"]["address"] = raw
            context.user_data["adm_mode"] = "adm_edit_addr_postcode"
            await update.message.reply_text("Почтовый индекс:")
            return

        if a_mode == "adm_edit_addr_postcode":
            buf = context.user_data.get("adm_buf", {})
            try:
                sheets.upsert_address(
                    user_id=buf["edit_user_id"],
                    username=buf.get("edit_username",""),
                    full_name=buf.get("full_name",""),
                    phone=buf.get("phone",""),
                    city=buf.get("city",""),
                    address=buf.get("address",""),
                    postcode=raw,
                )
                await update.message.reply_text("Адрес обновлён ✅")
            except Exception as e:
                await update.message.reply_text(f"Ошибка: {e}")
            finally:
                context.user_data.pop("adm_mode", None)
                context.user_data.pop("adm_buf", None)
            return

        # Выгрузить разборы по note
        if a_mode == "adm_export_orders_by_note":
            marker = raw.strip()
            if not marker:
                await update.message.reply_text("Пришли метку/слово для поиска в note.")
                return
            orders = sheets.get_orders_by_note(marker)  # реализуй в sheets: фильтр по подстроке в note
            if not orders:
                await update.message.reply_text("Ничего не найдено.")
            else:
                lines = []
                for o in orders:
                    lines.append(
                        f"*order_id:* `{o.get('order_id','')}`\n"
                        f"*client_name:* {o.get('client_name','')}\n"
                        f"*phone:* {o.get('phone','')}\n"
                        f"*origin:* {o.get('origin','')}\n"
                        f"*status:* {o.get('status','')}\n"
                        f"*note:* {o.get('note','')}\n"
                        f"*country:* {o.get('country','')}\n"
                        f"*updated_at:* {o.get('updated_at','')}\n"
                        "—"
                    )
                await update.message.reply_markdown("\n".join(lines))
            context.user_data.pop("adm_mode", None)
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

    # ====== Мастер адреса (как раньше) ======
    if mode == "add_address_fullname":
        context.user_data["full_name"] = raw
        await update.message.reply_text("Телефон (пример: 87001234567):")
        context.user_data["mode"] = "add_address_phone"
        return

    if mode == "add_address_phone":
        normalized = raw.strip().replace(" ", "").replace("-", "")
        if normalized.startswith("+7"): normalized = "8" + normalized[2:]
        elif normalized.startswith("7"): normalized = "8" + normalized[1:]
        if not (normalized.isdigit() and len(normalized) == 11 and normalized.startswith("8")):
            await update.message.reply_text("Нужно 11 цифр и обязательно с 8. Пример: 87001234567\nВведи номер ещё раз или нажми «Отмена».")
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

# ---------------------- Клиент: статус/подписки/адреса ----------------------

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
        lines.append(f"• {a['full_name']}, {a['phone']}, {a['city']}, {a['address']}, {a['postcode']}")
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
    # автоподписка на свои разборы (если есть username в participants)
    try:
        if u.username:
            for oid in sheets.find_orders_for_username(u.username):
                try: sheets.subscribe(u.id, oid)
                except Exception: pass
    except Exception as e:
        logger.warning(f"auto-subscribe failed: {e}")

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

# ---------- Уведомления подписчикам ----------

async def notify_subscribers(application, order_id: str, new_status: str):
    """Шлём всем подписчикам заказа. last_sent_status обновляем в таблице."""
    try:
        subs_all = sheets.get_all_subscriptions()
        targets = [s for s in subs_all if str(s.get("order_id")) == str(order_id)]
    except Exception:
        # fallback: рассылка по участникам разбора
        usernames = sheets.get_unpaid_usernames(order_id) + [p.get("username") for p in sheets.get_participants(order_id)]
        user_ids = list(set(sheets.get_user_ids_by_usernames([u for u in usernames if u])))
        targets = [{"user_id": uid, "order_id": order_id} for uid in user_ids]

    for s in targets:
        uid = int(s["user_id"])
        try:
            await application.bot.send_message(
                chat_id=uid,
                text=f"Обновление по заказу *{order_id}*\nНовый статус: *{new_status}*",
                parse_mode="Markdown",
            )
            try: sheets.set_last_sent_status(uid, order_id, new_status)
            except Exception: pass
        except Exception as e:
            logger.warning(f"notify_subscribers fail to {uid}: {e}")

# ---------- Напоминания об оплате ----------

async def remind_unpaid_for_order(application, order_id: str) -> bool:
    order = sheets.get_order(order_id)
    if not order:
        return False
    unpaid_usernames = sheets.get_unpaid_usernames(order_id)
    if not unpaid_usernames:
        return False
    user_ids = sheets.get_user_ids_by_usernames(unpaid_usernames)
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

async def report_unpaid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    grouped = sheets.get_all_unpaid_grouped()
    if not grouped:
        await update.message.reply_text("Должников не найдено — красота!")
        return
    lines = ["📋 Отчёт по должникам:"]
    for oid, users in grouped.items():
        ulist = ", ".join([f"@{u}" for u in users])
        lines.append(f"• {oid}: {ulist if ulist else '—'}")
    await update.message.reply_text("\n".join(lines))

async def broadcast_all_unpaid_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    grouped = sheets.get_all_unpaid_grouped()
    total_orders = len(grouped)
    total_ok = 0
    total_fail = 0
    report_lines: List[str] = []
    for order_id, users in grouped.items():
        user_ids = sheets.get_user_ids_by_usernames(users)
        ok = 0; fail = 0
        for uid in user_ids:
            try:
                await context.bot.send_message(chat_id=uid, text=f"Напоминание: неоплаченный разбор {order_id}. Пожалуйста, оплатите.")
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

    # адреса (клиент)
    if data == "addr:add":
        context.user_data["mode"] = "add_address_fullname"
        await q.message.reply_text("Давайте добавим/обновим адрес.\nФИО:")
        return

    if data == "addr:del":
        ok = sheets.delete_address(update.effective_user.id)
        await q.message.reply_text("Адрес удалён ✅" if ok else "Удалять нечего — адрес не найден.")
        return

    # смена статуса из карточки заказа
    if data.startswith("adm:status_menu:"):
        if not _is_admin(update.effective_user.id): return
        order_id = data.split(":", 2)[2]
        rows = [[InlineKeyboardButton(s, callback_data=f"adm:set_status_val:{order_id}:{i}")] for i, s in enumerate(STATUSES)]
        await q.message.reply_text("Выберите новый статус:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("adm:set_status_val:"):
        if not _is_admin(update.effective_user.id): return
        _, _, order_id, idx_s = data.split(":")
        try:
            idx = int(idx_s); new_status = STATUSES[idx]
        except Exception:
            await q.message.reply_text("Некорректный выбор статуса.")
            return
        ok = sheets.update_order_status(order_id, new_status)
        if ok:
            await q.message.reply_text(f"Статус *{order_id}* обновлён на: _{new_status}_ ✅", parse_mode="Markdown")
            await notify_subscribers(context.application, order_id, new_status)
        else:
            await q.message.reply_text("Заказ не найден.")
        return

    # подписка/отписка (клиент)
    if data.startswith("sub:"):
        order_id = data.split(":", 1)[1]
        sheets.subscribe(update.effective_user.id, order_id)
        try:
            await q.edit_message_reply_markup(InlineKeyboardMarkup([[InlineKeyboardButton("🔕 Отписаться", callback_data=f"unsub:{order_id}")]]))
        except Exception:
            pass
        await q.message.reply_text("Готово! Буду присылать обновления по этому заказу 🔔")
        return

    if data.startswith("unsub:"):
        order_id = data.split(":", 1)[1]
        sheets.unsubscribe(update.effective_user.id, order_id)
        await q.message.reply_text("Отписка выполнена.")
        try:
            await q.edit_message_reply_markup(InlineKeyboardMarkup([[InlineKeyboardButton("🔔 Подписаться на обновления", callback_data=f"sub:{order_id}")]]))
        except Exception:
            pass
        return

    # управление оплатой участников (тумблеры)
    if data.startswith("pp:toggle:"):
        _, _, order_id, username = data.split(":", 3)
        sheets.toggle_participant_paid(order_id, username)
        participants = sheets.get_participants(order_id)
        page = 0; per_page = 8
        txt = build_participants_text(order_id, participants, page, per_page)
        kb = build_participants_kb(order_id, participants, page, per_page)
        try:
            await q.message.edit_text(txt, reply_markup=kb, parse_mode="Markdown")
        except Exception:
            await q.message.reply_markdown(txt, reply_markup=kb)
        return

    if data.startswith("pp:refresh:"):
        parts = data.split(":")
        order_id = parts[2]; page = int(parts[3]) if len(parts) > 3 else 0
        participants = sheets.get_participants(order_id)
        per_page = 8
        await q.message.edit_text(build_participants_text(order_id, participants, page, per_page),
                                  reply_markup=build_participants_kb(order_id, participants, page, per_page),
                                  parse_mode="Markdown")
        return

    if data.startswith("pp:page:"):
        _, _, order_id, page_s = data.split(":")
        page = int(page_s)
        participants = sheets.get_participants(order_id)
        per_page = 8
        await q.message.edit_text(build_participants_text(order_id, participants, page, per_page),
                                  reply_markup=build_participants_kb(order_id, participants, page, per_page),
                                  parse_mode="Markdown")
        return

# ---------------------- Регистрация ----------------------

def register_handlers(application):
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
