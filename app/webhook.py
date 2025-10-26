# app/webhook.py
import logging

from fastapi import FastAPI, Request
from fastapi.responses import Response

from telegram import Update
from telegram.ext import Application, ApplicationBuilder

from .main import register_handlers
try:
    from .main import register_admin_ui
except Exception:
    register_admin_ui = None  # безопасно, чтобы деплой не падал

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI()
application: Application | None = None


async def _build_application() -> Application:
    from .config import BOT_TOKEN, PUBLIC_URL
    app_ = ApplicationBuilder().token(BOT_TOKEN).build()

    # базовые хэндлеры
    register_handlers(app_)

    # админ-UI
    if register_admin_ui:
        try:
            register_admin_ui(app_)
            logger.info("Admin UI handlers registered.")
        except Exception as e:
            logger.warning("Admin UI not registered: %s", e)

    # вебхук
    if PUBLIC_URL:
        await app_.bot.set_webhook(f"{PUBLIC_URL.rstrip('/')}/telegram")
        logger.info("Webhook set to %s/telegram", PUBLIC_URL.rstrip('/'))
    else:
        logger.info("PUBLIC_URL is empty; running without setWebhook")

    return app_


@app.on_event("startup")
async def on_startup():
    global application
    application = await _build_application()
    logger.info("Startup complete.")


@app.post("/telegram")
async def telegram(request: Request):
    global application
    if application is None:
        application = await _build_application()

    data = await request.json()
    try:
        update = Update.de_json(data, application.bot)
        # диагностика
        try:
            utype = (
                "message" if getattr(update, "message", None) else
                "callback_query" if getattr(update, "callback_query", None) else
                "other"
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
