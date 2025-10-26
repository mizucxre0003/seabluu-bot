# app/webhook.py
import os
import logging

from fastapi import FastAPI, Request
from fastapi.responses import Response

from telegram import Update
from telegram.ext import Application, ApplicationBuilder

from .main import register_handlers
try:
    from .main import register_admin_ui
except Exception:
    register_admin_ui = None  # безопасно, если функции нет

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI()
application: Application | None = None


def _get_bot_token() -> str:
    # 1) из config.py
    try:
        from .config import BOT_TOKEN as _TOK  # type: ignore
        if _TOK:
            return _TOK
    except Exception:
        pass
    # 2) из окружения
    env_tok = os.getenv("BOT_TOKEN", "")
    if not env_tok:
        raise RuntimeError("BOT_TOKEN is not set (neither in app.config nor in environment)")
    return env_tok


def _get_public_url() -> str:
    # 1) из config.py
    try:
        from .config import PUBLIC_URL as _URL  # type: ignore
        if _URL:
            return _URL
    except Exception:
        pass
    # 2) из окружения
    return os.getenv("PUBLIC_URL", "")


async def _build_application() -> Application:
    """Создаёт Application, регистрирует хэндлеры, настраивает вебхук (без инициализации)."""
    bot_token = _get_bot_token()
    public_url = _get_public_url()

    app_ = ApplicationBuilder().token(bot_token).build()

    # базовые хэндлеры
    register_handlers(app_)

    # админ-UI (если есть)
    if register_admin_ui:
        try:
            register_admin_ui(app_)
            logger.info("Admin UI handlers registered.")
        except Exception as e:
            logger.warning("Admin UI not registered: %s", e)

    # вебхук
    if public_url:
        url = f"{public_url.rstrip('/')}/telegram"
        await app_.bot.set_webhook(url)
        logger.info("Webhook set to %s", url)
    else:
        logger.warning("PUBLIC_URL is empty or missing; skipping setWebhook")

    return app_


async def _ensure_ready():
    """Гарантирует, что Application создано, initialize()/start() вызваны."""
    global application
    if application is None:
        application = await _build_application()

    # initialize + start обязательны в PTB v21 при внешнем фреймворке
    try:
        await application.initialize()
    except Exception:
        # если уже инициализировано — ок
        pass
    try:
        await application.start()
    except Exception:
        # если уже запущено — ок
        pass


@app.on_event("startup")
async def on_startup():
    global application
    application = await _build_application()
    # ВАЖНО: инициализация и старт
    await application.initialize()
    await application.start()
    logger.info("Startup complete: application initialized & started.")


@app.on_event("shutdown")
async def on_shutdown():
    # Корректное завершение
    if application is not None:
        try:
            await application.stop()
        finally:
            await application.shutdown()
    logger.info("Shutdown complete.")


@app.post("/telegram")
async def telegram(request: Request):
    await _ensure_ready()

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
