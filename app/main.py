# app/main.py
import logging
import re
import asyncio
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
from telegram.constants import ChatAction

from . import sheets
from .config import ADMIN_IDS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------- Константы и утилиты ----------------------

STATUSES = [
    "🛒 выкуплен",
    "📦 отправка на адрес (Корея)",
    "📦 отправка на адрес (Китай)",
    "📬 приехал на адрес (Корея)",
    "📬 приехал на адрес (Китай)",
    "🛫 ожидает доставку в Казахстан",
    "🚚 отправлен на адрес в Казахстан",
    "🏠 приехал админу в Казахстан",
    "📦 ожидает отправку по Казахстану",
    "🚚 отправлен по Казахстану",
    "✅ получен заказчиком",
]

UNPAID_STATUS = "доставка не оплачена"

ORDER_ID_RE = re.compile(r"([A-ZА-Я]{1,3})[ \-–—_]*([A-Z0-9]{2,})", re.IGNORECASE)
USERNAME_RE = re.compile(r"@([A-Za-z0-9_]{5,})")

def extract_order_id(s: str) -> str | None:
    if not s:
        return None
    s = s.strip()
    m = ORDER_ID_RE.search(s)
    if m:
        return f"{m.group(1).upper()}-{m.group(2).upper()}"
    # fallback: если уже похоже на PREFIX-SUFFIX, нормализуем
    if "-" in s:
        left, right = s.split("-", 1)
        left, right = left.strip(), right.strip()
        if left and right and left.isalpha():
            import re as _re
            right_norm = _re.sub(r"[^A-Z0-9]+", "", right, flags=_re.I)
            if right_norm:
                return f"{left.upper()}-{right_norm.upper()}"
    return None

def is_valid_status(s: str, statuses: list[str]) -> bool:
    return bool(s) and s.strip().lower() in {x.lower() for x in statuses}

def _is_admin(uid) -> bool:
    return uid in ADMIN_IDS or str(uid) in {str(x) for x in ADMIN_IDS}

# -------- небольшая «анимация» ответов (эффект печати) --------

async def _typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int, seconds: float = 0.6):
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception:
        pass
    await asyncio.sleep(seconds)

async def reply_animated(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs):
    msg = update.message or update.callback_query.message
    await _typing(context, msg.chat_id)
    return await msg.reply_text(text, **kwargs)

async def reply_markdown_animated(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs):
    msg = update.message or update.callback_query.message
    await _typing(context, msg.chat_id)
    return await msg.reply_markdown(text, **kwargs)

# ---------------------- Текст кнопок (новые + обратная совместимость) ----------------------

# Клиентские
BTN_TRACK_NEW = "🔍 Отследить разбор"
BTN_ADDRS_NEW = "🏠 Мои адреса"
BTN_SUBS_NEW  = "🔔 Мои подписки"
BTN_CANCEL_NEW = "❌ Отмена"

CLIENT_ALIASES = {
    "track": {BTN_TRACK_NEW, "отследить разбор"},
    "addrs": {BTN_ADDRS_NEW, "мои адреса"},
    "subs":  {BTN_SUBS_NEW,  "мои подписки"},
    "cancel": {BTN_CANCEL_NEW, "отмена", "cancel"},
}

# Админские
BTN_ADMIN_ADD_NEW     = "➕ Добавить разбор"
BTN_ADMIN_TRACK_NEW   = "🔎 Отследить разбор"
BTN_ADMIN_SEND_NEW    = "📣 Админ: Рассылка"
BTN_ADMIN_ADDRS_NEW   = "📇 Админ: Адреса"
BTN_ADMIN_REPORTS_NEW = "📊 Отчёты"
BTN_ADMIN_MASS_NEW    = "🧰 Массовая смена статусов"
BTN_ADMIN_EXIT_NEW    = "🚪 Выйти из админ-панели"

BTN_BACK_TO_ADMIN_NEW = "⬅️ Назад, в админ-панель"

ADMIN_MENU_ALIASES = {
    "admin_add": {BTN_ADMIN_ADD_NEW, "добавить разбор"},
    "admin_track": {BTN_ADMIN_TRACK_NEW, "отследить разбор"},
    "admin_send": {BTN_ADMIN_SEND_NEW, "админ: рассылка"},
    "admin_addrs": {BTN_ADMIN_ADDRS_NEW, "админ: адреса"},
    "admin_reports": {BTN_ADMIN_REPORTS_NEW, "отчёты"},
    "admin_mass": {BTN_ADMIN_MASS_NEW, "массовая смена статусов"},
    "admin_exit": {BTN_ADMIN_EXIT_NEW, "выйти из админ-панели"},
    "back_admin": {BTN_BACK_TO_ADMIN_NEW, "назад, в админ-панель"},
}

# Подменю «Рассылка»
BTN_BC_ALL_NEW  = "📨 Уведомления всем должникам"
BTN_BC_ONE_NEW  = "📩 Уведомления по ID разбора"

BROADCAST_ALIASES = {
    "bc_all": {BTN_BC_ALL_NEW, "уведомления всем должникам"},
    "bc_one": {BTN_BC_ONE_NEW, "уведомления по id разбора"},
}

# Подменю «Адреса»
BTN_ADDRS_EXPORT_NEW = "📤 Выгрузить адреса"
BTN_ADDRS_EDIT_NEW   = "✏️ Изменить адрес по username"

ADMIN_ADDR_ALIASES = {
    "export_addrs": {BTN_ADDRS_EXPORT_NEW, "выгрузить адреса"},
    "edit_addr":    {BTN_ADDRS_EDIT_NEW, "изменить адрес по username"},
}

# Подменю «Отчёты»
BTN_REPORT_EXPORT_BY_NOTE_NEW = "🧾 Выгрузить разборы админа"
BTN_REPORT_UNPAID_NEW         = "🧮 Отчёт по должникам"

REPORT_ALIASES = {
    "report_by_note": {BTN_REPORT_EXPORT_BY_NOTE_NEW, "выгрузить разборы админа"},
    "report_unpaid": {BTN_REPORT_UNPAID_NEW, "отчёт по должникам"},
}

def _is(text: str, group: set[str]) -> bool:
    return text.strip().lower() in {x.lower() for x in group}

# ---------------------- Клавиатуры ----------------------

MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_TRACK_NEW)],
        [KeyboardButton(BTN_ADDRS_NEW), KeyboardButton(BTN_SUBS_NEW)],
        [KeyboardButton(BTN_CANCEL_NEW)],
    ],
    resize_keyboard=True,
)

# Перестроил админ-меню: «Отчёты» + «Массовая смена» в одной строке, выход — отдельной
ADMIN_MENU_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_ADMIN_ADD_NEW),  KeyboardButton(BTN_ADMIN_TRACK_NEW)],
        [KeyboardButton(BTN_ADMIN_SEND_NEW), KeyboardButton(BTN_ADMIN_ADDRS_NEW)],
        [KeyboardButton(BTN_ADMIN_REPORTS_NEW), KeyboardButton(BTN_ADMIN_MASS_NEW)],
        [KeyboardButton(BTN_ADMIN_EXIT_NEW)],
    ],
    resize_keyboard=True,
)

BROADCAST_MENU_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_BC_ALL_NEW)],
        [KeyboardButton(BTN_BC_ONE_NEW)],
        [KeyboardButton(BTN_BACK_TO_ADMIN_NEW)],
    ],
    resize_keyboard=True,
)

ADMIN_ADDR_MENU_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_ADDRS_EXPORT_NEW)],
        [KeyboardButton(BTN_ADDRS_EDIT_NEW)],
        [KeyboardButton(BTN_BACK_TO_ADMIN_NEW)],
    ],
    resize_keyboard=True,
)

REPORTS_MENU_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_REPORT_EXPORT_BY_NOTE_NEW)],
        [KeyboardButton(BTN_REPORT_UNPAID_NEW)],
        [KeyboardButton(BTN_BACK_TO_ADMIN_NEW)],
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

# Универсальная клавиатура выбора статуса с произвольным префиксом (для массового режима)
def status_keyboard_with_prefix(prefix: str, cols: int = 2) -> InlineKeyboardMarkup:
    rows, row = [], []
    for i, s in enumerate(STATUSES):
        row.append(InlineKeyboardButton(s, callback_data=f"{prefix}:{i}"))
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
    nav.append(InlineKeyboardButton("🔄 Обновить", callback_data=f"pp:refresh:{order_id}:{page}"))
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
    
# ---- Подсказка для текущего шага админа (чтобы не «выкидывало») ----
def _admin_mode_prompt(mode: str):
    """Вернёт (текст, reply_markup) для повторного запроса на текущем шаге."""
    if mode == "add_order_id":
        return "Введи order_id (например: CN-12345):", None
    if mode == "add_order_client":
        return "Имя клиента (можно несколько @username):", None
    if mode == "add_order_country":
        return "Страна/склад: введи 'CN' (Китай) или 'KR' (Корея):", None
    if mode == "add_order_status":
        return "Выбери стартовый статус кнопкой ниже или напиши точный:", status_keyboard(2)
    if mode == "add_order_note":
        return "Примечание (или '-' если нет):", None
    if mode == "find_order":
        return "Введи order_id для поиска (например: CN-12345):", None
    if mode == "adm_remind_unpaid_order":
        return "Введи order_id для рассылки неплательщикам:", None
    if mode == "adm_export_addrs":
        return "Пришли список @username (через пробел/запятую/новые строки):", None
    if mode == "adm_edit_addr_username":
        return "Пришли @username пользователя, чей адрес нужно изменить:", None
    if mode == "adm_edit_addr_fullname":
        return "ФИО (новое значение):", None
    if mode == "adm_edit_addr_phone":
        return "Телефон:", None
    if mode == "adm_edit_addr_city":
        return "Город:", None
    if mode == "adm_edit_addr_address":
        return "Адрес:", None
    if mode == "adm_edit_addr_postcode":
        return "Почтовый индекс:", None
    if mode == "adm_export_orders_by_note":
        return "Пришли метку/слово из note (по ней выгружу разборы):", None
    if mode == "mass_pick_status":
        return "Выбери новый статус для нескольких заказов:", status_keyboard_with_prefix("mass:pick_status_id")
    if mode == "mass_update_status_ids":
        return ("Пришли список order_id (через пробел/запятые/новые строки), "
                "например: CN-1001 CN-1002, KR-2003"), None
    # по умолчанию — просто покажем админ-меню
    return "Вы в админ-панели. Выберите действие:", ADMIN_MENU_KB
    # Короткая причина ошибки отправки
def _err_reason(e: Exception) -> str:
    s = str(e).lower()
    if "forbidden" in s or "blocked" in s:
        return "бот заблокирован"
    if "chat not found" in s or "not found" in s:
        return "нет chat_id"
    if "bad request" in s:
        return "bad request"
    if "retry after" in s or "flood" in s:
        return "rate limit"
    if "timeout" in s:
        return "timeout"
    return "ошибка"

# ---------------------- Команды ----------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Дружелюбное приветствие + лёгкая «анимация»
    hello = (
        "✨ Привет! Я *SEABLUU* Helper — помогу отследить разборы, адреса и подписки.\n\n"
        "*Что умею:*\n"
        "• 🔍 Отследить разбор — статус по `order_id` (например, `CN-12345`).\n"
        "• 🔔 Подписки — уведомлю, когда статус заказа изменится.\n"
        "• 🏠 Мои адреса — сохраню/обновлю адрес для доставки.\n\n"
        "Если что-то пошло не так — нажми «Отмена» или используй /help."
    )
    await reply_markdown_animated(update, context, hello, reply_markup=MAIN_KB)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_animated(
        update, context,
        "📘 Помощь:\n"
        "• 🔍 Отследить разбор — статус по номеру\n"
        "• 🏠 Мои адреса — добавить/изменить адрес\n"
        "• 🔔 Мои подписки — список подписок\n"
        "• /admin — админ-панель (для админов)"
    )

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    for k in ("adm_mode", "adm_buf", "awaiting_unpaid_order_id"):
        context.user_data.pop(k, None)
    await reply_animated(update, context, "🛠 Открываю админ-панель…", reply_markup=ADMIN_MENU_KB)

# ---------------------- Пользовательские сценарии ----------------------

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()
    text = raw.lower()

    # ===== ADMIN FLOW =====
    if _is_admin(update.effective_user.id):

        if _is(text, ADMIN_MENU_ALIASES["admin_exit"]):
            context.user_data.clear()
            await reply_animated(update, context, "🚪 Готово, вышли из админ-панели.", reply_markup=MAIN_KB)
            return

        if _is(text, ADMIN_MENU_ALIASES["admin_add"]):
            context.user_data["adm_mode"] = "add_order_id"
            context.user_data["adm_buf"] = {}
            await reply_markdown_animated(update, context, "➕ Введи *order_id* (например: `CN-12345`):")
            return

        if _is(text, ADMIN_MENU_ALIASES["admin_reports"]):
            await reply_animated(update, context, "📊 Раздел «Отчёты»", reply_markup=REPORTS_MENU_KB)
            return

        if _is(text, ADMIN_MENU_ALIASES["admin_send"]):
            await reply_animated(update, context, "📣 Раздел «Рассылка»", reply_markup=BROADCAST_MENU_KB)
            return

        if _is(text, ADMIN_MENU_ALIASES["admin_addrs"]):
            await reply_animated(update, context, "📇 Раздел «Адреса»", reply_markup=ADMIN_ADDR_MENU_KB)
            return

        if _is(text, ADMIN_MENU_ALIASES["admin_mass"]):
            # шаг 1: выбрать целевой статус из инлайн-клавиатуры
            context.user_data["adm_mode"] = "mass_pick_status"
            await reply_animated(
                update, context,
                "Выбери новый статус для нескольких заказов:",
                reply_markup=status_keyboard_with_prefix("mass:pick_status_id")
            )
            return

        if _is(text, ADMIN_MENU_ALIASES["back_admin"]):
            await admin_menu(update, context)
            return

        # --- Рассылка
        if _is(text, BROADCAST_ALIASES["bc_all"]):
            await broadcast_all_unpaid_text(update, context)
            return

        if _is(text, BROADCAST_ALIASES["bc_one"]):
            context.user_data["adm_mode"] = "adm_remind_unpaid_order"
            await reply_markdown_animated(update, context, "✉️ Введи *order_id* для рассылки неплательщикам:")
            return

        # --- Адреса (подменю)
        if _is(text, ADMIN_ADDR_ALIASES["export_addrs"]):
            context.user_data["adm_mode"] = "adm_export_addrs"
            await reply_animated(update, context, "Пришли список @username (через пробел/запятую/новые строки):")
            return

        if _is(text, ADMIN_ADDR_ALIASES["edit_addr"]):
            context.user_data["adm_mode"] = "adm_edit_addr_username"
            await reply_animated(update, context, "Пришли @username пользователя, чей адрес нужно изменить:")
            return

        # --- Отчёты (подменю)
        if _is(text, REPORT_ALIASES["report_by_note"]):
            context.user_data["adm_mode"] = "adm_export_orders_by_note"
            await reply_markdown_animated(update, context, "🧾 Пришли метку/слово из *note*, по которому помечены твои разборы:")
            return

        if _is(text, REPORT_ALIASES["report_unpaid"]):
            await report_unpaid(update, context)
            return

        # --- Отследить разбор
        if _is(text, ADMIN_MENU_ALIASES["admin_track"]) and (context.user_data.get("adm_mode") is None):
            context.user_data["adm_mode"] = "find_order"
            await reply_markdown_animated(update, context, "🔎 Введи *order_id* для поиска:")
            return

        # --- Мастера/вводы ---
        a_mode = context.user_data.get("adm_mode")

        # Добавление заказа
        if a_mode == "add_order_id":
            context.user_data["adm_buf"] = {"order_id": raw}
            context.user_data["adm_mode"] = "add_order_client"
            await reply_animated(update, context, "Имя клиента (можно несколько @username):")
            return

        if a_mode == "add_order_client":
            context.user_data["adm_buf"]["client_name"] = raw
            context.user_data["adm_mode"] = "add_order_country"
            await reply_animated(update, context, "Страна/склад (CN или KR):")
            return

        if a_mode == "add_order_country":
            country = raw.upper()
            if country not in ("CN", "KR"):
                await reply_animated(update, context, "Введи 'CN' (Китай) или 'KR' (Корея):")
                return
            context.user_data["adm_buf"]["country"] = country
            context.user_data["adm_mode"] = "add_order_status"
            await reply_animated(update, context, "Выбери стартовый статус кнопкой ниже или напиши точный:", reply_markup=status_keyboard(2))
            return

        if a_mode == "add_order_status":
            if not is_valid_status(raw, STATUSES):
                await reply_animated(update, context, "Выбери статус кнопкой ниже или напиши точный:", reply_markup=status_keyboard(2))
                return
            context.user_data["adm_buf"]["status"] = raw.strip()
            context.user_data["adm_mode"] = "add_order_note"
            await reply_animated(update, context, "Примечание (или '-' если нет):")
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
                await reply_markdown_animated(update, context, f"✅ Заказ *{buf['order_id']}* добавлен")
            except Exception as e:
                await reply_animated(update, context, f"Ошибка: {e}")
            finally:
                for k in ("adm_mode", "adm_buf"):
                    context.user_data.pop(k, None)
            return

        # Поиск и карточка + участники + кнопка смены статуса
        if a_mode == "find_order":
            parsed_id = extract_order_id(raw) or raw
            order = sheets.get_order(parsed_id)
            if not order:
                await reply_animated(update, context, "🙈 Заказ не найден.")
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

            await reply_markdown_animated(update, context, "\n".join(head), reply_markup=order_card_kb(order_id))

            # участники
            participants = sheets.get_participants(order_id)
            page = 0; per_page = 8
            part_text = build_participants_text(order_id, participants, page, per_page)
            kb = build_participants_kb(order_id, participants, page, per_page)
            await reply_markdown_animated(update, context, part_text, reply_markup=kb)

            context.user_data.pop("adm_mode", None)
            return

        # Массовая смена статусов: админ присылает список order_id
        if a_mode == "mass_update_status_ids":
            # распарсим произвольный список ID
            raw_ids = re.split(r"[,\s]+", raw.strip())
            ids = []
            seen = set()
            for token in raw_ids:
                oid = extract_order_id(token)
                if oid and oid not in seen:
                    seen.add(oid)
                    ids.append(oid)

            if not ids:
                await reply_animated(update, context, "Не нашёл order_id. Пришли ещё раз (пример: CN-1001 KR-2002).")
                return

            new_status = context.user_data.get("mass_status")
            if not new_status:
                await reply_animated(update, context, "Не выбран новый статус. Повтори с начала: «🧰 Массовая смена статусов».")
                context.user_data.pop("adm_mode", None)
                return

            ok, fail = 0, 0
            failed_ids = []
            for oid in ids:
                try:
                    updated = sheets.update_order_status(oid, new_status)
                    if updated:
                        ok += 1
                        # уведомим подписчиков конкретного заказа
                        try:
                            await notify_subscribers(context.application, oid, new_status)
                        except Exception:
                            pass
                    else:
                        fail += 1
                        failed_ids.append(oid)
                except Exception:
                    fail += 1
                    failed_ids.append(oid)

            # очистим режим
            context.user_data.pop("adm_mode", None)
            context.user_data.pop("mass_status", None)

            # отчёт
            parts = [
                "🧰 Массовая смена статусов — итог",
                f"Всего заказов: {len(ids)}",
                f"✅ Успешно: {ok}",
                f"❌ Ошибки: {fail}",
            ]
            if failed_ids:
                parts.append("")
                parts.append("Не удалось обновить:")
                parts.append(", ".join(failed_ids))
            await reply_animated(update, context, "\n".join(parts))
            return

        # Ручная рассылка по одному order_id
        if a_mode == "adm_remind_unpaid_order":
            parsed_id = extract_order_id(raw) or raw

            # если такого заказа нет — остаёмся в этом же шаге и просим ввести корректный
            order = sheets.get_order(parsed_id)
            if not order:
                await reply_animated(
                    update, context,
                    "🙈 Заказ не найден. Введи корректный *order_id* (например: CN-12345):"
                )
                return  # НЕ выходим из админки и шага

            # если заказ есть — шлём рассылку и показываем подробный отчёт
            ok, report = await remind_unpaid_for_order(context.application, parsed_id)
            await reply_animated(update, context, report)

            # выходим из шага, но остаёмся в админ-панели
            context.user_data.pop("adm_mode", None)
            return

        # Выгрузить адреса (по списку username)
        if a_mode == "adm_export_addrs":
            usernames = [m.group(1) for m in USERNAME_RE.finditer(raw)]
            if not usernames:
                await reply_animated(update, context, "Пришли список @username.")
                return
            rows = sheets.get_addresses_by_usernames(usernames)
            if not rows:
                await reply_animated(update, context, "Адреса не найдены.")
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
                await reply_animated(update, context, "\n".join(lines))
            context.user_data.pop("adm_mode", None)
            return

        # Изменить адрес по username — шаги мастера
        if a_mode == "adm_edit_addr_username":
            usernames = [m.group(1) for m in USERNAME_RE.finditer(raw)]
            if not usernames:
                await reply_animated(update, context, "Пришли @username.")
                return
            uname = usernames[0].lower()
            ids = sheets.get_user_ids_by_usernames([uname])
            if not ids:
                await reply_animated(update, context, "Пользователь не найден по username (нет записи в адресах).")
                context.user_data.pop("adm_mode", None)
                return
            context.user_data["adm_mode"] = "adm_edit_addr_fullname"
            context.user_data["adm_buf"] = {"edit_user_id": ids[0], "edit_username": uname}
            await reply_animated(update, context, "ФИО (новое значение):")
            return

        if a_mode == "adm_edit_addr_fullname":
            context.user_data.setdefault("adm_buf", {})["full_name"] = raw
            context.user_data["adm_mode"] = "adm_edit_addr_phone"
            await reply_animated(update, context, "Телефон:")
            return

        if a_mode == "adm_edit_addr_phone":
            context.user_data["adm_buf"]["phone"] = raw
            context.user_data["adm_mode"] = "adm_edit_addr_city"
            await reply_animated(update, context, "Город:")
            return

        if a_mode == "adm_edit_addr_city":
            context.user_data["adm_buf"]["city"] = raw
            context.user_data["adm_mode"] = "adm_edit_addr_address"
            await reply_animated(update, context, "Адрес:")
            return

        if a_mode == "adm_edit_addr_address":
            context.user_data["adm_buf"]["address"] = raw
            context.user_data["adm_mode"] = "adm_edit_addr_postcode"
            await reply_animated(update, context, "Почтовый индекс:")
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
                await reply_animated(update, context, "✅ Адрес обновлён")
            except Exception as e:
                await reply_animated(update, context, f"Ошибка: {e}")
            finally:
                context.user_data.pop("adm_mode", None)
                context.user_data.pop("adm_buf", None)
            return

        # Выгрузить разборы по note
        if a_mode == "adm_export_orders_by_note":
            marker = raw.strip()
            if not marker:
                await reply_animated(update, context, "Пришли метку/слово для поиска в note.")
                return
            orders = sheets.get_orders_by_note(marker)
            if not orders:
                await reply_animated(update, context, "Ничего не найдено.")
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
                await reply_markdown_animated(update, context, "\n".join(lines))
            context.user_data.pop("adm_mode", None)
            return

    # ===== USER FLOW =====
    if _is(text, CLIENT_ALIASES["cancel"]):
        context.user_data["mode"] = None
        await reply_animated(update, context, "Отменили действие. Что дальше? 🙂", reply_markup=MAIN_KB)
        return

    if _is(text, CLIENT_ALIASES["track"]):
        context.user_data["mode"] = "track"
        await reply_animated(update, context, "🔎 Отправьте номер заказа (например: CN-12345):")
        return

    if _is(text, CLIENT_ALIASES["addrs"]):
        context.user_data["mode"] = None
        await show_addresses(update, context)
        return

    if _is(text, CLIENT_ALIASES["subs"]):
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
        await reply_animated(update, context, "📞 Телефон (пример: 87001234567):")
        context.user_data["mode"] = "add_address_phone"
        return

    if mode == "add_address_phone":
        normalized = raw.strip().replace(" ", "").replace("-", "")
        if normalized.startswith("+7"): normalized = "8" + normalized[2:]
        elif normalized.startswith("7"): normalized = "8" + normalized[1:]
        if not (normalized.isdigit() and len(normalized) == 11 and normalized.startswith("8")):
            await reply_animated(update, context, "Нужно 11 цифр и обязательно с 8. Пример: 87001234567\nВведи номер ещё раз или нажми «Отмена».")
            return
        context.user_data["phone"] = normalized
        await reply_animated(update, context, "🏙 Город (пример: Астана):")
        context.user_data["mode"] = "add_address_city"
        return

    if mode == "add_address_city":
        context.user_data["city"] = raw
        await reply_animated(update, context, "🏠 Адрес (свободный формат):")
        context.user_data["mode"] = "add_address_address"
        return

    if mode == "add_address_address":
        context.user_data["address"] = raw
        await reply_animated(update, context, "📮 Почтовый индекс (пример: 010000):")
        context.user_data["mode"] = "add_address_postcode"
        return

    if mode == "add_address_postcode":
        if not (raw.isdigit() and 5 <= len(raw) <= 6):
            await reply_animated(update, context, "Индекс выглядит странно. Пример: 010000\nВведи индекс ещё раз или нажми «Отмена».")
            return
        context.user_data["postcode"] = raw
        await save_address(update, context)
        return

  # Ничего не подошло — отдельная ветка для админов и для клиентов
    if _is_admin(update.effective_user.id):
        a_mode = context.user_data.get("adm_mode")
        # если админ в конкретном шаге — не выходим, а просим ввести корректно
        if a_mode:
            msg, kb = _admin_mode_prompt(a_mode)
            await reply_animated(update, context, f"⚠️ Не понял. {msg}", reply_markup=kb or ADMIN_MENU_KB)
            return
        # если админ не в шаге — просто перерисуем админ-меню
        await reply_animated(update, context, "Вы в админ-панели. Выберите действие:", reply_markup=ADMIN_MENU_KB)
        return

    # Клиентский фолбэк
    await reply_animated(
        update, context,
        "Хмм, не понял. Выберите кнопку ниже или введите номер заказа. Если что — «Отмена».",
        reply_markup=MAIN_KB,
    )

# ---------------------- Клиент: статус/подписки/адреса ----------------------

async def query_status(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str):
    await _typing(context, update.effective_chat.id, 0.5)
    order_id = extract_order_id(order_id) or order_id
    order = sheets.get_order(order_id)
    if not order:
        await reply_animated(update, context, "🙈 Такой заказ не найден. Проверьте номер или повторите позже.")
        return
    status = order.get("status") or "статус не указан"
    origin = order.get("origin") or ""
    txt = f"📦 Заказ *{order_id}*\nСтатус: *{status}*"
    if origin:
        txt += f"\nСтрана/источник: {origin}"

    if sheets.is_subscribed(update.effective_user.id, order_id):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔕 Отписаться", callback_data=f"unsub:{order_id}")]])
    else:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔔 Подписаться на обновления", callback_data=f"sub:{order_id}")]])
    await reply_markdown_animated(update, context, txt, reply_markup=kb)
    context.user_data["mode"] = None

async def show_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _typing(context, update.effective_chat.id, 0.4)
    addrs = sheets.list_addresses(update.effective_user.id)
    if not addrs:
        await reply_animated(
            update, context,
            "У вас пока нет адреса. Добавим?",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Добавить адрес", callback_data="addr:add")]]),
        )
        return
    lines = []
    for a in addrs:
        lines.append(f"• {a['full_name']} — {a['phone']}\n{a['city']}, {a['address']}, {a['postcode']}")
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✏️ Изменить адрес", callback_data="addr:add")],
            [InlineKeyboardButton("🗑 Удалить адрес", callback_data="addr:del")],
        ]
    )
    await reply_animated(update, context, "📍 Ваш адрес доставки:\n" + "\n\n".join(lines), reply_markup=kb)

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
        "✅ Адрес сохранён!\n\n"
        f"👤 ФИО: {context.user_data.get('full_name','')}\n"
        f"📞 Телефон: {context.user_data.get('phone','')}\n"
        f"🏙 Город: {context.user_data.get('city','')}\n"
        f"🏠 Адрес: {context.user_data.get('address','')}\n"
        f"📮 Индекс: {context.user_data.get('postcode','')}"
    )
    await reply_animated(update, context, msg, reply_markup=MAIN_KB)

async def show_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _typing(context, update.effective_chat.id, 0.4)
    subs = sheets.list_subscriptions(update.effective_user.id)
    if not subs:
        await reply_animated(update, context, "Пока нет подписок. Отследите заказ и нажмите «Подписаться».")
        return
    txt_lines, kb_rows = [], []
    for s in subs:
        last = s.get("last_sent_status", "—")
        order_id = s["order_id"]
        txt_lines.append(f"• {order_id} — последний статус: {last}")
        kb_rows.append([InlineKeyboardButton(f"🗑 Отписаться от {order_id}", callback_data=f"unsub:{order_id}")])
    await reply_animated(update, context, "🔔 Ваши подписки:\n" + "\n".join(txt_lines), reply_markup=InlineKeyboardMarkup(kb_rows))

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
                text=f"🔄 Обновление по заказу *{order_id}*\nНовый статус: *{new_status}*",
                parse_mode="Markdown",
            )
            try: sheets.set_last_sent_status(uid, order_id, new_status)
            except Exception: pass
        except Exception as e:
            logger.warning(f"notify_subscribers fail to {uid}: {e}")

# ---------- Напоминания об оплате ----------

async def remind_unpaid_for_order(application, order_id: str) -> tuple[bool, str]:
    """
    Шлёт напоминание неплательщикам ТОЛЬКО по указанному order_id
    и возвращает (было_ли_кому_слать, подробный_отчёт_в_markdown).
    """
    order = sheets.get_order(order_id)
    if not order:
        return False, "🙈 Заказ не найден."

    usernames = sheets.get_unpaid_usernames(order_id)  # список username без @
    if not usernames:
        return False, f"🎉 По заказу *{order_id}* должников нет — красота!"

    lines = [f"📩 Уведомления по ID разбора — {order_id}"]
    ok_cnt, fail_cnt = 0, 0

    for uname in usernames:
        ids = []
        try:
            ids = sheets.get_user_ids_by_usernames([uname])  # [uid] или []
        except Exception:
            pass

        if not ids:
            fail_cnt += 1
            lines.append(f"• ❌ @{uname} — нет chat_id")
            continue

        uid = ids[0]
        try:
            # на всякий случай подпишем, чтобы получил будущие статусы
            try:
                sheets.subscribe(uid, order_id)
            except Exception:
                pass

            await application.bot.send_message(
                chat_id=uid,
                text=(
                    f"💳 Напоминание по разбору *{order_id}*\n"
                    f"Статус: *Доставка не оплачена*\n\n"
                    f"Пожалуйста, оплатите доставку. Если уже оплатили — можно игнорировать."
                ),
                parse_mode="Markdown",
            )
            ok_cnt += 1
            lines.append(f"• ✅ @{uname}")
        except Exception as e:
            fail_cnt += 1
            lines.append(f"• ❌ @{uname} — {_err_reason(e)}")

    lines.append("")
    lines.append(f"_Итого:_ ✅ {ok_cnt}  ❌ {fail_cnt}")
    return True, "\n".join(lines)

async def report_unpaid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    grouped = sheets.get_all_unpaid_grouped()
    if not grouped:
        await reply_animated(update, context, "🎉 Должников не найдено — красота!")
        return
    lines = ["📋 Отчёт по должникам:"]
    for oid, users in grouped.items():
        ulist = ", ".join([f"@{u}" for u in users])
        lines.append(f"• {oid}: {ulist if ulist else '—'}")
    await reply_animated(update, context, "\n".join(lines))

async def broadcast_all_unpaid_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Шлёт напоминания всем должникам по всем разборам и формирует подробный отчёт:
    для каждого order_id — список пользователей с ✅/❌ и краткой причиной.
    """
    grouped = sheets.get_all_unpaid_grouped()  # {order_id: [username, ...]}
    if not grouped:
        await reply_animated(update, context, "🎉 Должников не найдено — красота!")
        return

    total_orders = len(grouped)
    total_ok = 0
    total_fail = 0
    blocks: list[str] = []

    for order_id, usernames in grouped.items():
        order_ok = 0
        order_fail = 0
        lines = [f"{order_id}:"]

        # обрабатываем по username, чтобы красиво показать, кому именно ушло/не ушло
        for uname in usernames:
            try:
                ids = sheets.get_user_ids_by_usernames([uname])  # [uid] или []
                if not ids:
                    order_fail += 1
                    lines.append(f"• ❌ @{uname} — нет chat_id")
                    continue

                uid = ids[0]
                try:
                    # подписываем на обновления заказа, чтобы дальше человек получал статусы
                    try:
                        sheets.subscribe(uid, order_id)
                    except Exception:
                        pass

                    await context.bot.send_message(
                        chat_id=uid,
                        text=(
                            f"💳 Напоминание по разбору *{order_id}*\n"
                            f"Статус: *Доставка не оплачена*\n\n"
                            f"Пожалуйста, оплатите доставку. Если уже оплатили — можно игнорировать."
                        ),
                        parse_mode="Markdown",
                    )
                    order_ok += 1
                    lines.append(f"• ✅ @{uname}")
                except Exception as e:
                    order_fail += 1
                    lines.append(f"• ❌ @{uname} — {_err_reason(e)}")
            except Exception as e:
                order_fail += 1
                lines.append(f"• ❌ @{uname} — {_err_reason(e)}")

        total_ok += order_ok
        total_fail += order_fail
        lines.append(f"_Итого по разбору:_ ✅ {order_ok}  ❌ {order_fail}")
        blocks.append("\n".join(lines))

    summary = "\n".join([
    "📣 Уведомления всем должникам — итог",
    f"Разборов: {total_orders}",
    f"✅ Успешно: {total_ok}",
    f"❌ Ошибок: {total_fail}",
    "",
    *blocks,
])
    await reply_animated(update, context, summary)

# ---------- CallbackQuery ----------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    # адреса (клиент)
    if data == "addr:add":
        context.user_data["mode"] = "add_address_fullname"
        await reply_animated(update, context, "Давайте добавим/обновим адрес.\n👤 ФИО:")
        return

    if data == "addr:del":
        ok = sheets.delete_address(update.effective_user.id)
        await reply_animated(update, context, "Адрес удалён ✅" if ok else "Удалять нечего — адрес не найден.")
        return

    # смена статуса из карточки заказа
    if data.startswith("adm:status_menu:"):
        if not _is_admin(update.effective_user.id): return
        order_id = data.split(":", 2)[2]
        rows = [[InlineKeyboardButton(s, callback_data=f"adm:set_status_val:{order_id}:{i}")] for i, s in enumerate(STATUSES)]
        await reply_animated(update, context, "Выберите новый статус:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("adm:set_status_val:"):
        if not _is_admin(update.effective_user.id): return
        _, _, order_id, idx_s = data.split(":")
        try:
            idx = int(idx_s); new_status = STATUSES[idx]
        except Exception:
            await reply_animated(update, context, "Некорректный выбор статуса.")
            return
        ok = sheets.update_order_status(order_id, new_status)
        if ok:
            await reply_markdown_animated(update, context, f"✨ Статус *{order_id}* обновлён на: _{new_status}_ ✅")
            await notify_subscribers(context.application, order_id, new_status)
        else:
            await reply_animated(update, context, "Заказ не найден.")
        return

    # <<< НОВОЕ >>> выбор статуса в мастере добавления заказа
    if data.startswith("adm:pick_status_id:"):
        if not _is_admin(update.effective_user.id):
            return
        _, _, idx_s = data.split(":")
        try:
            idx = int(idx_s)
            chosen = STATUSES[idx]
        except Exception:
            await reply_animated(update, context, "Некорректный выбор статуса.")
            return
        # положим в буфер и перейдём к шагу «примечание»
        context.user_data.setdefault("adm_buf", {})["status"] = chosen
        context.user_data["adm_mode"] = "add_order_note"
        await reply_animated(update, context, "Примечание (или '-' если нет):")
        return

    # массовая смена статусов: выбор статуса (шаг 1)
    if data.startswith("mass:pick_status_id:"):
        if not _is_admin(update.effective_user.id):
            return
        _, _, idx_s = data.split(":")
        try:
            idx = int(idx_s)
            new_status = STATUSES[idx]
        except Exception:
            await reply_animated(update, context, "Некорректный выбор статуса.")
            return
        # запомним и попросим список заказов
        context.user_data["adm_mode"] = "mass_update_status_ids"
        context.user_data["mass_status"] = new_status
        await reply_markdown_animated(
            update, context,
            "Ок! Новый статус: *{0}*\n\nТеперь пришли список `order_id`:\n"
            "• через пробел, запятые или с новой строки\n"
            "• пример: `CN-1001 CN-1002, KR-2003`".format(new_status)
        )
        return

    # подписка/отписка (клиент)
    if data.startswith("sub:"):
        order_id = data.split(":", 1)[1]
        sheets.subscribe(update.effective_user.id, order_id)
        try:
            await q.edit_message_reply_markup(InlineKeyboardMarkup([[InlineKeyboardButton("🔕 Отписаться", callback_data=f"unsub:{order_id}")]]))
        except Exception:
            pass
        await reply_animated(update, context, "Готово! Буду присылать обновления по этому заказу 🔔")
        return

    if data.startswith("unsub:"):
        order_id = data.split(":", 1)[1]
        sheets.unsubscribe(update.effective_user.id, order_id)
        await reply_animated(update, context, "Отписка выполнена.")
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
            await reply_markdown_animated(update, context, txt, reply_markup=kb)
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
