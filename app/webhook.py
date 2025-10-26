# app/webhook.py
import logging
from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import ApplicationBuilder

from .config import BOT_TOKEN
from .main import register_handlers  # все хендлеры регистрируем одной функцией

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Создаём PTB-приложение один раз (без сетевых вызовов на старте)
application = ApplicationBuilder().token(BOT_TOKEN).build()
register_handlers(application)

app = FastAPI()

@app.on_event("startup")
async def on_startup():
    # Ничего сетевого (initialize/set_webhook) не делаем на старте,
    # чтобы не упасть из-за внешних ограничений — ленивая инициализация ниже.
    logger.info("FastAPI started. Waiting for Telegram updates...")

@app.on_event("shutdown")
async def on_shutdown():
    try:
        await application.shutdown()
    except Exception:
        pass

@app.post("/telegram")
async def telegram_webhook(req: Request):
    """Входящий webhook от Telegram с ленивой инициализацией PTB."""
    try:
        data = await req.json()

        # Ленивая инициализация PTB на первом апдейте
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
