# from __future__ import annotations

# import base64
# import hashlib
# import json
# import logging
# import mimetypes
# from typing import Any, Dict, List, Optional

# import httpx
# import redis.asyncio as aioredis
# from aiogram import Bot
# from aiogram.exceptions import TelegramForbiddenError
# from aiogram.fsm.context import FSMContext
# from aiogram.fsm.storage.base import StorageKey
# from aiogram.fsm.storage.redis import DefaultKeyBuilder, RedisStorage
# from arq import create_pool
# from arq.connections import RedisSettings
# from sqlalchemy import select, update
# from sqlalchemy.exc import OperationalError
# from uuid import uuid4

# from core.config import settings
# from db.engine import SessionLocal
# from db.models import Task, User
# from services.pricing import CREDITS_PER_GENERATION
# from vendors.runblob import RunBlobClient, RunBlobError
# from services.broadcast import broadcast_send

# log = logging.getLogger("worker")


# def _j(event: str, **fields) -> str:
#     return json.dumps({"event": event, **fields}, ensure_ascii=False)


# def _guess_mime_from_headers_or_path(resp: httpx.Response, file_path: str) -> str:
#     ct = (resp.headers.get("Content-Type") or "").lower().split(";")[0].strip()
#     if ct.startswith("image/"):
#         return ct
#     mt, _ = mimetypes.guess_type(file_path or "")
#     return mt or "image/jpeg"


# async def _tg_file_to_image_dict(bot: Bot, file_id: str, *, cid: str) -> Dict[str, Any]:
#     """
#     ✅ Возвращает bytes + mime (поддерживается API RunBlob)
#     """
#     f = await bot.get_file(file_id)
#     file_url = f"https://api.telegram.org/file/bot{settings.TELEGRAM_BOT_TOKEN}/{f.file_path}"

#     async with httpx.AsyncClient(timeout=60) as client:
#         resp = await client.get(file_url)
#         resp.raise_for_status()
#         content = resp.content
#         mime = _guess_mime_from_headers_or_path(resp, f.file_path)

#     size = len(content)
#     sha = hashlib.sha256(content).hexdigest()
#     # log.info(_j("queue.fetch_tg_file.ok", cid=cid, file_path=f.file_path, mime=mime, size=size, sha256=sha))

#     ALLOWED_MIMES = {"image/png", "image/jpeg", "image/webp"}
#     MAX_BYTES = 7 * 1024 * 1024

#     if mime not in ALLOWED_MIMES:
#         log.error(_j("queue.image_unsupported_mime", cid=cid, mime=mime))
#         raise ValueError("unsupported image mime")

#     if size > MAX_BYTES:
#         log.error(_j("queue.image_too_large", cid=cid, size=size, max=MAX_BYTES))
#         raise ValueError("image too large")

#     b64 = base64.b64encode(content).decode("ascii")
#     log.info(_j("queue.image.prepared", cid=cid, mime=mime, b64_len=len(b64)))
#     return {"bytes": b64, "mime": mime}


# async def enqueue_generation(chat_id: int, prompt: str, photos: List[str], aspect_ratio: Optional[str] = None) -> None:
#     redis_pool = await create_pool(
#         RedisSettings(
#             host=settings.REDIS_HOST,
#             port=settings.REDIS_PORT,
#             database=settings.REDIS_DB_CACHE,
#         )
#     )
#     await redis_pool.enqueue_job("process_generation", chat_id, prompt, photos, aspect_ratio)


# async def startup(ctx: dict[str, Bot]):
#     ctx["bot"] = Bot(token=settings.TELEGRAM_BOT_TOKEN)


# async def shutdown(ctx: dict[str, Bot]):
#     """Graceful shutdown - закрываем все соединения"""
#     bot: Bot = ctx.get("bot")
#     if bot:
#         await bot.session.close()
    
#     # ✅ Закрываем все Redis клиенты
#     try:
#         import gc
#         for obj in gc.get_objects():
#             if isinstance(obj, aioredis.Redis):
#                 try:
#                     await obj.aclose()
#                 except Exception:
#                     pass
#     except Exception:
#         pass


# async def _clear_waiting_message(bot: Bot, chat_id: int) -> None:
#     r = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_FSM)
#     try:
#         storage = RedisStorage(redis=r, key_builder=DefaultKeyBuilder(with_bot_id=True))
#         me = await bot.get_me()
#         fsm = FSMContext(storage=storage, key=StorageKey(me.id, chat_id, chat_id))
#         data = await fsm.get_data()
#         msg_id = data.get("wait_msg_id")
#         if msg_id:
#             try:
#                 await bot.delete_message(chat_id, msg_id)
#             except Exception:
#                 pass
#             await fsm.update_data(wait_msg_id=None)
#     except Exception:
#         pass
#     finally:
#         await r.aclose()



# async def _maybe_refund_if_deducted(chat_id: int, task_uuid: str, amount: int, cid: str, reason: str) -> None:
#     rcache = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_CACHE)
#     deb_key = f"credits:debited:{task_uuid}"
#     try:
#         debited = await rcache.get(deb_key)
#     except Exception:
#         debited = None
#     if not debited:
#         log.info(_j("refund.skipped_not_debited", cid=cid, chat_id=chat_id, task_uuid=task_uuid))
#         return

#     try:
#         async with SessionLocal() as s:
#             q = await s.execute(select(User).where(User.chat_id == chat_id))
#             u = q.scalar_one_or_none()
#             if u is not None:
#                 await s.execute(
#                     update(User)
#                     .where(User.id == u.id)
#                     .values(balance_credits=User.balance_credits + amount)
#                 )
#                 await s.commit()
#                 log.info(_j("refund.ok", cid=cid, chat_id=chat_id, task_uuid=task_uuid, amount=amount, reason=reason))
#                 try:
#                     await rcache.delete(deb_key)
#                 except Exception:
#                     pass
#                 return
#     except Exception:
#         log.exception(_j("refund.db_error", cid=cid, task_uuid=task_uuid))


# async def process_generation(
#     ctx: dict[str, Bot], chat_id: int, prompt: str, photos: List[str], aspect_ratio: Optional[str] = None
# ) -> Dict[str, Any] | None:
#     bot: Bot = ctx["bot"]
#     api = RunBlobClient()
#     cid = uuid4().hex[:12]

#     try:
#         async with SessionLocal() as s:
#             try:
#                 q = await s.execute(select(User).where(User.chat_id == chat_id))
#                 user = q.scalar_one_or_none()
#                 if user is None:
#                     await _clear_waiting_message(bot, chat_id)
#                     try:
#                         await bot.send_message(chat_id, "Нажмите /start для инициализации")
#                     except Exception:
#                         pass
#                     log.warning(_j("queue.user_not_found", cid=cid, chat_id=chat_id))
#                     return {"ok": False, "error": "user_not_found"}
#             except OperationalError:
#                 await _clear_waiting_message(bot, chat_id)
#                 try:
#                     await bot.send_message(chat_id, "⚠️ Ошибка БД. Напишите @guard_gpt")
#                 except Exception:
#                     pass
#                 return {"ok": False, "error": "db_unavailable"}

#             if user.balance_credits < CREDITS_PER_GENERATION:
#                 await bot.send_message(chat_id, "Недостаточно генераций. /buy")
#                 return {"ok": False, "error": "insufficient_credits"}

#             images: List[Dict[str, Any]] = []
#             for fid in (photos or [])[:4]:
#                 try:
#                     images.append(await _tg_file_to_image_dict(bot, fid, cid=cid))
#                 except Exception:
#                     log.exception(_j("queue.fetch_image.failed", cid=cid, file_id=fid))

#             had_input_photos = bool(photos)
#             if had_input_photos and not images:
#                 await bot.send_message(
#                     chat_id,
#                     "Можно загрузить 1–4 изображения PNG/JPG/WebP, до 7 MB. Попробуйте снова 🙏",
#                 )
#                 return {"ok": False, "error": "images_download_failed"}

#             try:
#                 callback = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/webhook/runblob"
#                 task_uuid = await api.create_task(
#                     prompt,
#                     images=images if images else None,
#                     callback_url=callback,
#                     aspect_ratio=aspect_ratio,
#                     cid=cid,
#                 )
#             except httpx.HTTPError as e:
#                 code = getattr(getattr(e, "response", None), "status_code", None)
#                 log.warning(_j("queue.runblob_http_error", cid=cid, status_code=code))
#                 await _clear_waiting_message(bot, chat_id)
#                 try:
#                     await bot.send_message(chat_id, "⚠️ Ошибка генерации. Напишите @guard_gpt")
#                 except Exception:
#                     pass
#                 return {"ok": False, "error": f"runblob_http_{code or 'unknown'}"}

#             try:
#                 task = Task(user_id=user.id, prompt=prompt, task_uuid=task_uuid, status="queued", delivered=False)
#                 s.add(task)
#                 await s.commit()
#                 await s.refresh(task)
#             except Exception:
#                 log.warning(_j("queue.db_write_failed", cid=cid, task_uuid=task_uuid))

#         return {"ok": True, "task_uuid": task_uuid}

#     except RunBlobError as e:
#         log.error(_j("queue.runblob_error", cid=cid, err=str(e)[:500]))
#         await _clear_waiting_message(bot, chat_id)
#         if 'task_uuid' in locals():
#             await _maybe_refund_if_deducted(chat_id, task_uuid, CREDITS_PER_GENERATION, cid, reason="runblob_error")
#         try:
#             await bot.send_message(chat_id, "⚠️ Ошибка генерации. Напишите @guard_gpt")
#         except Exception:
#             pass
#         return {"ok": False, "error": str(e)[:500]}

#     except TelegramForbiddenError:
#         log.warning(_j("queue.tg_forbidden_on_start", cid=cid, chat_id=chat_id))
#         return {"ok": False, "error": "telegram_forbidden"}

#     except Exception:
#         log.exception(_j("queue.fatal", cid=cid))
#         await _clear_waiting_message(bot, chat_id)
#         if 'task_uuid' in locals():
#             await _maybe_refund_if_deducted(chat_id, task_uuid, CREDITS_PER_GENERATION, cid, reason="internal")
#         try:
#             await bot.send_message(chat_id, "⚠️ Ошибка. Напишите @guard_gpt")
#         except Exception:
#             pass
#         return {"ok": False, "error": "internal"}
    
#     finally:
#         await api.aclose()


# class WorkerSettings:
#     functions = [process_generation, broadcast_send]
#     on_startup = startup
#     on_shutdown = shutdown
#     redis_settings = RedisSettings(
#         host=settings.REDIS_HOST, port=settings.REDIS_PORT, database=settings.REDIS_DB_CACHE
#     )
#     job_timeout = 259200
#     keep_result = 0

##KIEEEEEEEEEEE
# from __future__ import annotations

# import asyncio
# import base64
# import hashlib
# import json
# import logging
# import mimetypes
# from typing import Any, Dict, List, Optional
# from pathlib import Path

# import httpx
# import redis.asyncio as aioredis
# from aiogram import Bot
# from aiogram.exceptions import TelegramForbiddenError,TelegramBadRequest
# from aiogram.fsm.context import FSMContext
# from aiogram.fsm.storage.base import StorageKey
# from aiogram.fsm.storage.redis import DefaultKeyBuilder, RedisStorage
# from arq import create_pool
# from arq.connections import RedisSettings
# from sqlalchemy import select, update
# from sqlalchemy.exc import OperationalError
# from uuid import uuid4
# from arq.cron import cron
# from services.cleanup_db import cleanup_database_task
# from services.backup_db import backup_database_task
# from core.config import settings
# from db.engine import SessionLocal
# from db.models import Task, User
# from services.pricing import CREDITS_PER_GENERATION
# from vendors.kie import KieClient, KieError
# from services.broadcast import broadcast_send

# log = logging.getLogger("worker")


# def _j(event: str, **fields) -> str:
#     return json.dumps({"event": event, **fields}, ensure_ascii=False)


# async def _tg_file_to_public_url(bot: Bot, file_id: str, *, cid: str) -> str:
#     """
#     ✅ ИСПРАВЛЕНО: проверка размера файла + обработка ошибок
#     """
#     # ✅ Сначала получаем информацию о файле
#     try:
#         f = await bot.get_file(file_id)
#     except TelegramBadRequest as e:
#         error_msg = str(e).lower()
#         if "file is too big" in error_msg:
#             log.error(_j("queue.file_too_big", cid=cid, file_id=file_id))
#             raise ValueError("file_too_big")
#         raise
    
#     # ✅ Проверяем размер (лимит 20MB для Bot API)
#     file_size = f.file_size or 0
#     max_size = 20 * 1024 * 1024  # 20 MB
    
#     if file_size > max_size:
#         log.error(_j(
#             "queue.file_size_limit", 
#             cid=cid, 
#             file_id=file_id,
#             size_mb=file_size / (1024 * 1024),
#             limit_mb=max_size / (1024 * 1024)
#         ))
#         raise ValueError("file_too_big")
    
#     file_url = f"https://api.telegram.org/file/bot{settings.TELEGRAM_BOT_TOKEN}/{f.file_path}"

#     async with httpx.AsyncClient(timeout=60) as client:
#         resp = await client.get(file_url)
#         resp.raise_for_status()
#         content = resp.content

#     temp_dir = Path("/app/temp_inputs")
    
#     try:
#         temp_dir.mkdir(exist_ok=True, parents=True)
#     except OSError as e:
#         if e.errno == 28:
#             log.error(_j("queue.disk_full", cid=cid, error="No space left on device"))
#             raise OSError("Disk full") from e
#         raise
    
#     ext = Path(f.file_path).suffix or ".jpg"
#     filename = f"{uuid4().hex}{ext}"
#     filepath = temp_dir / filename
    
#     try:
#         with open(filepath, "wb") as out:
#             out.write(content)
#     except OSError as e:
#         if e.errno == 28:
#             log.error(_j("queue.disk_full_write", cid=cid, file=filename))
#             raise OSError("Disk full") from e
#         raise
    
#     public_url = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/proxy/image/{filename}"
    
#     log.info(_j(
#         "queue.file_saved", 
#         cid=cid, 
#         filename=filename, 
#         size=len(content),
#         size_mb=round(len(content) / (1024 * 1024), 2),
#         ext=ext,
#         public_url=public_url
#     ))
    
#     return public_url

# async def enqueue_generation(
#     chat_id: int,
#     prompt: str,
#     photos: List[str],
#     aspect_ratio: Optional[str] = None
# ) -> None:
#     redis_pool = await create_pool(
#         RedisSettings(
#             host=settings.REDIS_HOST,
#             port=settings.REDIS_PORT,
#             database=settings.REDIS_DB_CACHE,
#         )
#     )
#     await redis_pool.enqueue_job("process_generation", chat_id, prompt, photos, aspect_ratio)


# async def startup(ctx: dict[str, Bot]):
#     ctx["bot"] = Bot(token=settings.TELEGRAM_BOT_TOKEN)

#     if settings.ADMIN_ID:
#         from core.telegram_logger import TelegramLogHandler
#         import logging
#         telegram_handler = TelegramLogHandler(ctx["bot"], settings.ADMIN_ID)
#         logging.getLogger().addHandler(telegram_handler)


# async def shutdown(ctx: dict[str, Bot]):
#     """
#     ✅ ИСПРАВЛЕНО: Graceful shutdown с закрытием ВСЕХ ресурсов
#     """
#     bot: Bot = ctx.get("bot")
#     if bot:
#         await bot.session.close()
    
#     # ✅ Закрываем все асинхронные ресурсы
#     import gc
#     try:
#         for obj in gc.get_objects():
#             # Redis connections
#             if isinstance(obj, aioredis.Redis):
#                 try:
#                     await obj.aclose()
#                 except Exception:
#                     pass
            
#             # ✅ ДОБАВЛЕНО: httpx клиенты
#             if isinstance(obj, httpx.AsyncClient):
#                 try:
#                     await obj.aclose()
#                 except Exception:
#                     pass
#     except Exception:
#         pass


# async def _clear_waiting_message(bot: Bot, chat_id: int) -> None:
#     r = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_FSM)
#     try:
#         storage = RedisStorage(redis=r, key_builder=DefaultKeyBuilder(with_bot_id=True))
#         me = await bot.get_me()
#         fsm = FSMContext(storage=storage, key=StorageKey(me.id, chat_id, chat_id))
#         data = await fsm.get_data()
#         msg_id = data.get("wait_msg_id")
#         if msg_id:
#             try:
#                 await bot.delete_message(chat_id, msg_id)
#             except Exception:
#                 pass
#             await fsm.update_data(wait_msg_id=None)
#     except Exception:
#         pass
#     finally:
#         await r.aclose()


# async def _maybe_refund_if_deducted(
#     chat_id: int,
#     task_uuid: str,
#     amount: int,
#     cid: str,
#     reason: str
# ) -> None:
#     rcache = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_CACHE)
#     deb_key = f"credits:debited:{task_uuid}"
#     try:
#         debited = await rcache.get(deb_key)
#     except Exception:
#         debited = None
#     finally:
#         await rcache.aclose()
    
#     if not debited:
#         log.info(_j("refund.skipped_not_debited", cid=cid, chat_id=chat_id, task_uuid=task_uuid))
#         return

#     try:
#         async with SessionLocal() as s:
#             q = await s.execute(select(User).where(User.chat_id == chat_id))
#             u = q.scalar_one_or_none()
#             if u is not None:
#                 await s.execute(
#                     update(User)
#                     .where(User.id == u.id)
#                     .values(balance_credits=User.balance_credits + amount)
#                 )
#                 await s.commit()
#                 log.info(_j("refund.ok", cid=cid, chat_id=chat_id, task_uuid=task_uuid, amount=amount, reason=reason))
#                 try:
#                     rcache2 = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_CACHE)
#                     await rcache2.delete(deb_key)
#                     await rcache2.aclose()
#                 except Exception:
#                     pass
#                 return
#     except Exception:
#         log.exception(_j("refund.db_error", cid=cid, task_uuid=task_uuid))

# async def process_generation(
#     ctx: dict[str, Bot],
#     chat_id: int,
#     prompt: str,
#     photos: List[str],
#     aspect_ratio: Optional[str] = None
# ) -> Dict[str, Any] | None:
#     """
#     ✅ ИСПРАВЛЕНО: улучшена обработка ошибок диска, больших файлов и загрузки
#     """
#     bot: Bot = ctx["bot"]
#     api = KieClient()
#     cid = uuid4().hex[:12]

#     try:
#         async with SessionLocal() as s:
#             try:
#                 q = await s.execute(select(User).where(User.chat_id == chat_id))
#                 user = q.scalar_one_or_none()
#                 if user is None:
#                     await _clear_waiting_message(bot, chat_id)
#                     try:
#                         await bot.send_message(chat_id, "Нажмите /start для инициализации")
#                     except Exception:
#                         pass
#                     log.warning(_j("queue.user_not_found", cid=cid, chat_id=chat_id))
#                     return {"ok": False, "error": "user_not_found"}
#             except OperationalError:
#                 await _clear_waiting_message(bot, chat_id)
#                 try:
#                     await bot.send_message(chat_id, "⚠️ Ошибка БД. Напишите @guard_gpt")
#                 except Exception:
#                     pass
#                 return {"ok": False, "error": "db_unavailable"}

#             if user.balance_credits < CREDITS_PER_GENERATION:
#                 await bot.send_message(chat_id, "Недостаточно генераций. /buy")
#                 return {"ok": False, "error": "insufficient_credits"}

#             # ✅ УЛУЧШЕННАЯ ОБРАБОТКА ЗАГРУЗКИ ИЗОБРАЖЕНИЙ
#             image_urls: List[str] = []
#             download_errors = []
#             file_too_big_count = 0  # ✅ счётчик больших файлов
            
#             for fid in (photos or [])[:5]:
#                 try:
#                     url = await _tg_file_to_public_url(bot, fid, cid=cid)
#                     image_urls.append(url)
#                 except ValueError as e:
#                     # ✅ Специальная обработка "file too big"
#                     if "file_too_big" in str(e):
#                         log.warning(_j("queue.file_too_big_skip", cid=cid, file_id=fid))
#                         file_too_big_count += 1
#                         download_errors.append("file_too_big")
#                     else:
#                         log.exception(_j("queue.fetch_image.value_error", cid=cid, file_id=fid))
#                         download_errors.append("value_error")
#                 except OSError as e:
#                     # ✅ Специфичная обработка ошибок диска
#                     if "Disk full" in str(e):
#                         log.error(_j("queue.disk_full", cid=cid, file_id=fid))
#                         download_errors.append("disk_full")
#                         # ✅ При заполнении диска - немедленно прерываем
#                         await _clear_waiting_message(bot, chat_id)
#                         try:
#                             await bot.send_message(
#                                 chat_id,
#                                 "⚠️ Временная проблема на сервере.\n"
#                                 "Попробуйте через 1-2 минуты или напишите @guard_gpt"
#                             )
#                         except Exception:
#                             pass
#                         return {"ok": False, "error": "disk_full"}
#                     else:
#                         log.exception(_j("queue.fetch_image.os_error", cid=cid, file_id=fid))
#                         download_errors.append("os_error")
#                 except TelegramBadRequest as e:
#                     # ✅ Обработка ошибок Telegram
#                     error_msg = str(e).lower()
#                     if "file is too big" in error_msg:
#                         log.warning(_j("queue.tg_file_too_big", cid=cid, file_id=fid))
#                         file_too_big_count += 1
#                         download_errors.append("file_too_big")
#                     else:
#                         log.exception(_j("queue.fetch_image.telegram_error", cid=cid, file_id=fid))
#                         download_errors.append("telegram_error")
#                 except Exception as e:
#                     log.exception(_j("queue.fetch_image.failed", cid=cid, file_id=fid))
#                     download_errors.append("unknown")

#             had_input_photos = bool(photos)
#             if had_input_photos and not image_urls:
#                 # ✅ ИНФОРМАТИВНЫЕ СООБЩЕНИЯ В ЗАВИСИМОСТИ ОТ ТИПА ОШИБКИ
#                 if file_too_big_count > 0:
#                     await bot.send_message(
#                         chat_id,
#                         "⚠️ <b>Файлы слишком большие</b>\n\n"
#                         "Telegram Bot API не может скачать файлы больше 20 MB.\n\n"
#                         "✅ <b>Решение:</b>\n"
#                         "• Отправьте фото как <b>фото</b> (сжатое), а не как документ\n"
#                         "• Или используйте изображения меньшего размера\n"
#                         "• Максимум: 20 MB на файл",
#                         parse_mode="HTML"
#                     )
#                 elif "disk_full" in download_errors:
#                     await bot.send_message(
#                         chat_id,
#                         "⚠️ Временная проблема на сервере. Попробуйте через 1-2 минуты или напишите @guard_gpt"
#                     )
#                 elif len(download_errors) == len(photos):
#                     await bot.send_message(
#                         chat_id,
#                         "⚠️ Не удалось обработать изображения.\n\n"
#                         "Убедитесь что:\n"
#                         "• Файлы в формате PNG/JPG/WebP\n"
#                         "• Размер до 20 MB каждый\n"
#                         "• Изображения не повреждены\n\n"
#                         "Если проблема повторяется — напишите @guard_gpt"
#                     )
#                 else:
#                     await bot.send_message(
#                         chat_id,
#                         f"⚠️ Удалось загрузить только {len(image_urls)} из {len(photos)} изображений.\n"
#                         f"Попробуйте отправить проблемные фото по одному или напишите @guard_gpt"
#                     )
#                 return {"ok": False, "error": "images_download_failed"}

#             try:
#                 callback = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/webhook/kie"
#                 task_uuid = await api.create_task(
#                     prompt,
#                     image_urls=image_urls if image_urls else None,
#                     callback_url=callback,
#                     output_format=settings.KIE_OUTPUT_FORMAT,
#                     image_size=aspect_ratio or settings.KIE_IMAGE_SIZE,
#                     cid=cid,
#                 )
#             except httpx.HTTPError as e:
#                 code = getattr(getattr(e, "response", None), "status_code", None)
#                 log.warning(_j("queue.kie_http_error", cid=cid, status_code=code))
#                 await _clear_waiting_message(bot, chat_id)
#                 try:
#                     await bot.send_message(chat_id, "⚠️ Ошибка генерации. Напишите @guard_gpt")
#                 except Exception:
#                     pass
#                 return {"ok": False, "error": f"kie_http_{code or 'unknown'}"}

#             try:
#                 task = Task(
#                     user_id=user.id,
#                     prompt=prompt,
#                     task_uuid=task_uuid,
#                     status="queued",
#                     delivered=False
#                 )
#                 s.add(task)
#                 await s.commit()
#                 await s.refresh(task)
#             except Exception:
#                 log.warning(_j("queue.db_write_failed", cid=cid, task_uuid=task_uuid))

#         return {"ok": True, "task_uuid": task_uuid}

#     except KieError as e:
#         log.error(_j("queue.kie_error", cid=cid, err=str(e)[:500]))
#         await _clear_waiting_message(bot, chat_id)
#         if 'task_uuid' in locals():
#             await _maybe_refund_if_deducted(chat_id, task_uuid, CREDITS_PER_GENERATION, cid, reason="kie_error")
#         try:
#             await bot.send_message(chat_id, "⚠️ Ошибка генерации. Напишите @guard_gpt")
#         except Exception:
#             pass
#         return {"ok": False, "error": str(e)[:500]}

#     except TelegramForbiddenError:
#         log.warning(_j("queue.tg_forbidden_on_start", cid=cid, chat_id=chat_id))
#         return {"ok": False, "error": "telegram_forbidden"}

#     except Exception:
#         log.exception(_j("queue.fatal", cid=cid))
#         await _clear_waiting_message(bot, chat_id)
#         if 'task_uuid' in locals():
#             await _maybe_refund_if_deducted(chat_id, task_uuid, CREDITS_PER_GENERATION, cid, reason="internal")
#         try:
#             await bot.send_message(chat_id, "⚠️ Ошибка. Напишите @guard_gpt")
#         except Exception:
#             pass
#         return {"ok": False, "error": "internal"}
    
#     finally:
#         await api.aclose()

# class WorkerSettings:
#     functions = [process_generation, broadcast_send]
#     on_startup = startup
#     on_shutdown = shutdown
#     redis_settings = RedisSettings(
#         host=settings.REDIS_HOST,
#         port=settings.REDIS_PORT,
#         database=settings.REDIS_DB_CACHE
#     )
#     job_timeout = 259200
#     keep_result = 0
    
#     # ✅ ДОБАВЛЕНО: регистрация cron задач
#     cron_jobs = [
#         # Очистка БД каждые 10 минут
#         cron(cleanup_database_task, minute={0, 10, 20, 30, 40, 50}, run_at_startup=True),
        
#         # Бэкап БД каждый час (в :05 минут каждого часа)
#         cron(backup_database_task, minute=5, run_at_startup=False),
#     ]
    
    
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import mimetypes
from typing import Any, Dict, List, Optional
from pathlib import Path

import httpx
import redis.asyncio as aioredis
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError,TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.redis import DefaultKeyBuilder, RedisStorage
from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from uuid import uuid4
from arq.cron import cron
from services.cleanup_db import cleanup_database_task
from services.backup_db import backup_database_task
from core.config import settings
from db.engine import SessionLocal
from db.models import Task, User
from services.pricing import CREDITS_PER_GENERATION
from vendors.kie import KieClient, KieError
from services.broadcast import broadcast_send

log = logging.getLogger("worker")


def _j(event: str, **fields) -> str:
    return json.dumps({"event": event, **fields}, ensure_ascii=False)


async def _tg_file_to_public_url(bot: Bot, file_id: str, *, cid: str) -> str:
    """
    ✅ ИСПРАВЛЕНО: проверка размера файла + обработка ошибок
    """
    # ✅ Сначала получаем информацию о файле
    try:
        f = await bot.get_file(file_id)
    except TelegramBadRequest as e:
        error_msg = str(e).lower()
        if "file is too big" in error_msg:
            log.error(_j("queue.file_too_big", cid=cid, file_id=file_id))
            raise ValueError("file_too_big")
        raise
    
    # ✅ Проверяем размер (лимит 20MB для Bot API)
    file_size = f.file_size or 0
    max_size = 20 * 1024 * 1024  # 20 MB
    
    if file_size > max_size:
        log.error(_j(
            "queue.file_size_limit", 
            cid=cid, 
            file_id=file_id,
            size_mb=file_size / (1024 * 1024),
            limit_mb=max_size / (1024 * 1024)
        ))
        raise ValueError("file_too_big")
    
    file_url = f"https://api.telegram.org/file/bot{settings.TELEGRAM_BOT_TOKEN}/{f.file_path}"

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(file_url)
        resp.raise_for_status()
        content = resp.content

    temp_dir = Path("/app/temp_inputs")
    
    try:
        temp_dir.mkdir(exist_ok=True, parents=True)
    except OSError as e:
        if e.errno == 28:
            log.error(_j("queue.disk_full", cid=cid, error="No space left on device"))
            raise OSError("Disk full") from e
        raise
    
    ext = Path(f.file_path).suffix or ".jpg"
    filename = f"{uuid4().hex}{ext}"
    filepath = temp_dir / filename
    
    try:
        with open(filepath, "wb") as out:
            out.write(content)
    except OSError as e:
        if e.errno == 28:
            log.error(_j("queue.disk_full_write", cid=cid, file=filename))
            raise OSError("Disk full") from e
        raise
    
    public_url = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/proxy/image/{filename}"
    
    log.info(_j(
        "queue.file_saved", 
        cid=cid, 
        filename=filename, 
        size=len(content),
        size_mb=round(len(content) / (1024 * 1024), 2),
        ext=ext,
        public_url=public_url
    ))
    
    return public_url

async def enqueue_generation(
    chat_id: int,
    prompt: str,
    photos: List[str],
    aspect_ratio: Optional[str] = None
) -> None:
    redis_pool = await create_pool(
        RedisSettings(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            database=settings.REDIS_DB_CACHE,
        )
    )
    await redis_pool.enqueue_job("process_generation", chat_id, prompt, photos, aspect_ratio)


async def startup(ctx: dict[str, Bot]):
    ctx["bot"] = Bot(token=settings.TELEGRAM_BOT_TOKEN)

    if settings.ADMIN_ID:
        from core.telegram_logger import TelegramLogHandler
        import logging
        telegram_handler = TelegramLogHandler(ctx["bot"], settings.ADMIN_ID)
        logging.getLogger().addHandler(telegram_handler)


async def shutdown(ctx: dict[str, Bot]):
    """
    ✅ ИСПРАВЛЕНО: Graceful shutdown с закрытием ВСЕХ ресурсов
    """
    bot: Bot = ctx.get("bot")
    if bot:
        await bot.session.close()
    
    # ✅ Закрываем все асинхронные ресурсы
    import gc
    try:
        for obj in gc.get_objects():
            # Redis connections
            if isinstance(obj, aioredis.Redis):
                try:
                    await obj.aclose()
                except Exception:
                    pass
            
            # ✅ ДОБАВЛЕНО: httpx клиенты
            if isinstance(obj, httpx.AsyncClient):
                try:
                    await obj.aclose()
                except Exception:
                    pass
    except Exception:
        pass


async def _clear_waiting_message(bot: Bot, chat_id: int) -> None:
    r = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_FSM)
    try:
        storage = RedisStorage(redis=r, key_builder=DefaultKeyBuilder(with_bot_id=True))
        me = await bot.get_me()
        fsm = FSMContext(storage=storage, key=StorageKey(me.id, chat_id, chat_id))
        data = await fsm.get_data()
        msg_id = data.get("wait_msg_id")
        if msg_id:
            try:
                await bot.delete_message(chat_id, msg_id)
            except Exception:
                pass
            await fsm.update_data(wait_msg_id=None)
    except Exception:
        pass
    finally:
        await r.aclose()


async def _maybe_refund_if_deducted(
    chat_id: int,
    task_uuid: str,
    amount: int,
    cid: str,
    reason: str
) -> None:
    rcache = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_CACHE)
    deb_key = f"credits:debited:{task_uuid}"
    try:
        debited = await rcache.get(deb_key)
    except Exception:
        debited = None
    finally:
        await rcache.aclose()
    
    if not debited:
        log.info(_j("refund.skipped_not_debited", cid=cid, chat_id=chat_id, task_uuid=task_uuid))
        return

    try:
        async with SessionLocal() as s:
            q = await s.execute(select(User).where(User.chat_id == chat_id))
            u = q.scalar_one_or_none()
            if u is not None:
                await s.execute(
                    update(User)
                    .where(User.id == u.id)
                    .values(balance_credits=User.balance_credits + amount)
                )
                await s.commit()
                log.info(_j("refund.ok", cid=cid, chat_id=chat_id, task_uuid=task_uuid, amount=amount, reason=reason))
                try:
                    rcache2 = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_CACHE)
                    await rcache2.delete(deb_key)
                    await rcache2.aclose()
                except Exception:
                    pass
                return
    except Exception:
        log.exception(_j("refund.db_error", cid=cid, task_uuid=task_uuid))

# async def process_generation(
#     ctx: dict[str, Bot],
#     chat_id: int,
#     prompt: str,
#     photos: List[str],
#     aspect_ratio: Optional[str] = None
# ) -> Dict[str, Any] | None:
#     """
#     ✅ УЛУЧШЕНО: лучшие сообщения об ошибках для пользователей
#     """
#     bot: Bot = ctx["bot"]
#     api = KieClient()
#     cid = uuid4().hex[:12]

#     try:
#         async with SessionLocal() as s:
#             try:
#                 q = await s.execute(select(User).where(User.chat_id == chat_id))
#                 user = q.scalar_one_or_none()
#                 if user is None:
#                     await _clear_waiting_message(bot, chat_id)
#                     try:
#                         await bot.send_message(chat_id, "Нажмите /start для инициализации")
#                     except Exception:
#                         pass
#                     log.warning(_j("queue.user_not_found", cid=cid, chat_id=chat_id))
#                     return {"ok": False, "error": "user_not_found"}
#             except OperationalError:
#                 await _clear_waiting_message(bot, chat_id)
#                 try:
#                     await bot.send_message(chat_id, "⚠️ Ошибка БД. Напишите @guard_gpt")
#                 except Exception:
#                     pass
#                 return {"ok": False, "error": "db_unavailable"}

#             if user.balance_credits < CREDITS_PER_GENERATION:
#                 await bot.send_message(chat_id, "Недостаточно генераций. /buy")
#                 return {"ok": False, "error": "insufficient_credits"}

#             # ✅ УЛУЧШЕННАЯ ОБРАБОТКА ЗАГРУЗКИ ИЗОБРАЖЕНИЙ
#             image_urls: List[str] = []
#             download_errors = []
#             file_too_big_count = 0  # ✅ счётчик больших файлов
            
#             for fid in (photos or [])[:5]:
#                 try:
#                     url = await _tg_file_to_public_url(bot, fid, cid=cid)
#                     image_urls.append(url)
#                 except ValueError as e:
#                     # ✅ Специальная обработка "file too big"
#                     if "file_too_big" in str(e):
#                         log.warning(_j("queue.file_too_big_skip", cid=cid, file_id=fid))
#                         file_too_big_count += 1
#                         download_errors.append("file_too_big")
#                     else:
#                         log.exception(_j("queue.fetch_image.value_error", cid=cid, file_id=fid))
#                         download_errors.append("value_error")
#                 except OSError as e:
#                     # ✅ Специфичная обработка ошибок диска
#                     if "Disk full" in str(e):
#                         log.error(_j("queue.disk_full", cid=cid, file_id=fid))
#                         download_errors.append("disk_full")
#                         # ✅ При заполнении диска - немедленно прерываем
#                         await _clear_waiting_message(bot, chat_id)
#                         try:
#                             await bot.send_message(
#                                 chat_id,
#                                 "⚠️ Временная проблема на сервере.\n"
#                                 "Попробуйте через 1-2 минуты или напишите @guard_gpt"
#                             )
#                         except Exception:
#                             pass
#                         return {"ok": False, "error": "disk_full"}
#                     else:
#                         log.exception(_j("queue.fetch_image.os_error", cid=cid, file_id=fid))
#                         download_errors.append("os_error")
#                 except TelegramBadRequest as e:
#                     # ✅ Обработка ошибок Telegram
#                     error_msg = str(e).lower()
#                     if "file is too big" in error_msg:
#                         log.warning(_j("queue.tg_file_too_big", cid=cid, file_id=fid))
#                         file_too_big_count += 1
#                         download_errors.append("file_too_big")
#                     else:
#                         log.exception(_j("queue.fetch_image.telegram_error", cid=cid, file_id=fid))
#                         download_errors.append("telegram_error")
#                 except Exception as e:
#                     log.exception(_j("queue.fetch_image.failed", cid=cid, file_id=fid))
#                     download_errors.append("unknown")

#             had_input_photos = bool(photos)
#             if had_input_photos and not image_urls:
#                 # ✅ ИНФОРМАТИВНЫЕ СООБЩЕНИЯ В ЗАВИСИМОСТИ ОТ ТИПА ОШИБКИ
#                 if file_too_big_count > 0:
#                     await bot.send_message(
#                         chat_id,
#                         "⚠️ <b>Файлы слишком большие</b>\n\n"
#                         "Telegram Bot API не может скачать файлы больше 20 MB.\n\n"
#                         "✅ <b>Решение:</b>\n"
#                         "• Отправьте фото как <b>фото</b> (сжатое), а не как документ\n"
#                         "• Или используйте изображения меньшего размера\n"
#                         "• Максимум: 20 MB на файл",
#                         parse_mode="HTML"
#                     )
#                 elif "disk_full" in download_errors:
#                     await bot.send_message(
#                         chat_id,
#                         "⚠️ Временная проблема на сервере. Попробуйте через 1-2 минуты или напишите @guard_gpt"
#                     )
#                 elif len(download_errors) == len(photos):
#                     await bot.send_message(
#                         chat_id,
#                         "⚠️ Не удалось обработать изображения.\n\n"
#                         "Убедитесь что:\n"
#                         "• Файлы в формате PNG/JPG/WebP\n"
#                         "• Размер до 20 MB каждый\n"
#                         "• Изображения не повреждены\n\n"
#                         "Если проблема повторяется — напишите @guard_gpt"
#                     )
#                 else:
#                     await bot.send_message(
#                         chat_id,
#                         f"⚠️ Удалось загрузить только {len(image_urls)} из {len(photos)} изображений.\n"
#                         f"Попробуйте отправить проблемные фото по одному или напишите @guard_gpt"
#                     )
#                 return {"ok": False, "error": "images_download_failed"}

#             try:
#                 callback = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/webhook/kie"
#                 task_uuid = await api.create_task(
#                     prompt,
#                     image_urls=image_urls if image_urls else None,
#                     callback_url=callback,
#                     output_format=settings.KIE_OUTPUT_FORMAT,
#                     image_size=aspect_ratio or settings.KIE_IMAGE_SIZE,
#                     cid=cid,
#                 )
#             except httpx.HTTPError as e:
#                 code = getattr(getattr(e, "response", None), "status_code", None)
#                 log.warning(_j("queue.kie_http_error", cid=cid, status_code=code))
#                 await _clear_waiting_message(bot, chat_id)
#                 try:
#                     await bot.send_message(chat_id, "⚠️ Ошибка генерации. Напишите @guard_gpt")
#                 except Exception:
#                     pass
#                 return {"ok": False, "error": f"kie_http_{code or 'unknown'}"}

#             try:
#                 task = Task(
#                     user_id=user.id,
#                     prompt=prompt,
#                     task_uuid=task_uuid,
#                     status="queued",
#                     delivered=False
#                 )
#                 s.add(task)
#                 await s.commit()
#                 await s.refresh(task)
#             except Exception:
#                 log.warning(_j("queue.db_write_failed", cid=cid, task_uuid=task_uuid))

#         return {"ok": True, "task_uuid": task_uuid}

#     except KieError as e:
#         error_str = str(e).lower()
#         log.error(_j("queue.kie_error", cid=cid, err=str(e)[:500]))
#         await _clear_waiting_message(bot, chat_id)
        
#         if 'task_uuid' in locals():
#             await _maybe_refund_if_deducted(chat_id, task_uuid, CREDITS_PER_GENERATION, cid, reason="kie_error")
        
#         try:
#             # ✅ УЛУЧШЕННЫЕ СООБЩЕНИЯ ОБ ОШИБКАХ
#             if "rate_limit" in error_str or "frequency" in error_str:
#                 await bot.send_message(
#                     chat_id,
#                     "⚠️ <b>Сервер перегружен</b>\n\n"
#                     "Попробуйте через 1-2 минуты.\n"
#                     "Если проблема повторяется — напишите @guard_gpt",
#                     parse_mode="HTML"
#                 )
#             elif "timeout" in error_str:
#                 await bot.send_message(
#                     chat_id,
#                     "⚠️ <b>Превышено время ожидания</b>\n\n"
#                     "Попробуйте:\n"
#                     "• Упростить промт\n"
#                     "• Использовать меньше фото\n"
#                     "• Повторить попытку\n\n"
#                     "Если не помогает — @guard_gpt",
#                     parse_mode="HTML"
#                 )
#             elif "bad_request" in error_str:
#                 # Извлекаем сообщение от API
#                 msg = str(e).split(":", 1)[-1].strip() if ":" in str(e) else str(e)
#                 await bot.send_message(
#                     chat_id,
#                     f"⚠️ <b>Ошибка запроса</b>\n\n"
#                     f"{msg[:200]}\n\n"
#                     f"Попробуйте изменить промт или фото.\n"
#                     f"Поддержка: @guard_gpt",
#                     parse_mode="HTML"
#                 )
#             else:
#                 await bot.send_message(
#                     chat_id,
#                     "⚠️ <b>Ошибка генерации</b>\n\n"
#                     "Попробуйте:\n"
#                     "• Изменить промт\n"
#                     "• Использовать другие фото\n"
#                     "• Повторить через минуту\n\n"
#                     "Поддержка: @guard_gpt",
#                     parse_mode="HTML"
#                 )
#         except Exception:
#             pass
        
#         return {"ok": False, "error": str(e)[:500]}

#     except TelegramForbiddenError:
#         log.warning(_j("queue.tg_forbidden_on_start", cid=cid, chat_id=chat_id))
#         return {"ok": False, "error": "telegram_forbidden"}

#     except Exception:
#         log.exception(_j("queue.fatal", cid=cid))
#         await _clear_waiting_message(bot, chat_id)
#         if 'task_uuid' in locals():
#             await _maybe_refund_if_deducted(chat_id, task_uuid, CREDITS_PER_GENERATION, cid, reason="internal")
#         try:
#             await bot.send_message(chat_id, "⚠️ Ошибка. Напишите @guard_gpt")
#         except Exception:
#             pass
#         return {"ok": False, "error": "internal"}
    
#     finally:
#         await api.aclose()

async def process_generation(
    ctx: dict[str, Bot],
    chat_id: int,
    prompt: str,
    photos: List[str],
    aspect_ratio: Optional[str] = None
) -> Dict[str, Any] | None:
    """
    ✅ УЛУЧШЕНО: лучшие сообщения об ошибках для пользователей
    """
    bot: Bot = ctx["bot"]
    api = KieClient()
    cid = uuid4().hex[:12]

    try:
        async with SessionLocal() as s:
            try:
                q = await s.execute(select(User).where(User.chat_id == chat_id))
                user = q.scalar_one_or_none()
                if user is None:
                    await _clear_waiting_message(bot, chat_id)
                    try:
                        await bot.send_message(chat_id, "Нажмите /start для инициализации")
                    except Exception:
                        pass
                    log.warning(_j("queue.user_not_found", cid=cid, chat_id=chat_id))
                    return {"ok": False, "error": "user_not_found"}
            except OperationalError:
                await _clear_waiting_message(bot, chat_id)
                try:
                    await bot.send_message(chat_id, "⚠️ Ошибка БД. Напишите @guard_gpt")
                except Exception:
                    pass
                return {"ok": False, "error": "db_unavailable"}

            if user.balance_credits < CREDITS_PER_GENERATION:
                await bot.send_message(chat_id, "Недостаточно генераций. /buy")
                return {"ok": False, "error": "insufficient_credits"}

            # ✅ УЛУЧШЕННАЯ ОБРАБОТКА ЗАГРУЗКИ ИЗОБРАЖЕНИЙ
            image_urls: List[str] = []
            download_errors = []
            file_too_big_count = 0  # ✅ счётчик больших файлов
            
            for fid in (photos or [])[:5]:
                try:
                    url = await _tg_file_to_public_url(bot, fid, cid=cid)
                    image_urls.append(url)
                except ValueError as e:
                    # ✅ Специальная обработка "file too big"
                    if "file_too_big" in str(e):
                        log.warning(_j("queue.file_too_big_skip", cid=cid, file_id=fid))
                        file_too_big_count += 1
                        download_errors.append("file_too_big")
                    else:
                        log.exception(_j("queue.fetch_image.value_error", cid=cid, file_id=fid))
                        download_errors.append("value_error")
                except OSError as e:
                    # ✅ Специфичная обработка ошибок диска
                    if "Disk full" in str(e):
                        log.error(_j("queue.disk_full", cid=cid, file_id=fid))
                        download_errors.append("disk_full")
                        # ✅ При заполнении диска - немедленно прерываем
                        await _clear_waiting_message(bot, chat_id)
                        try:
                            await bot.send_message(
                                chat_id,
                                "⚠️ Временная проблема на сервере.\n"
                                "Попробуйте через 1-2 минуты или напишите @guard_gpt"
                            )
                        except Exception:
                            pass
                        return {"ok": False, "error": "disk_full"}
                    else:
                        log.exception(_j("queue.fetch_image.os_error", cid=cid, file_id=fid))
                        download_errors.append("os_error")
                except TelegramBadRequest as e:
                    # ✅ Обработка ошибок Telegram
                    error_msg = str(e).lower()
                    if "file is too big" in error_msg:
                        log.warning(_j("queue.tg_file_too_big", cid=cid, file_id=fid))
                        file_too_big_count += 1
                        download_errors.append("file_too_big")
                    else:
                        log.exception(_j("queue.fetch_image.telegram_error", cid=cid, file_id=fid))
                        download_errors.append("telegram_error")
                except Exception as e:
                    log.exception(_j("queue.fetch_image.failed", cid=cid, file_id=fid))
                    download_errors.append("unknown")

            had_input_photos = bool(photos)
            if had_input_photos and not image_urls:
                # ✅ ИНФОРМАТИВНЫЕ СООБЩЕНИЯ В ЗАВИСИМОСТИ ОТ ТИПА ОШИБКИ
                if file_too_big_count > 0:
                    await bot.send_message(
                        chat_id,
                        "⚠️ <b>Файлы слишком большие</b>\n\n"
                        "Telegram Bot API не может скачать файлы больше 20 MB.\n\n"
                        "✅ <b>Решение:</b>\n"
                        "• Отправьте фото как <b>фото</b> (сжатое), а не как документ\n"
                        "• Или используйте изображения меньшего размера\n"
                        "• Максимум: 20 MB на файл",
                        parse_mode="HTML"
                    )
                elif "disk_full" in download_errors:
                    await bot.send_message(
                        chat_id,
                        "⚠️ Временная проблема на сервере. Попробуйте через 1-2 минуты или напишите @guard_gpt"
                    )
                elif len(download_errors) == len(photos):
                    await bot.send_message(
                        chat_id,
                        "⚠️ Не удалось обработать изображения.\n\n"
                        "Убедитесь что:\n"
                        "• Файлы в формате PNG/JPG/WebP\n"
                        "• Размер до 20 MB каждый\n"
                        "• Изображения не повреждены\n\n"
                        "Если проблема повторяется — напишите @guard_gpt"
                    )
                else:
                    await bot.send_message(
                        chat_id,
                        f"⚠️ Удалось загрузить только {len(image_urls)} из {len(photos)} изображений.\n"
                        f"Попробуйте отправить проблемные фото по одному или напишите @guard_gpt"
                    )
                return {"ok": False, "error": "images_download_failed"}

            try:
                callback = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/webhook/kie"
                task_uuid = await api.create_task(
                    prompt,
                    image_urls=image_urls if image_urls else None,
                    callback_url=callback,
                    output_format=settings.KIE_OUTPUT_FORMAT,
                    image_size=aspect_ratio or settings.KIE_IMAGE_SIZE,
                    cid=cid,
                )
            except httpx.HTTPError as e:
                code = getattr(getattr(e, "response", None), "status_code", None)
                log.warning(_j("queue.kie_http_error", cid=cid, status_code=code))
                await _clear_waiting_message(bot, chat_id)
                try:
                    await bot.send_message(chat_id, "⚠️ Ошибка генерации. Напишите @guard_gpt")
                except Exception:
                    pass
                return {"ok": False, "error": f"kie_http_{code or 'unknown'}"}

            try:
                task = Task(
                    user_id=user.id,
                    prompt=prompt,
                    task_uuid=task_uuid,
                    status="queued",
                    delivered=False
                )
                s.add(task)
                await s.commit()
                await s.refresh(task)
            except Exception:
                log.warning(_j("queue.db_write_failed", cid=cid, task_uuid=task_uuid))

        return {"ok": True, "task_uuid": task_uuid}

    except KieError as e:
        error_str = str(e).lower()
        log.error(_j("queue.kie_error", cid=cid, err=str(e)[:500]))
        await _clear_waiting_message(bot, chat_id)
        
        if 'task_uuid' in locals():
            await _maybe_refund_if_deducted(chat_id, task_uuid, CREDITS_PER_GENERATION, cid, reason="kie_error")
        
        try:
            # ✅ СПЕЦИАЛЬНАЯ ОБРАБОТКА ДЛЯ НЕПОДДЕРЖИВАЕМЫХ ФОРМАТОВ (HEIC)
            if "file type not supported" in error_str or "not supported" in error_str:
                await bot.send_message(
                    chat_id, 
                    "❌ <b>Неподдерживаемый формат изображения</b>\n\n"
                    "📱 Файлы HEIC (iPhone) и некоторые другие форматы не поддерживаются.\n\n"
                    "✅ <b>Решение:</b>\n"
                    "• Отправьте фото как <b>фото</b> (сжатое), а не как документ\n"
                    "• Или конвертируйте в JPG/PNG/WEBP\n"
                    "• На iPhone: Настройки → Камера → Форматы → «Наиболее совместимые»",
                    parse_mode="HTML"
                )
            # ✅ УЛУЧШЕННЫЕ СООБЩЕНИЯ ОБ ОШИБКАХ
            elif "rate_limit" in error_str or "frequency" in error_str:
                await bot.send_message(
                    chat_id,
                    "⚠️ <b>Сервер перегружен</b>\n\n"
                    "Попробуйте через 1-2 минуты.\n"
                    "Если проблема повторяется — напишите @guard_gpt",
                    parse_mode="HTML"
                )
            elif "timeout" in error_str:
                await bot.send_message(
                    chat_id,
                    "⚠️ <b>Превышено время ожидания</b>\n\n"
                    "Попробуйте:\n"
                    "• Упростить промт\n"
                    "• Использовать меньше фото\n"
                    "• Повторить попытку\n\n"
                    "Если не помогает — @guard_gpt",
                    parse_mode="HTML"
                )
            elif "bad_request" in error_str:
                # Извлекаем сообщение от API
                msg = str(e).split(":", 1)[-1].strip() if ":" in str(e) else str(e)
                await bot.send_message(
                    chat_id,
                    f"⚠️ <b>Ошибка запроса</b>\n\n"
                    f"{msg[:200]}\n\n"
                    f"Попробуйте изменить промт или фото.\n"
                    f"Поддержка: @guard_gpt",
                    parse_mode="HTML"
                )
            else:
                await bot.send_message(
                    chat_id,
                    "⚠️ <b>Ошибка генерации</b>\n\n"
                    "Попробуйте:\n"
                    "• Изменить промт\n"
                    "• Использовать другие фото\n"
                    "• Повторить через минуту\n\n"
                    "Поддержка: @guard_gpt",
                    parse_mode="HTML"
                )
        except Exception:
            pass
        
        return {"ok": False, "error": str(e)[:500]}

    except TelegramForbiddenError:
        log.warning(_j("queue.tg_forbidden_on_start", cid=cid, chat_id=chat_id))
        return {"ok": False, "error": "telegram_forbidden"}

    except Exception:
        log.exception(_j("queue.fatal", cid=cid))
        await _clear_waiting_message(bot, chat_id)
        if 'task_uuid' in locals():
            await _maybe_refund_if_deducted(chat_id, task_uuid, CREDITS_PER_GENERATION, cid, reason="internal")
        try:
            await bot.send_message(chat_id, "⚠️ Ошибка. Напишите @guard_gpt")
        except Exception:
            pass
        return {"ok": False, "error": "internal"}
    
    finally:
        await api.aclose()
        
class WorkerSettings:
    functions = [process_generation, broadcast_send]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        database=settings.REDIS_DB_CACHE
    )
    job_timeout = 259200
    keep_result = 0
    
    # ✅ ДОБАВЛЕНО: регистрация cron задач
    cron_jobs = [
        # Очистка БД каждые 10 минут
        cron(cleanup_database_task, minute={0, 10, 20, 30, 40, 50}, run_at_startup=True),
        
        # Бэкап БД каждый час (в :05 минут каждого часа)
        cron(backup_database_task, minute=5, run_at_startup=False),
    ]    