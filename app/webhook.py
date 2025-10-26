import logging
from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from .config import BOT_TOKEN
from .main import start, help_cmd, handle_text, on_callback
from app.main import admin_menu, on_admin_callback  # если ещё не добавлено

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

application = ApplicationBuilder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_cmd))
application.add_handler(CallbackQueryHandler(on_callback))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
application.add_handler(CommandHandler("admin", admin_menu))
application.add_handler(CallbackQueryHandler(on_admin_callback, pattern=r"^adm:"))

app = FastAPI()

@app.on_event("startup")
async def on_startup():
    logger.info("FastAPI started. Waiting for Telegram updates...")

@app.on_event("shutdown")
async def on_shutdown():
    try:
        await application.shutdown()
    except Exception:
        pass

@app.post("/telegram")
async def telegram_webhook(req: Request):
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
