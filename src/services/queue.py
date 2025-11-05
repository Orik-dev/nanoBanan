# from __future__ import annotations

# import base64
# import hashlib
# import json
# import logging
# import mimetypes
# from typing import Any, Dict, List,Optional

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

# # 🆕 ИМПОРТ broadcast_send
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
#     f = await bot.get_file(file_id)
#     file_url = f"https://api.telegram.org/file/bot{settings.TELEGRAM_BOT_TOKEN}/{f.file_path}"

#     async with httpx.AsyncClient(timeout=60) as client:
#         resp = await client.get(file_url)
#         resp.raise_for_status()
#         content = resp.content
#         mime = _guess_mime_from_headers_or_path(resp, f.file_path)

#     size = len(content)
#     sha = hashlib.sha256(content).hexdigest()
#     log.info(_j("queue.fetch_tg_file.ok", cid=cid, file_path=f.file_path, mime=mime, size=size, sha256=sha))

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


# async def enqueue_generation(chat_id: int, prompt: str, photos: List[str],aspect_ratio: Optional[str] = None,seed: Optional[int] = None,) -> None:
#     """Ставит в очередь генерацию изображения"""
#     redis_pool = await create_pool(
#         RedisSettings(
#             host=settings.REDIS_HOST,
#             port=settings.REDIS_PORT,
#             database=settings.REDIS_DB_CACHE,
#         )
#     )
#     await redis_pool.enqueue_job("process_generation", chat_id, prompt, photos,aspect_ratio,seed)


# async def startup(ctx: dict[str, Bot]):
#     ctx["bot"] = Bot(token=settings.TELEGRAM_BOT_TOKEN)


# async def shutdown(ctx: dict[str, Bot]):
#     bot: Bot = ctx.get("bot")
#     if bot:
#         await bot.session.close()


# async def _clear_waiting_message(bot: Bot, chat_id: int) -> None:
#     try:
#         r = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_FSM)
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


# async def _maybe_refund_if_deducted(chat_id: int, task_uuid: str, amount: int, cid: str, reason: str) -> None:
#     """Возврат кредитов если они были списаны"""
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
#     ctx: dict[str, Bot], chat_id: int, prompt: str, photos: List[str],aspect_ratio: Optional[str] = None,seed: Optional[int] = None,
# ) -> Dict[str, Any] | None:
#     bot: Bot = ctx["bot"]
#     api = RunBlobClient()
#     cid = uuid4().hex[:12]

#     log.info(
#         _j(
#             "queue.process.start",
#             cid=cid,
#             chat_id=chat_id,
#             photos_in=len(photos or []),
#             prompt_len=len(prompt or ""),
#             seed=seed,
#         )
#     )

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
#                 log.info(_j("queue.balance.insufficient", cid=cid, balance=user.balance_credits))
#                 return {"ok": False, "error": "insufficient_credits"}

#             images: List[Dict[str, Any]] = []
#             for fid in (photos or [])[:4]:
#                 try:
#                     images.append(await _tg_file_to_image_dict(bot, fid, cid=cid))
#                 except Exception:
#                     log.exception(_j("queue.fetch_image.failed", cid=cid, file_id=fid))
#             log.info(_j("queue.images.built", cid=cid, count=len(images)))

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
#                     seed=seed,
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

#             log.info(_j("queue.create_task.ok", cid=cid, task_uuid=task_uuid))

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


# # 🆕 ДОБАВЛЯЕМ broadcast_send В ВОРКЕР
# class WorkerSettings:
#     functions = [process_generation, broadcast_send]  # добавили broadcast_send
#     on_startup = startup
#     on_shutdown = shutdown
#     redis_settings = RedisSettings(
#         host=settings.REDIS_HOST, port=settings.REDIS_PORT, database=settings.REDIS_DB_CACHE
#     )
#     # job_timeout = settings.ARQ_JOB_TIMEOUT_S
#     job_timeout = 259200
#     keep_result = 0

# # #KIE AI 
# # from __future__ import annotations

# # import logging
# # import json
# # from typing import List, Dict, Any

# # from aiogram import Bot
# # from arq.connections import RedisSettings
# # from arq import create_pool
# # from sqlalchemy import select
# # from uuid import uuid4

# # from core.config import settings
# # from db.engine import SessionLocal
# # from db.models import Task, User
# # from services.pricing import CREDITS_PER_GENERATION

# # from vendors.kie import KieClient, KieError

# # log = logging.getLogger("worker")

# # def _j(event: str, **fields) -> str:
# #     return json.dumps({"event": event, **fields}, ensure_ascii=False)

# # async def _tg_file_to_url(bot: Bot, file_id: str, *, cid: str) -> str:
# #     """
# #     Возвращает публичный URL файла Telegram для внешнего запроса:
# #     https://api.telegram.org/file/bot<token>/<file_path>
# #     Валидируем размер (<=10MB) и расширение.
# #     """
# #     f = await bot.get_file(file_id)
# #     file_path = f.file_path
# #     file_size = getattr(f, "file_size", None) or 0

# #     if file_size > 10 * 1024 * 1024:
# #         log.error(_j("queue.image_too_large", cid=cid, size=file_size))
# #         raise ValueError("image too large")

# #     lower = (file_path or "").lower()
# #     if not (lower.endswith(".png") or lower.endswith(".jpg") or lower.endswith(".jpeg") or lower.endswith(".webp")):
# #         log.error(_j("queue.image_unsupported_ext", cid=cid, file_path=file_path))
# #         raise ValueError("unsupported image ext")

# #     return f"https://api.telegram.org/file/bot{settings.TELEGRAM_BOT_TOKEN}/{file_path}"

# # async def enqueue_generation(chat_id: int, prompt: str, photos: List[str]) -> None:
# #     redis = await create_pool(
# #         RedisSettings(
# #             host=settings.REDIS_HOST,
# #             port=settings.REDIS_PORT,
# #             database=settings.REDIS_DB_CACHE,
# #         )
# #     )
# #     await redis.enqueue_job("process_generation", chat_id, prompt, photos)

# # async def startup(ctx: dict[str, Bot]):
# #     ctx["bot"] = Bot(token=settings.TELEGRAM_BOT_TOKEN)

# # async def shutdown(ctx: dict[str, Bot]):
# #     bot: Bot = ctx.get("bot")
# #     if bot:
# #         await bot.session.close()

# # async def process_generation(ctx: dict[str, Bot], chat_id: int, prompt: str, photos: List[str]) -> Dict[str, Any] | None:
# #     """
# #     Создаём задачу в KIE и сохраняем Task(status='queued').
# #     Дальше всё делает вебхук /webhook/kie: списание кредитов, скачивание, отправка.
# #     """
# #     bot: Bot = ctx["bot"]
# #     api = KieClient()
# #     cid = uuid4().hex[:12]

# #     log.info(_j("queue.process.start", cid=cid, chat_id=chat_id, photos_in=len(photos or []), prompt_len=len(prompt or "")))

# #     try:
# #         async with SessionLocal() as s:
# #             user = (await s.execute(select(User).where(User.chat_id == chat_id))).scalar_one()
# #             if user.balance_credits < CREDITS_PER_GENERATION:
# #                 await bot.send_message(chat_id, "Недостаточно генераций. Пополните баланс: /buy")
# #                 log.info(_j("queue.balance.insufficient", cid=cid, balance=user.balance_credits))
# #                 return {"ok": False, "error": "insufficient_credits"}

# #             # готовим до 5 ссылок
# #             image_urls: List[str] = []
# #             for fid in (photos or [])[:5]:
# #                 try:
# #                     image_urls.append(await _tg_file_to_url(bot, fid, cid=cid))
# #                 except Exception:
# #                     log.exception(_j("queue.fetch_image_url.failed", cid=cid, file_id=fid))
# #             if not image_urls:
# #                 await bot.send_message(
# #                     chat_id,
# #                     "Можно загрузить 1–5 изображений PNG/JPG/WebP, до 10 MB каждое. Попробуйте снова 🙏"
# #                 )
# #                 return {"ok": False, "error": "images_prepare_failed"}

# #             callback = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/webhook/kie?t={settings.WEBHOOK_SECRET_TOKEN}"

# #             try:
# #                 task_id = await api.create_task(
# #                     prompt,
# #                     image_urls=image_urls,
# #                     call_back_url=callback,
# #                     cid=cid,
# #                 )
# #             except KieError as e:
# #                 msg = str(e)
# #                 if msg.startswith("bad_request:"):
# #                     await bot.send_message(
# #                         chat_id,
# #                         "Запрос отклонён. Уточните промт и/или изображения (PNG/JPG/WebP, до 10 MB)."
# #                     )
# #                     return {"ok": False, "error": msg}
# #                 await bot.send_message(chat_id, "Произошла ошибка. Команда уже разбирается.")
# #                 return {"ok": False, "error": msg}

# #             # сохраняем задачу (queued). списание — в вебхуке
# #             task = Task(user_id=user.id, prompt=prompt, task_uuid=task_id, status="queued")
# #             s.add(task)
# #             await s.commit()
# #             await s.refresh(task)

# #         log.info(_j("queue.task.queued", cid=cid, task_uuid=task_id))
# #         return {"ok": True, "task_uuid": task_id}

# #     except Exception:
# #         log.exception(_j("queue.fatal", cid=cid))
# #         try:
# #             await bot.send_message(chat_id, "Произошла ошибка. Команда уже разбирается.")
# #         except Exception:
# #             pass
# #         return {"ok": False, "error": "internal"}

# # class WorkerSettings:
# #     functions = [process_generation]
# #     on_startup = startup
# #     on_shutdown = shutdown
# #     redis_settings = RedisSettings(
# #         host=settings.REDIS_HOST,
# #         port=settings.REDIS_PORT,
# #         database=settings.REDIS_DB_CACHE,
# #     )
# #     job_timeout = settings.ARQ_JOB_TIMEOUT_S
# #     keep_result = 0

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import httpx
import redis.asyncio as aioredis
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.redis import DefaultKeyBuilder, RedisStorage
from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from uuid import uuid4

from core.config import settings
from db.engine import SessionLocal
from db.models import Task, User
from services.pricing import CREDITS_PER_GENERATION
from vendors.runblob import RunBlobClient, RunBlobError
from services.broadcast import broadcast_send

log = logging.getLogger("worker")


def _j(event: str, **fields) -> str:
    return json.dumps({"event": event, **fields}, ensure_ascii=False)


async def _tg_file_to_image_dict(bot: Bot, file_id: str, *, cid: str) -> Dict[str, Any]:
    """
    ✅ КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Используем Telegram URL напрямую!
    API примеры показывают использование URL, а не bytes!
    """
    f = await bot.get_file(file_id)
    
    # Проверяем расширение файла
    file_path = f.file_path or ""
    lower_path = file_path.lower()
    
    # Валидация формата
    if not (lower_path.endswith('.png') or lower_path.endswith('.jpg') or 
            lower_path.endswith('.jpeg') or lower_path.endswith('.webp')):
        log.error(_j("queue.image_invalid_ext", cid=cid, file_path=file_path))
        raise ValueError(f"invalid_format")
    
    # Валидация размера (7MB максимум по документации)
    file_size = getattr(f, 'file_size', 0) or 0
    MAX_BYTES = 7 * 1024 * 1024
    
    if file_size > MAX_BYTES:
        size_mb = file_size / (1024 * 1024)
        log.error(_j("queue.image_too_large", cid=cid, size=file_size, max=MAX_BYTES))
        raise ValueError(f"too_large:{size_mb:.1f}MB")
    
    # ✅ КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Отправляем URL напрямую!
    # Telegram URL публичный и доступен для API
    file_url = f"https://api.telegram.org/file/bot{settings.TELEGRAM_BOT_TOKEN}/{file_path}"
    
    log.info(_j("queue.image.prepared", cid=cid, file_path=file_path, size_mb=file_size/(1024*1024), url_len=len(file_url)))
    
    # ✅ Возвращаем URL формат как в документации!
    return {"url": file_url}


async def enqueue_generation(chat_id: int, prompt: str, photos: List[str], aspect_ratio: Optional[str] = None) -> None:
    """Ставит в очередь генерацию изображения"""
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


async def shutdown(ctx: dict[str, Bot]):
    bot: Bot = ctx.get("bot")
    if bot:
        await bot.session.close()


async def _clear_waiting_message(bot: Bot, chat_id: int) -> None:
    try:
        r = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_FSM)
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


async def _maybe_refund_if_deducted(chat_id: int, task_uuid: str, amount: int, cid: str, reason: str) -> None:
    """Возврат кредитов если они были списаны"""
    rcache = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_CACHE)
    deb_key = f"credits:debited:{task_uuid}"
    try:
        debited = await rcache.get(deb_key)
    except Exception:
        debited = None
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
                    await rcache.delete(deb_key)
                except Exception:
                    pass
                return
    except Exception:
        log.exception(_j("refund.db_error", cid=cid, task_uuid=task_uuid))


async def process_generation(
    ctx: dict[str, Bot], chat_id: int, prompt: str, photos: List[str], aspect_ratio: Optional[str] = None
) -> Dict[str, Any] | None:
    bot: Bot = ctx["bot"]
    api = RunBlobClient()
    cid = uuid4().hex[:12]

    log.info(
        _j(
            "queue.process.start",
            cid=cid,
            chat_id=chat_id,
            photos_in=len(photos or []),
            prompt_len=len(prompt or ""),
        )
    )

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
                log.info(_j("queue.balance.insufficient", cid=cid, balance=user.balance_credits))
                return {"ok": False, "error": "insufficient_credits"}

            # ✅ ПОДРОБНАЯ ОБРАБОТКА ФОТОГРАФИЙ С ОТЧЁТОМ!
            images: List[Dict[str, Any]] = []
            failed_photos: List[str] = []
            
            photos_to_process = (photos or [])[:4]
            total_photos = len(photos_to_process)
            
            for idx, fid in enumerate(photos_to_process, 1):
                try:
                    img = await _tg_file_to_image_dict(bot, fid, cid=cid)
                    images.append(img)
                    log.info(_j("queue.photo_ok", cid=cid, idx=idx, total=total_photos))
                except ValueError as e:
                    err_msg = str(e)
                    log.error(_j("queue.photo_validation_failed", cid=cid, idx=idx, error=err_msg))
                    failed_photos.append(f"Фото {idx}: {err_msg}")
                except Exception as e:
                    log.exception(_j("queue.photo_download_failed", cid=cid, idx=idx, file_id=fid))
                    failed_photos.append(f"Фото {idx}: ошибка загрузки")

            # ✅ ИНФОРМИРУЕМ ПОЛЬЗОВАТЕЛЯ О ПРОБЛЕМАХ!
            if failed_photos:
                error_details = []
                for msg in failed_photos:
                    if "too_large" in msg:
                        error_details.append(f"{msg.split(':')[0]}: слишком большое (>7MB)")
                    elif "invalid_format" in msg:
                        error_details.append(f"{msg.split(':')[0]}: неверный формат")
                    else:
                        error_details.append(msg)
                
                warning = (
                    f"⚠️ Не удалось загрузить {len(failed_photos)} из {total_photos} фото:\n\n"
                    + "\n".join(error_details) +
                    "\n\n💡 <b>Как исправить:</b>\n"
                    "• Отправляйте как <b>документы</b> (📎 → Файл)\n"
                    "• Размер: до 7 MB на фото\n"
                    "• Формат: PNG, JPG, WebP\n\n"
                )
                
                if not images:
                    await _clear_waiting_message(bot, chat_id)
                    await bot.send_message(
                        chat_id,
                        warning + 
                        "❌ <b>Ни одно фото не загрузилось</b>\n\n"
                        "📋 <b>Что делать:</b>\n"
                        "1️⃣ Отправьте фото как <b>документы</b> (📎 → Файл)\n"
                        "2️⃣ Проверьте размер: каждое фото до <b>7 MB</b>\n"
                        "3️⃣ Формат: только PNG, JPG или WebP",
                        parse_mode="HTML"
                    )
                    return {"ok": False, "error": "all_images_failed"}
                else:
                    await bot.send_message(
                        chat_id,
                        warning + 
                        f"✅ Продолжаем генерацию с {len(images)} фото.",
                        parse_mode="HTML"
                    )

            log.info(_j("queue.images.built", cid=cid, success=len(images), failed=len(failed_photos)))

            had_input_photos = bool(photos)
            if had_input_photos and not images:
                await _clear_waiting_message(bot, chat_id)
                await bot.send_message(
                    chat_id,
                    "❌ <b>Не удалось загрузить фото</b>\n\n"
                    "💡 <b>Рекомендации:</b>\n"
                    "• Отправляйте фото как <b>документы</b> (📎 → Файл)\n"
                    "• Размер: до <b>7 MB</b> на фото\n"
                    "• Формат: PNG, JPG, WebP\n\n"
                    "🔄 Попробуйте ещё раз!",
                    parse_mode="HTML"
                )
                return {"ok": False, "error": "images_download_failed"}

            try:
                callback = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/webhook/runblob"
                task_uuid = await api.create_task(
                    prompt,
                    images=images if images else None,
                    callback_url=callback,
                    aspect_ratio=aspect_ratio,
                    cid=cid,
                )
            except httpx.HTTPError as e:
                code = getattr(getattr(e, "response", None), "status_code", None)
                log.warning(_j("queue.runblob_http_error", cid=cid, status_code=code))
                await _clear_waiting_message(bot, chat_id)
                try:
                    await bot.send_message(chat_id, "⚠️ Ошибка генерации. Напишите @guard_gpt")
                except Exception:
                    pass
                return {"ok": False, "error": f"runblob_http_{code or 'unknown'}"}

            log.info(_j("queue.create_task.ok", cid=cid, task_uuid=task_uuid))

            try:
                task = Task(user_id=user.id, prompt=prompt, task_uuid=task_uuid, status="queued", delivered=False)
                s.add(task)
                await s.commit()
                await s.refresh(task)
            except Exception:
                log.warning(_j("queue.db_write_failed", cid=cid, task_uuid=task_uuid))

        return {"ok": True, "task_uuid": task_uuid}

    except RunBlobError as e:
        log.error(_j("queue.runblob_error", cid=cid, err=str(e)[:500]))
        await _clear_waiting_message(bot, chat_id)
        if 'task_uuid' in locals():
            await _maybe_refund_if_deducted(chat_id, task_uuid, CREDITS_PER_GENERATION, cid, reason="runblob_error")
        try:
            await bot.send_message(chat_id, "⚠️ Ошибка генерации. Напишите @guard_gpt")
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


class WorkerSettings:
    functions = [process_generation, broadcast_send]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings(
        host=settings.REDIS_HOST, port=settings.REDIS_PORT, database=settings.REDIS_DB_CACHE
    )
    job_timeout = 259200
    keep_result = 0