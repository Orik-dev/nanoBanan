
# """
# Telegram logger handler - отправляет критические ошибки админу
# """
# import asyncio
# import logging
# import hashlib
# from datetime import datetime
# from typing import Optional

# import redis.asyncio as aioredis
# from aiogram import Bot

# from core.config import settings


# class TelegramLogHandler(logging.Handler):
#     """
#     Handler для отправки ERROR и CRITICAL логов в Telegram админу
#     """
#     def __init__(self, bot: Bot, admin_id: int):
#         super().__init__(level=logging.ERROR)  # ✅ Только ERROR и выше
#         self.bot = bot
#         self.admin_id = admin_id
#         self._redis: Optional[aioredis.Redis] = None
#         self._loop: Optional[asyncio.AbstractEventLoop] = None
    
#     async def _get_redis(self) -> aioredis.Redis:
#         """Ленивая инициализация Redis"""
#         if self._redis is None:
#             self._redis = aioredis.Redis(
#                 host=settings.REDIS_HOST,
#                 port=settings.REDIS_PORT,
#                 db=settings.REDIS_DB_CACHE
#             )
#         return self._redis
    
#     def _format_error(self, record: logging.LogRecord) -> str:
#         """Форматирование ошибки для Telegram"""
#         timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        
#         # Базовая информация
#         lines = [
#             f"🚨 <b>{record.levelname}</b>",
#             f"📅 {timestamp}",
#             f"📂 {record.name}",
#             f"📍 {record.filename}:{record.lineno}",
#             "",
#             f"<b>Сообщение:</b>",
#             f"<code>{record.getMessage()[:500]}</code>",
#         ]
        
#         # Traceback если есть
#         if record.exc_info:
#             import traceback
#             tb = ''.join(traceback.format_exception(*record.exc_info))
#             # Ограничиваем длину traceback
#             tb = tb[-2000:]  # Последние 2000 символов
#             lines.append("")
#             lines.append("<b>Traceback:</b>")
#             lines.append(f"<code>{tb}</code>")
        
#         message = "\n".join(lines)
        
#         # Telegram лимит 4096 символов
#         if len(message) > 4000:
#             message = message[:3900] + "\n\n... (обрезано)"
        
#         return message
    
#     def _get_error_hash(self, record: logging.LogRecord) -> str:
#         """Хэш ошибки для дедупликации"""
#         key_parts = [
#             record.name,
#             record.levelname,
#             record.getMessage()[:200],
#             f"{record.filename}:{record.lineno}"
#         ]
#         key = "|".join(key_parts)
#         return hashlib.md5(key.encode()).hexdigest()
    
#     async def _should_send(self, error_hash: str) -> bool:
#         """Проверка через Redis - не отправляли ли эту ошибку недавно"""
#         try:
#             redis = await self._get_redis()
#             key = f"tg_log:{error_hash}"
            
#             # Если ключ существует - ошибка уже отправлялась
#             exists = await redis.exists(key)
#             if exists:
#                 return False
            
#             # Устанавливаем ключ на 5 минут (не спамим одинаковыми ошибками)
#             await redis.setex(key, 300, "1")
#             return True
#         except Exception:
#             # Если Redis недоступен - отправляем
#             return True
    
#     def emit(self, record: logging.LogRecord):
#         """Отправка лога в Telegram"""
#         try:
#             # Получаем event loop
#             try:
#                 loop = asyncio.get_running_loop()
#             except RuntimeError:
#                 # Если нет running loop - создаем задачу для нового
#                 asyncio.run(self._async_emit(record))
#                 return
            
#             # Если loop уже есть - создаем задачу
#             loop.create_task(self._async_emit(record))
        
#         except Exception as e:
#             # Не ломаем приложение если не можем отправить лог
#             print(f"TelegramLogHandler error: {e}")
    
#     async def _async_emit(self, record: logging.LogRecord):
#         """Асинхронная отправка"""
#         try:
#             error_hash = self._get_error_hash(record)
            
#             # Проверяем дедупликацию
#             if not await self._should_send(error_hash):
#                 return
            
#             message = self._format_error(record)
            
#             await self.bot.send_message(
#                 self.admin_id,
#                 message,
#                 parse_mode="HTML"
#             )
        
#         except Exception as e:
#             # Не ломаем приложение
#             print(f"Failed to send log to Telegram: {e}")
    
#     async def close_async(self):
#         """Закрытие Redis соединения"""
#         if self._redis:
#             await self._redis.aclose()

"""
Telegram logger handler - отправляет критические ошибки админу
"""
import asyncio
import logging
import hashlib
from datetime import datetime
from typing import Optional
import html  # ✅ ДОБАВЛЕНО

import redis.asyncio as aioredis
from aiogram import Bot

from core.config import settings


class TelegramLogHandler(logging.Handler):
    """
    Handler для отправки ERROR и CRITICAL логов в Telegram админу
    """
    def __init__(self, bot: Bot, admin_id: int):
        super().__init__(level=logging.ERROR)
        self.bot = bot
        self.admin_id = admin_id
        self._redis: Optional[aioredis.Redis] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
    
    async def _get_redis(self) -> aioredis.Redis:
        """Ленивая инициализация Redis"""
        if self._redis is None:
            self._redis = aioredis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB_CACHE
            )
        return self._redis
    
    def _format_error(self, record: logging.LogRecord) -> str:
        """✅ ИСПРАВЛЕНО: Форматирование ошибки с экранированием HTML"""
        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        
        # ✅ Экранируем все текстовые данные
        logger_name = html.escape(record.name)
        filename = html.escape(record.filename)
        message = html.escape(record.getMessage()[:500])
        
        lines = [
            f"🚨 <b>{record.levelname}</b>",
            f"📅 {timestamp}",
            f"📂 {logger_name}",
            f"📍 {filename}:{record.lineno}",
            "",
            f"<b>Сообщение:</b>",
            f"<pre>{message}</pre>",  # ✅ используем <pre> вместо <code>
        ]
        
        # Traceback если есть
        if record.exc_info:
            import traceback
            tb = ''.join(traceback.format_exception(*record.exc_info))
            tb = tb[-2000:]
            tb_escaped = html.escape(tb)  # ✅ Экранируем traceback
            lines.append("")
            lines.append("<b>Traceback:</b>")
            lines.append(f"<pre>{tb_escaped}</pre>")
        
        message = "\n".join(lines)
        
        # Telegram лимит 4096 символов
        if len(message) > 4000:
            message = message[:3900] + "\n\n... (обрезано)"
        
        return message
    
    def _get_error_hash(self, record: logging.LogRecord) -> str:
        """Хэш ошибки для дедупликации"""
        key_parts = [
            record.name,
            record.levelname,
            record.getMessage()[:200],
            f"{record.filename}:{record.lineno}"
        ]
        key = "|".join(key_parts)
        return hashlib.md5(key.encode()).hexdigest()
    
    async def _should_send(self, error_hash: str) -> bool:
        """Проверка через Redis - не отправляли ли эту ошибку недавно"""
        try:
            redis = await self._get_redis()
            key = f"tg_log:{error_hash}"
            
            exists = await redis.exists(key)
            if exists:
                return False
            
            await redis.setex(key, 300, "1")
            return True
        except Exception:
            return True
    
    def emit(self, record: logging.LogRecord):
        """Отправка лога в Telegram"""
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(self._async_emit(record))
                return
            
            loop.create_task(self._async_emit(record))
        
        except Exception as e:
            print(f"TelegramLogHandler error: {e}")
    
    async def _async_emit(self, record: logging.LogRecord):
        """Асинхронная отправка"""
        try:
            error_hash = self._get_error_hash(record)
            
            if not await self._should_send(error_hash):
                return
            
            message = self._format_error(record)
            
            await self.bot.send_message(
                self.admin_id,
                message,
                parse_mode="HTML"
            )
        
        except Exception as e:
            print(f"Failed to send log to Telegram: {e}")
    
    async def close_async(self):
        """Закрытие Redis соединения"""
        if self._redis:
            await self._redis.aclose()