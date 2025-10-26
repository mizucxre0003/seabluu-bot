# app/webhook.py
import logging
import asyncio
import os
from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import ApplicationBuilder

from .config import BOT_TOKEN
from .main import register_handlers, register_daily_unpaid_job, register_admin_ui
from . import sheets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# PTB Application
application = ApplicationBuilder().token(BOT_TOKEN).build()
register_handlers(application)
# Register admin UI
try:
    register_admin_ui(application)
    logger.info('Admin UI handlers registered.')
except Exception as e:
    logger.warning('Admin UI not registered: %s', e)

# FastAPI app
app = FastAPI()
_bg_task = None


async def _bg_loop():
    """Фоновая рассылка по подпискам: ловим ручные правки в Google Sheets."""
    logger.info("Background loop started")
    while True:
        try:
            to_send = sheets.scan_updates()
            logger.info("[bg] subscriptions delta: %d", len(to_send))
            for item in to_send:
                try:
                    logger.info("[bg] sending update to user_id=%s order=%s", item.get("user_id"), item.get("order_id"))
                    await application.bot.send_message(
                        chat_id=int(item["user_id"]),
                        text=(
                            f"Обновление по заказу *{item['order_id']}*\n"
                            f"Новый статус: *{item['new_status']}*"
                        ),
                        parse_mode="Markdown",
                    )
                except Exception as e:
                    logger.warning("Send fail: %s", e)
        except Exception as e:
            logger.exception("scan_updates error: %s", e)
        await asyncio.sleep(120)  # каждые 2 минуты


@app.on_event("startup")
async def on_startup():
    """Инициализация PTB, вебхука и фоновых задач."""
    global _bg_task

    # 1) Инициализируем PTB (чтобы был бот и запросы работали)
    try:
        await application.initialize()
        logger.info("PTB Application initialized.")
    except Exception as e:
        logger.exception("Failed to initialize PTB: %s", e)
        # продолжаем без падения — /telegram защитим try/except

    # 2) Ставим webhook, если есть PUBLIC_URL
    public_url = os.getenv("PUBLIC_URL", "").rstrip("/")
    if public_url:
        webhook_url = f"{public_url}/telegram"
        try:
            await application.bot.set_webhook(url=webhook_url)
            me = await application.bot.get_me()
            logger.info("Webhook set to %s (bot: @%s)", webhook_url, me.username)
        except Exception as e:
            logger.exception("Failed to set webhook to %s: %s", webhook_url, e)
    else:
        logger.warning("PUBLIC_URL не задан — webhook не устанавливаем.")

    # 3) Запускаем фоновую проверку обновлений в таблице
    if _bg_task is None:
        _bg_task = asyncio.create_task(_bg_loop())

    # 4) Регистрируем ежедневную рассылку должникам (APSheduler)
    try:
        register_daily_unpaid_job(application)
    except Exception as e:
        logger.warning("Daily unpaid job not registered: %s", e)

    logger.info("FastAPI started. Waiting for Telegram updates...")


@app.on_event("shutdown")
async def on_shutdown():
    try:
        await application.shutdown()
    except Exception:
        pass


@app.post("/telegram")
async def telegram_webhook(req: Request):
    """Входящий webhook от Telegram."""
    try:
        data = await req.json()

        # На случай, если initialize не успел на старте
        if not getattr(application, "_initialized", False):
            try:
                await application.initialize()
                logger.info("PTB Application initialized lazily on first update.")
            except Exception as e:
                logger.exception("Failed to initialize PTB on first update: %s", e)
                return Response(status_code=200)

        update = Update.de_json(data, application.bot)
        try:
            utype = (
                'message' if getattr(update, 'message', None) else
                'callback_query' if getattr(update, 'callback_query', None) else
                'other'
            )
            logger.info("[webhook] incoming update: type=%s", utype)
        except Exception:
            pass
        await application.process_update(update)
    except Exception as e:
        logger.exception("Error processing update: %s", e)
    return Response(status_code=200)


@app.get("/health")
async def health():
    return {"ok": True}


