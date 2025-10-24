import asyncio
import redis.asyncio as redis

from fastapi import FastAPI
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.redis import RedisStorage, DefaultKeyBuilder
from aiogram.exceptions import TelegramRetryAfter

from core.config import settings
from core.logging import configure_json_logging

from bot.middlewares import ErrorLoggingMiddleware, RateLimitMiddleware
from bot.routers import commands as r_cmd
from bot.routers import payments as r_payments
from bot.routers import generation as r_generation

from web.routes import tg as rt_tg
from web.routes import yookassa as rt_yk
from web.routes import health as rt_health
from web.routes import misc as rt_misc
from web.routes import runblob as rt_rb
from bot.routers import voice as r_voice
from bot.routers import broadcast as r_broadcast 
# from web.routes import freepik as rt_freepik
# from web.routes import kie as rt_kie


configure_json_logging()
app = FastAPI(title="NanoBanana", version="1.0.0")

bot = Bot(token=settings.TELEGRAM_BOT_TOKEN,
          default=DefaultBotProperties(parse_mode=ParseMode.HTML))
redis_fsm = redis.from_url(f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB_FSM}")
storage = RedisStorage(redis=redis_fsm, key_builder=DefaultKeyBuilder(with_bot_id=True))
dp = Dispatcher(storage=storage)

# include routers
dp.include_router(r_voice.router)
dp.include_router(r_broadcast.router)
dp.include_router(r_cmd.router)
dp.include_router(r_payments.router)
dp.include_router(r_generation.router)



# middlewares
dp.message.middleware(ErrorLoggingMiddleware())
dp.message.middleware(
    RateLimitMiddleware(
        redis.from_url(f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB_CACHE}"),
        settings.RATE_LIMIT_PER_MIN,
    )
)

dp.callback_query.middleware(ErrorLoggingMiddleware())
dp.callback_query.middleware(
    RateLimitMiddleware(
        redis.from_url(f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB_CACHE}"),
        settings.RATE_LIMIT_PER_MIN,
    )
)


app.state.bot = bot
app.state.dp = dp
app.state.webhook_secret = settings.WEBHOOK_SECRET_TOKEN

# FastAPI routes
app.include_router(rt_tg.router)
app.include_router(rt_yk.router)
app.include_router(rt_health.router)
app.include_router(rt_misc.router)
app.include_router(rt_rb.router)
# app.include_router(rt_freepik.router)
# app.include_router(rt_kie.router)

@app.on_event("startup")
async def on_startup():
    if settings.WEBHOOK_USE:
        try:
            await bot.set_webhook(
                url=f"{settings.PUBLIC_BASE_URL}/tg/webhook",
                secret_token=settings.WEBHOOK_SECRET_TOKEN,
                drop_pending_updates=True,
            )
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.set_webhook(
                    url=f"{settings.PUBLIC_BASE_URL}/tg/webhook",
                    secret_token=settings.WEBHOOK_SECRET_TOKEN,
                    drop_pending_updates=True,
                )
            except TelegramRetryAfter:
                pass


@app.on_event("shutdown")
async def on_shutdown():
    await bot.session.close()
