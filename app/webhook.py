# app/webhook.py
import logging
import asyncio
from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import ApplicationBuilder

from .config import BOT_TOKEN
from .main import register_handlers
from . import sheets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

application = ApplicationBuilder().token(BOT_TOKEN).build()
register_handlers(application)

app = FastAPI()
_bg_task = None

async def _bg_loop():
    """Фоновая рассылка по подпискам: ловим ручные правки в Google Sheets."""
    await application.initialize()  # чтобы был bot
    logger.info("Background loop started")
    while True:
        try:
            to_send = sheets.scan_updates()
            for item in to_send:
                try:
                    await application.bot.send_message(
                        chat_id=int(item["user_id"]),
                        text=f"Обновление по заказу *{item['order_id']}*\nНовый статус: *{item['new_status']}*",
                        parse_mode="Markdown",
                    )
                except Exception as e:
                    logger.warning("Send fail: %s", e)
        except Exception as e:
            logger.exception("scan_updates error: %s", e)
        await asyncio.sleep(120)  # каждые 2 минуты

@app.on_event("startup")
async def on_startup():
    global _bg_task
    if _bg_task is None:
        _bg_task = asyncio.create_task(_bg_loop())
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

        if not getattr(application, "_initialized", False):
            try:
                await application.initialize()
                logger.info("PTB Application initialized lazily on first update.")
            except Exception as e:
                logger.exception("Failed to initialize PTB on first update: %s", e)
                return Response(status_code=200)

        update = Update.de_json(data, application.bot)
        await application.process_update(update)
    except Exception as e:
        logger.exception("Error processing update: %s", e)
    return Response(status_code=200)

@app.get("/health")
async def health():
    return {"ok": True}
