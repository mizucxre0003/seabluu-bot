# app/main.py
import logging
from typing import List

from telegram import (
    Update,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)
from telegram.ext import (
    ContextTypes,
    CommandHandler, MessageHandler, CallbackQueryHandler, filters,
)

from . import sheets
from .config import ADMIN_IDS  # ожидается список ID (int/str)

logger = logging.getLogger(__name__)

# =========================
# БАЗОВЫЕ ХЭНДЛЕРЫ / МЕНЮ
# =========================

HELP_TEXT = (
    "• Отследить заказ/разбор — статус по номеру\n"
    "• Мои адреса — добавить/изменить адрес\n"
    "• Подписки — уведомления об изменении статуса\n"
    "Для выхода — «Отмена»."
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    text = f"Привет, {u.first_name or 'друг'}!\n\n{HELP_TEXT}"
    # клиентская клавиатура
    kb = ReplyKeyboardMarkup(
        [[KeyboardButton("Отследить разбор")]],
        resize_keyboard=True,
        one_time_keyboard=False
    )
    await update.message.reply_text(text, reply_markup=kb)

def _is_admin(user_id) -> bool:
    return str(user_id) in {str(x) for x in ADMIN_IDS}

# =========================
# РЕЖИМ ОТСЛЕЖИВАНИЯ ЗАКАЗА
# =========================

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Общий текстовый роутер для клиентов (админов перехватит on_admin_text с приоритетом -10)."""
    if not update.message:
        return
    text = (update.message.text or "").strip().lower()

    # Ключевое: теперь ловим оба варианта
    if text in ("отследить заказ", "отследить разбор"):
        await update.message.reply_text("Введите номер заказа (например CN-00000). Для выхода — «Отмена».")
        context.user_data["awaiting_order_id"] = True
        return

    if context.user_data.get("awaiting_order_id"):
        order_id = (update.message.text or "").strip()
        context.user_data.pop("awaiting_order_id", None)
        # TODO: здесь ваша логика получения статуса из таблицы orders
        await update.message.reply_text(f"Статус по {order_id}: (здесь ваш статус)")
        return

    if text == "отмена":
        context.user_data.clear()
        await update.message.reply_text("Ок, вышли из режима.")
        return

    # прочее
    await update.message.reply_text("Не понял. Нажмите кнопку ниже или введите номер заказа. Для выхода — «Отмена».")

# ============
# АДРЕСА
# ============

async def save_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пример сохранения адреса с нормализацией username -> lower()."""
    u = update.effective_user
    # Пример: данные адреса вы соберёте через свою форму
    full_name = u.full_name or ""
    phone = ""
    city = ""
    address = ""
    postcode = ""
    username = (u.username or "").strip().lower()
    logger.info("[addresses] save: user_id=%s username=%s", u.id, username)

    sheets.upsert_address(
        user_id=u.id,
        full_name=full_name,
        phone=phone,
        city=city,
        address=address,
        postcode=postcode,
        username=username,
    )

    # Автоподписка — если у вас она предусмотрена:
    try:
        rel_orders: List[str] = sheets.find_orders_for_username(username.lower())  # noqa: your impl
        # for oid in rel_orders: sheets.subscribe(u.id, oid)  # noqa
    except Exception:
        pass

    await update.message.reply_text("Адрес сохранён.")

# ======================
# РАССЫЛКА ДОЛЖНИКАМ
# ======================

async def remind_unpaid_for_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправка напоминаний должникам по одному order_id + отчёт админу."""
    if not _is_admin(update.effective_user.id):
        return

    await update.message.reply_text("Введи order_id для рассылки неплательщикам:")
    context.user_data["awaiting_unpaid_order_id"] = True

async def on_text_after_remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ввода order_id для рассылки по одному разбору."""
    if not update.message:
        return
    if not context.user_data.get("awaiting_unpaid_order_id"):
        return
    if not _is_admin(update.effective_user.id):
        return

    order_id = (update.message.text or "").strip()
    context.user_data.pop("awaiting_unpaid_order_id", None)

    unpaid_usernames = [u.lower() for u in sheets.get_unpaid_usernames(order_id)]
    logger.info("[remind] order=%s unpaid=%s", order_id, unpaid_usernames)

    user_ids = sheets.get_user_ids_by_usernames(unpaid_usernames)
    logger.info("[remind] mapped user_ids=%s", user_ids)

    report_ok, report_fail = [], []

    # Отправка
    for uid in user_ids:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"Напоминание по доставке: неоплаченный разбор {order_id}. Пожалуйста, оплатите."
            )
            report_ok.append(uid)
        except Exception as e:
            logger.warning("remind failed for %s: %s", uid, e)
            report_fail.append({"user_id": uid, "reason": str(e)})

    # Итог админу
    ok_count = len(report_ok)
    fail_count = len(report_fail)
    lines = [
        f"📊 Отчёт по рассылке (order {order_id})",
        f"✅ Доставлено: {ok_count}",
        f"❌ Не доставлено: {fail_count}",
    ]
    if fail_count:
        details = [f"• user_id={x['user_id']} — {x['reason']}" for x in report_fail]
        lines += ["", "Причины:", *details]

    await context.bot.send_message(chat_id=update.effective_chat.id, text="\n".join(lines))

# Массовая рассылка по всем разборам
async def broadcast_all_unpaid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return

    grouped = sheets.get_all_unpaid_grouped()
    total_orders = len(grouped)
    total_ok = 0
    total_fail = 0
    report_lines = []
    for order_id, users in grouped.items():
        unpaid_usernames = [u.lower() for u in users]
        user_ids = sheets.get_user_ids_by_usernames(unpaid_usernames)
        ok, fail = 0, 0
        for uid in user_ids:
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text=f"Напоминание по доставке: неоплаченный разбор {order_id}. Пожалуйста, оплатите."
                )
                ok += 1
            except Exception:
                fail += 1
        total_ok += ok
        total_fail += fail
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

# ============================
# АДМИН-КЛАВИАТУРА И МЕНЮ
# ============================

ADMIN_MENU_BTNS = [
    [KeyboardButton("Отследить разбор"), KeyboardButton("Админ: Рассылка")],
    [KeyboardButton("Админ: Заказы"),   KeyboardButton("Админ: Адреса")],
]
ADMIN_BACK_BTN = KeyboardButton("Назад, в админ-панель")
BROADCAST_MENU_BTNS = [
    [KeyboardButton("Уведомления всем должникам")],
    [KeyboardButton("Уведомления по ID разбора")],
    [ADMIN_BACK_BTN],
]

async def show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    kb = ReplyKeyboardMarkup(ADMIN_MENU_BTNS, resize_keyboard=True, one_time_keyboard=False)
    await context.bot.send_message(update.effective_chat.id, "Админ-панель: выберите раздел", reply_markup=kb)

async def show_broadcast_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    kb = ReplyKeyboardMarkup(BROADCAST_MENU_BTNS, resize_keyboard=True, one_time_keyboard=False)
    await context.bot.send_message(update.effective_chat.id, "Раздел «Рассылка»", reply_markup=kb)

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    await show_admin_menu(update, context)

async def on_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Роутер для админских текстов. Регистрируется с group=-10, поэтому срабатывает раньше on_text."""
    if not update.message:
        return
    if not _is_admin(update.effective_user.id):
        return

    text = (update.message.text or "").strip().lower()

    # Подменю «Рассылка»
    if text == "админ: рассылка":
        return await show_broadcast_menu(update, context)

    # Возврат назад
    if text in ("назад, в админ-панель", "назад в админ-меню", "назад в админ-панель"):
        return await show_admin_menu(update, context)

    # Кнопки раздела «Рассылка»
    if text == "уведомления всем должникам":
        return await broadcast_all_unpaid(update, context)

    if text == "уведомления по id разбора":
        return await remind_unpaid_for_order(update, context)

    # Чтобы админ тоже мог «Отследить разбор» обычным способом
    if text in ("отследить заказ", "отследить разбор"):
        await update.message.reply_text("Введите номер заказа (например CN-00000). Для выхода — «Отмена».")
        context.user_data["awaiting_order_id"] = True
        return

# ============================
# РЕГИСТРАЦИЯ ХЭНДЛЕРОВ
# ============================

def register_handlers(application):
    """ВАШИ базовые хэндлеры (оставьте как было у вас, ниже — примеры)."""
    application.add_handler(CommandHandler("start", start))
    # общий текст (клиентский) — в обычной группе
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), on_text))
    # пост-обработчик ввода order_id для рассылки по одному разбору
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), on_text_after_remind))

def register_admin_ui(application):
    """Отдельная регистрация админ-UI. Ставим с приоритетом group=-10, чтобы обогнать общий on_text."""
    application.add_handler(CommandHandler("admin", cmd_admin), group=-10)
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), on_admin_text), group=-10)

# ==== здесь можно зарегистрировать прочие ваши админские/клиентские хэндлеры ====
# application.add_handler(CallbackQueryHandler(...))
# ...
