import logging

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from .config import BOT_TOKEN, POLL_MINUTES
from . import sheets
from .texts import HELP
from .statuses import STATUSES

logging.basicConfig(level=logging.INFO)

MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton("Отследить заказ")],
        [KeyboardButton("Мои адреса"), KeyboardButton("Мои подписки")],
        [KeyboardButton("Отмена")],
    ],
    resize_keyboard=True,
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот SEABLUU для отслеживания заказов и адресов. Выберите действие ниже.",
        reply_markup=MAIN_KB,
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()
    text = raw.lower()

    # --- КОМАНДЫ ВСЕГДА В ПРИОРИТЕТЕ ---
    if text in {"отмена", "cancel"}:
        context.user_data["mode"] = None
        await update.message.reply_text("Ок, отменил. Что дальше?", reply_markup=MAIN_KB)
        return

    if text == "отследить заказ":
        context.user_data["mode"] = "track"
        await update.message.reply_text("Отправьте номер заказа (например: KR-12345):")
        return

    if text == "мои адреса":
        context.user_data["mode"] = None   # <— сбрасываем режим
        await show_addresses(update, context)
        return

    if text == "мои подписки":
        context.user_data["mode"] = None   # <— сбрасываем режим
        await show_subscriptions(update, context)
        return

    # --- РЕЖИМЫ ВВОДА (если не нажаты команды) ---
    mode = context.user_data.get("mode")

    if mode == "track":
        # легкая проверка формата: разрешим и «SB-12345», и просто цифры, и произвольный код
        order_id = raw
        # если явно похоже на команду, не считаем это номером
        if text in {"мои адреса", "мои подписки", "отследить заказ"}:
            await update.message.reply_text("Сначала отправьте номер заказа или нажмите «Отмена».", reply_markup=MAIN_KB)
            return
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



async def query_status(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str):
    order = sheets.get_order(order_id)
    if not order:
        await update.message.reply_text("Такой заказ не найден. Проверьте номер или повторите позже.")
        return

    status = order.get("status") or "статус не указан"
    origin = order.get("origin") or ""

    text = (f"Заказ *{order_id}*\n"
            f"Статус: *{status}*")
    if origin:
        text += f"\nСтрана/источник: {origin}"

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔔 Подписаться на обновления", callback_data=f"sub:{order_id}")
    ]])
    await update.message.reply_markdown(text, reply_markup=kb)
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

    context.user_data["mode"] = None
    await update.message.reply_text("Адрес сохранён ✅", reply_markup=MAIN_KB)

async def show_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subs = sheets.list_subscriptions(update.effective_user.id)
    if not subs:
        await update.message.reply_text("Подписок пока нет. Отследите заказ и нажмите «Подписаться».")
        return
    txt = "\n".join([f"• {s['order_id']} (последний статус: {s.get('last_sent_status','—')})" for s in subs])
    await update.message.reply_text("Ваши подписки:\n" + txt)

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("sub:"):
        order_id = data.split(":", 1)[1]
        sheets.subscribe(update.effective_user.id, order_id)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("Готово! Буду присылать обновления по этому заказу 🔔")
    elif data == "addr:add":
        await query.message.reply_text("Давайте добавим/обновим адрес.\nФИО:")
        context.user_data["mode"] = "add_address_fullname"
    elif data == "addr:del":
        ok = sheets.delete_address(update.effective_user.id)
        await query.message.reply_text("Адрес удалён." if ok else "Удалять нечего — адреса нет.")
    else:
        await query.message.reply_text("Действие не поддерживается.")

async def poll_job(context: ContextTypes.DEFAULT_TYPE):
    """Периодический опрос таблицы через JobQueue."""
    try:
        events = sheets.scan_updates()
        for e in events:
            try:
                await context.bot.send_message(
                    chat_id=e["user_id"],
                    text=f"Обновление по заказу {e['order_id']}: {e['new_status']}",
                )
            except Exception:
                pass
    except Exception as ex:
        logging.exception("Ошибка в poll_job: %s", ex)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Unhandled error", exc_info=context.error)

def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Планируем фоновые проверки через JobQueue (через 10 сек, затем каждые POLL_MINUTES минут)
    application.job_queue.run_repeating(poll_job, interval=60 * POLL_MINUTES, first=10)

    # Ловим исключения, чтобы не падало
    application.add_error_handler(error_handler)

    logging.info("Bot started. Waiting for messages...")
    application.run_polling()

if __name__ == "__main__":
    main()
