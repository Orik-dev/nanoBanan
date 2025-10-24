# db/engine.py
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from core.config import settings

# 💡 Агрессивные, но безопасные таймауты + маленький пул для шаред-MySQL
engine = create_async_engine(
    settings.DB_DSN,
    pool_pre_ping=True,
    pool_recycle=180,          
    pool_size=15,
    pool_timeout=2,
    pool_use_lifo=True,
    max_overflow=45,
    connect_args={
        "connect_timeout": 3,  # не висеть долго на TCP connect
    },
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
