#!/usr/bin/env python3
"""
Скрипт для очистки старых данных из Redis и временных файлов.
Запускать через cron или docker-compose каждый час.

⚠️ ВАЖНО: Этот скрипт НЕ УДАЛЯЕТ FSM состояния!
Он ТОЛЬКО ставит TTL (24 часа) для ключей БЕЗ TTL.
"""
import asyncio
import os
import time
import logging
from pathlib import Path

import redis.asyncio as aioredis
from core.config import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cleanup")


async def cleanup_fsm_old_states():
    """
    Очистка старых FSM состояний (старше 24 часов).
    
    ⚠️ НЕ УДАЛЯЕТ FSM! Только ставит TTL для ключей без TTL.
    
    FSM ключи имеют формат: fsm:{bot_id}:{chat_id}:{chat_id}:state
    """
    r = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_FSM)
    
    try:
        # Получаем все ключи FSM
        cursor = 0
        deleted = 0
        checked = 0
        
        while True:
            cursor, keys = await r.scan(cursor, match="fsm:*", count=100)
            
            for key in keys:
                checked += 1
                try:
                    # Проверяем TTL:
                    # -1 = нет TTL (ключ висит вечно)
                    # -2 = ключ не существует
                    # >0 = TTL уже установлен
                    ttl = await r.ttl(key)
                    
                    # ✅ ТОЛЬКО если НЕТ TTL - ставим 24 часа
                    if ttl == -1:
                        await r.expire(key, 86400)  # 24 часа
                        deleted += 1
                    # Если TTL уже есть - НЕ ТРОГАЕМ!
                        
                except Exception as e:
                    log.warning(f"Ошибка обработки ключа {key}: {e}")
            
            if cursor == 0:
                break
        
        log.info(f"✅ FSM cleanup: проверено {checked}, установлен TTL для {deleted} ключей")
    
    except Exception as e:
        log.error(f"❌ Ошибка очистки FSM: {e}")
    finally:
        await r.aclose()  # ✅ ИСПРАВЛЕНО: было close()


async def cleanup_old_temp_files():
    """Удаляет файлы старше 6 часов из /tmp/nanobanana"""
    temp_dir = Path("/tmp/nanobanana")
    
    if not temp_dir.exists():
        log.info("📁 Директория /tmp/nanobanana не существует")
        return
    
    now = time.time()
    max_age = 6 * 3600  # 6 часов
    deleted = 0
    
    try:
        for file_path in temp_dir.glob("*.png"):
            try:
                file_age = now - file_path.stat().st_mtime
                
                if file_age > max_age:
                    file_path.unlink()
                    deleted += 1
            except Exception as e:
                log.warning(f"Ошибка удаления {file_path}: {e}")
        
        log.info(f"✅ Temp files cleanup: удалено {deleted} файлов старше 6 часов")
    
    except Exception as e:
        log.error(f"❌ Ошибка очистки временных файлов: {e}")


async def cleanup_old_redis_markers():
    """
    Очистка старых маркеров в REDIS_DB_CACHE:
    - wb:lock:* (блокировки вебхуков) - старше 10 минут
    - task:pending:* - старше 1 часа
    """
    r = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_CACHE)
    
    try:
        deleted = 0
        
        # Очистка старых блокировок вебхуков
        cursor = 0
        while True:
            cursor, keys = await r.scan(cursor, match="wb:lock:*", count=100)
            for key in keys:
                try:
                    ttl = await r.ttl(key)
                    # Если TTL истек или ключ "висит" без TTL
                    if ttl == -1 or ttl == -2:
                        await r.delete(key)
                        deleted += 1
                except Exception:
                    pass
            if cursor == 0:
                break
        
        # Очистка старых pending маркеров
        cursor = 0
        while True:
            cursor, keys = await r.scan(cursor, match="task:pending:*", count=100)
            for key in keys:
                try:
                    ttl = await r.ttl(key)
                    if ttl == -1:  # Если нет TTL - удаляем (должно быть TTL)
                        await r.delete(key)
                        deleted += 1
                except Exception:
                    pass
            if cursor == 0:
                break
        
        log.info(f"✅ Redis markers cleanup: удалено {deleted} старых маркеров")
    
    except Exception as e:
        log.error(f"❌ Ошибка очистки Redis маркеров: {e}")
    finally:
        await r.aclose()  # ✅ ИСПРАВЛЕНО: было close()


async def main():
    log.info("🧹 Запуск очистки...")
    
    await cleanup_fsm_old_states()
    await cleanup_old_temp_files()
    await cleanup_old_redis_markers()
    
    log.info("✅ Очистка завершена")


if __name__ == "__main__":
    asyncio.run(main())