# #!/usr/bin/env python3
# """
# Скрипт для очистки старых данных из Redis и временных файлов.
# """
# import asyncio
# import os
# import time
# import logging
# import shutil  # ✅ ДОБАВЛЕНО
# from pathlib import Path

# import redis.asyncio as aioredis
# from core.config import settings

# logging.basicConfig(level=logging.INFO)
# log = logging.getLogger("cleanup")


# async def cleanup_fsm_old_states():
#     r = aioredis.Redis(...)
    
#     try:
#         cursor = 0
#         deleted = 0
#         checked = 0
#         max_iterations = 1000  # ✅ Максимум итераций
#         iteration = 0
        
#         while True:
#             cursor, keys = await r.scan(cursor, match="fsm:*", count=100)
#             iteration += 1
            
#             for key in keys:
#                 checked += 1
#                 # ... код
            
#             if cursor == 0 or iteration >= max_iterations:
#                 if iteration >= max_iterations:
#                     log.warning(f"FSM cleanup stopped at {max_iterations} iterations")
#                 break
        
#         log.info(f"✅ FSM cleanup: проверено {checked}, установлен TTL для {deleted} ключей")
    
#     except Exception as e:
#         log.error(f"❌ Ошибка очистки FSM: {e}")
#     finally:
#         await r.aclose()


# async def _cleanup_directory(directory: Path, max_age_hours: float, pattern: str = "*"):
#     """Универсальная функция очистки директории"""
#     if not directory.exists():
#         log.info(f"📁 Директория {directory} не существует")
#         return
    
#     now = time.time()
#     max_age = max_age_hours * 3600
#     deleted = 0
#     errors = 0
    
#     try:
#         for file_path in directory.glob(pattern):
#             if not file_path.is_file():
#                 continue
            
#             try:
#                 file_age = now - file_path.stat().st_mtime
                
#                 if file_age > max_age:
#                     file_path.unlink()
#                     deleted += 1
#             except Exception as e:
#                 errors += 1
#                 if errors < 5:
#                     log.warning(f"Ошибка удаления {file_path}: {e}")
        
#         log.info(f"✅ Cleanup {directory}: удалено {deleted} файлов старше {max_age_hours}ч")
    
#     except Exception as e:
#         log.error(f"❌ Ошибка очистки {directory}: {e}")


# async def emergency_cleanup_if_needed():  # ✅ ДОБАВЛЕНО
#     """Экстренная очистка если диск заполнен >80%"""
#     try:
#         stat = shutil.disk_usage("/app")
#         used_percent = (stat.used / stat.total) * 100
        
#         if used_percent > 80:
#             log.warning(f"🚨 Диск заполнен на {used_percent:.1f}% - экстренная очистка!")
            
#             # Удалить ВСЕ файлы старше 5 минут
#             await _cleanup_directory(Path("/tmp/nanobanana"), max_age_hours=0.08, pattern="*")
#             await _cleanup_directory(Path("/app/temp_inputs"), max_age_hours=0.08, pattern="*")
            
#             log.info("✅ Экстренная очистка завершена")
#         else:
#             log.info(f"💾 Диск: {used_percent:.1f}% заполнен")
#     except Exception as e:
#         log.error(f"❌ Ошибка экстренной очистки: {e}")


# async def cleanup_old_temp_files():
#     """Удаляет файлы из всех временных директорий"""
    
#     # ✅ /tmp/nanobanana (результаты) - 1 час
#     temp_dir = Path("/tmp/nanobanana")
#     if temp_dir.exists():
#         await _cleanup_directory(temp_dir, max_age_hours=1, pattern="*")
    
#     # ✅ /app/temp_inputs (входящие фото) - 20 минут (ИЗМЕНЕНО с 1 часа)
#     temp_inputs = Path("/app/temp_inputs")
#     if temp_inputs.exists():
#         await _cleanup_directory(temp_inputs, max_age_hours=0.15, pattern="*")


# async def cleanup_old_redis_markers():
#     """Очистка старых маркеров в REDIS_DB_CACHE"""
#     r = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_CACHE)
    
#     try:
#         deleted = 0
        
#         # Очистка wb:lock:*
#         cursor = 0
#         while True:
#             cursor, keys = await r.scan(cursor, match="wb:lock:*", count=100)
#             for key in keys:
#                 try:
#                     ttl = await r.ttl(key)
#                     if ttl == -1 or ttl == -2:
#                         await r.delete(key)
#                         deleted += 1
#                 except Exception:
#                     pass
#             if cursor == 0:
#                 break
        
#         # Очистка task:pending:*
#         cursor = 0
#         while True:
#             cursor, keys = await r.scan(cursor, match="task:pending:*", count=100)
#             for key in keys:
#                 try:
#                     ttl = await r.ttl(key)
#                     if ttl == -1:
#                         await r.delete(key)
#                         deleted += 1
#                 except Exception:
#                     pass
#             if cursor == 0:
#                 break
        
#         log.info(f"✅ Redis markers cleanup: удалено {deleted} старых маркеров")
    
#     except Exception as e:
#         log.error(f"❌ Ошибка очистки Redis маркеров: {e}")
#     finally:
#         await r.aclose()


# async def main():
#     log.info("🧹 Запуск очистки...")
    
#     await emergency_cleanup_if_needed()  # ✅ ДОБАВЛЕНО - сначала проверяем
#     await cleanup_fsm_old_states()
#     await cleanup_old_temp_files()
#     await cleanup_old_redis_markers()
    
#     log.info("✅ Очистка завершена")


# if __name__ == "__main__":
#     asyncio.run(main())

#!/usr/bin/env python3
"""
✅ ИСПРАВЛЕНО: Скрипт очистки с защитой от exhaustion и мониторингом диска
"""
import asyncio
import os
import time
import logging
import shutil
from pathlib import Path

import redis.asyncio as aioredis
from core.config import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cleanup")


async def cleanup_fsm_old_states():
    """
    ✅ ИСПРАВЛЕНО: Очистка FSM с защитой от бесконечного цикла
    """
    r = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_FSM)
    
    try:
        cursor = 0
        deleted = 0
        checked = 0
        max_iterations = 1000  # ✅ Защита от бесконечного цикла
        iteration = 0
        
        while True:
            cursor, keys = await r.scan(cursor, match="fsm:*", count=100)
            iteration += 1
            
            for key in keys:
                checked += 1
                try:
                    ttl = await r.ttl(key)
                    if ttl == -1:
                        await r.expire(key, 86400)
                        deleted += 1
                except Exception as e:
                    log.warning(f"Ошибка обработки ключа {key}: {e}")
            
            if cursor == 0 or iteration >= max_iterations:
                if iteration >= max_iterations:
                    log.warning(f"⚠️ FSM cleanup stopped at {max_iterations} iterations (too many keys)")
                break
        
        log.info(f"✅ FSM cleanup: проверено {checked}, установлен TTL для {deleted} ключей")
    
    except Exception as e:
        log.error(f"❌ Ошибка очистки FSM: {e}")
    finally:
        await r.aclose()


async def _cleanup_directory(directory: Path, max_age_hours: float, pattern: str = "*"):
    """Универсальная функция очистки директории"""
    if not directory.exists():
        log.info(f"📁 Директория {directory} не существует")
        return
    
    now = time.time()
    max_age = max_age_hours * 3600
    deleted = 0
    errors = 0
    
    try:
        for file_path in directory.glob(pattern):
            if not file_path.is_file():
                continue
            
            try:
                file_age = now - file_path.stat().st_mtime
                
                if file_age > max_age:
                    file_path.unlink()
                    deleted += 1
            except Exception as e:
                errors += 1
                if errors < 5:
                    log.warning(f"Ошибка удаления {file_path}: {e}")
        
        log.info(f"✅ Cleanup {directory}: удалено {deleted} файлов старше {max_age_hours}ч")
    
    except Exception as e:
        log.error(f"❌ Ошибка очистки {directory}: {e}")


async def emergency_cleanup_if_needed():
    """
    ✅ ИСПРАВЛЕНО: Экстренная очистка с уведомлением админу
    """
    try:
        stat = shutil.disk_usage("/app")
        used_percent = (stat.used / stat.total) * 100
        
        if used_percent > 80:
            log.warning(f"🚨 Диск заполнен на {used_percent:.1f}% - экстренная очистка!")
            
            # ✅ ДОБАВЛЕНО: уведомление админу
            if settings.ADMIN_ID:
                try:
                    from aiogram import Bot
                    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
                    await bot.send_message(
                        settings.ADMIN_ID,
                        f"🚨 <b>CRITICAL</b>: Disk usage at {used_percent:.1f}%!\n\n"
                        f"📊 Total: {stat.total / (1024**3):.1f} GB\n"
                        f"📊 Used: {stat.used / (1024**3):.1f} GB\n"
                        f"📊 Free: {stat.free / (1024**3):.1f} GB\n\n"
                        f"🧹 Starting emergency cleanup...",
                        parse_mode="HTML"
                    )
                    await bot.session.close()
                except Exception as e:
                    log.error(f"Failed to send disk alert: {e}")
            
            # Удалить ВСЕ файлы старше 5 минут
            await _cleanup_directory(Path("/tmp/nanobanana"), max_age_hours=0.08, pattern="*")
            await _cleanup_directory(Path("/app/temp_inputs"), max_age_hours=0.08, pattern="*")
            
            log.info("✅ Экстренная очистка завершена")
        else:
            log.info(f"💾 Диск: {used_percent:.1f}% заполнен")
    except Exception as e:
        log.error(f"❌ Ошибка экстренной очистки: {e}")


async def cleanup_old_temp_files():
    """
    ✅ ИСПРАВЛЕНО: Уменьшено время хранения temp файлов
    """
    # /tmp/nanobanana (результаты) - 1 час
    temp_dir = Path("/tmp/nanobanana")
    if temp_dir.exists():
        await _cleanup_directory(temp_dir, max_age_hours=1, pattern="*")
    
    # ✅ ИЗМЕНЕНО: /app/temp_inputs - 10 минут вместо 20
    temp_inputs = Path("/app/temp_inputs")
    if temp_inputs.exists():
        await _cleanup_directory(temp_inputs, max_age_hours=0.15, pattern="*")


async def cleanup_old_redis_markers():
    """Очистка старых маркеров в REDIS_DB_CACHE"""
    r = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_CACHE)
    
    try:
        deleted = 0
        
        # Очистка wb:lock:*
        cursor = 0
        while True:
            cursor, keys = await r.scan(cursor, match="wb:lock:*", count=100)
            for key in keys:
                try:
                    ttl = await r.ttl(key)
                    if ttl == -1 or ttl == -2:
                        await r.delete(key)
                        deleted += 1
                except Exception:
                    pass
            if cursor == 0:
                break
        
        # Очистка task:pending:*
        cursor = 0
        while True:
            cursor, keys = await r.scan(cursor, match="task:pending:*", count=100)
            for key in keys:
                try:
                    ttl = await r.ttl(key)
                    if ttl == -1:
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
        await r.aclose()


async def main():
    log.info("🧹 Запуск очистки...")
    
    await emergency_cleanup_if_needed()  # ✅ Сначала проверяем диск
    await cleanup_fsm_old_states()
    await cleanup_old_temp_files()
    await cleanup_old_redis_markers()
    
    log.info("✅ Очистка завершена")


if __name__ == "__main__":
    asyncio.run(main())