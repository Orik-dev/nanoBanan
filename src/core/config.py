"""Application configuration for NanoBanana.

This module defines the `Settings` class that loads configuration values
from environment variables using Pydantic.  These settings include
Telegram tokens, RunBlob API keys, YooKassa credentials, database
connections and other runtime parameters.  A single instance of
`settings` is created at import time for convenience.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings
from pydantic import computed_field


class Settings(BaseSettings):

    # General environment
    ENV: str = "prod"
    TZ: str = "Asia/Baku"

    # Telegram bot configuration
    TELEGRAM_BOT_TOKEN: str
    WEBHOOK_USE: bool = True
    PUBLIC_BASE_URL: str
    WEBHOOK_SECRET_TOKEN: str
    ADMIN_ID: int | None = None 
    FFMPEG_PATH: str = "/usr/bin/ffmpeg"
    #Freepik
    FREEPIK_API_KEY: str
    FREEPIK_BASE: str = "https://api.freepik.com/v1/ai/gemini-2-5-flash-image-preview"
    FREEPIK_WEBHOOK_SECRET: str  # секрет для подписи вебхуков HMAC-SHA256 (строка)

    # KIE.ai (Nano Banana)
    KIE_API_KEY: str
    KIE_BASE: str = "https://api.kie.ai/api/v1"
    KIE_MODEL: str = "google/nano-banana-edit"
    KIE_OUTPUT_FORMAT: str = "png"   # png | jpeg
    KIE_IMAGE_SIZE: str = "auto"     # auto | 1:1 | 3:4 | 9:16 | 4:3 | 16:9
    
    # RunBlob (Gemini) API configuration
    RUNBLOB_API_KEY: str
    RUNBLOB_BASE: str = "https://api.runblob.io/api/v1/gemini"

    # YooKassa configuration
    YOOKASSA_SHOP_ID: str
    YOOKASSA_SECRET_KEY: str
    CURRENCY: str = "RUB"
    TOPUP_RETURN_URL: str

    # MySQL database configuration
    DB_HOST: str
    DB_PORT: int = 3310
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str
    
    BROADCAST_RPS: int = 10
    BROADCAST_CONCURRENCY: int = 5
    BROADCAST_BATCH: int = 100

    # Redis configuration
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB_FSM: int = 1
    REDIS_DB_CACHE: int = 2
    RATE_LIMIT_PER_MIN: int = 30
    REDIS_PASSWORD: str | None = None  #
    REDIS_DB_BROADCAST: int = 3 
    
    MAX_TASK_WAIT_S: int = 150
    ARQ_JOB_TIMEOUT_OFFSET_S: int = 60  # запас для ARQ

    @computed_field
    @property
    def ARQ_JOB_TIMEOUT_S(self) -> int:
        return max(self.MAX_TASK_WAIT_S + self.ARQ_JOB_TIMEOUT_OFFSET_S, 360)
    

    @computed_field
    @property
    def DB_DSN(self) -> str:
        """Assemble an async MySQL DSN from discrete components."""
        return (
            f"mysql+aiomysql://{self.DB_USER}:{self.DB_PASSWORD}@"
            f"{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?charset=utf8mb4"
        )
    
settings = Settings()
