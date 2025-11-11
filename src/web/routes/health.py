# from fastapi import APIRouter
# from fastapi.responses import PlainTextResponse

# router = APIRouter()

# @router.get("/healthz")
# async def healthz():
#     return PlainTextResponse("ok")

from fastapi import APIRouter
from fastapi.responses import JSONResponse
import redis.asyncio as redis
from sqlalchemy import text

from core.config import settings
from db.engine import SessionLocal

router = APIRouter()

@router.get("/healthz")
async def healthz():
    """Простая проверка"""
    return {"status": "ok"}

@router.get("/health/deep")
async def health_deep():
    """Глубокая проверка всех сервисов"""
    status = {"overall": "ok", "services": {}}
    
    # Проверка БД
    try:
        async with SessionLocal() as s:
            await s.execute(text("SELECT 1"))
        status["services"]["database"] = "ok"
    except Exception as e:
        status["services"]["database"] = f"error: {str(e)[:100]}"
        status["overall"] = "degraded"
    
    # Проверка Redis FSM
    try:
        r = redis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_FSM)
        await r.ping()
        await r.aclose()
        status["services"]["redis_fsm"] = "ok"
    except Exception as e:
        status["services"]["redis_fsm"] = f"error: {str(e)[:100]}"
        status["overall"] = "degraded"
    
    # Проверка Redis Cache
    try:
        r = redis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_CACHE)
        await r.ping()
        await r.aclose()
        status["services"]["redis_cache"] = "ok"
    except Exception as e:
        status["services"]["redis_cache"] = f"error: {str(e)[:100]}"
        status["overall"] = "degraded"
    
    return JSONResponse(status, status_code=200 if status["overall"] == "ok" else 503)