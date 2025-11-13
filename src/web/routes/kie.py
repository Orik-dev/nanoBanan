# from __future__ import annotations

# import asyncio
# import json
# import logging
# import os
# from typing import Optional, Tuple

# import httpx
# import redis.asyncio as aioredis
# from fastapi import APIRouter, Request
# from fastapi.responses import JSONResponse
# from sqlalchemy import select, update

# from aiogram.fsm.context import FSMContext
# from aiogram.fsm.storage.base import StorageKey
# from aiogram.fsm.storage.redis import DefaultKeyBuilder, RedisStorage

# from bot.routers.generation import send_generation_result
# from bot.states import CreateStates, GenStates
# from core.config import settings
# from db.engine import SessionLocal
# from db.models import Task, User
# from services.telegram_safe import safe_send_text

# router = APIRouter()
# log = logging.getLogger("kie")


# # -------------------- webhook lock --------------------

# async def _acquire_webhook_lock(task_id: str, ttl: int = 180) -> Optional[Tuple[aioredis.Redis, str]]:
#     r = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_CACHE)
#     key = f"wb:lock:kie:{task_id}"
#     try:
#         ok = await r.set(key, "1", nx=True, ex=ttl)
#         if ok:
#             return r, key
#         return None
#     except Exception:
#         try:
#             await r.aclose()
#         except Exception:
#             pass
#         return None


# async def _release_webhook_lock(lock: Optional[Tuple[aioredis.Redis, str]]) -> None:
#     if not lock:
#         return
#     r, key = lock
#     try:
#         await r.delete(key)
#     except Exception:
#         pass
#     finally:
#         try:
#             await r.aclose()
#         except Exception:
#             pass


# async def _clear_pending_marker(task_id: str) -> None:
#     try:
#         r = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_CACHE)
#         await r.delete(f"task:pending:{task_id}")
#         await r.aclose()
#     except Exception:
#         pass


# # -------------------- FSM cleanup --------------------

# async def _clear_wait_and_reset(bot, chat_id: int, *, back_to: str = "auto") -> None:
#     r = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_FSM)
#     try:
#         storage = RedisStorage(redis=r, key_builder=DefaultKeyBuilder(with_bot_id=True))
#         me = await bot.get_me()
#         fsm = FSMContext(storage=storage, key=StorageKey(me.id, chat_id, chat_id))

#         data = await fsm.get_data()
#         wait_id = data.get("wait_msg_id")
#         if wait_id:
#             try:
#                 await bot.delete_message(chat_id, wait_id)
#             except Exception:
#                 pass
#             await fsm.update_data(wait_msg_id=None)

#         mode = (data.get("mode") or "").lower()
#         target = back_to
#         if target == "auto":
#             target = "create" if mode == "create" else "edit"

#         if target == "create":
#             await fsm.update_data(mode="create", edits=[], photos=[])
#             await fsm.set_state(CreateStates.waiting_prompt)
#         else:
#             await fsm.set_state(GenStates.waiting_prompt)
#     finally:
#         await r.aclose()


# # -------------------- webhook --------------------

# @router.post("/webhook/kie")
# async def kie_callback(req: Request):
#     """
#     Обработка webhook от KIE AI.
#     """
#     try:
#         payload = await req.json()
#     except Exception:
#         log.warning(json.dumps({"event": "kie_webhook.invalid_json"}, ensure_ascii=False))
#         return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)

#     # Парсинг payload
#     data = payload.get("data") or {}
#     task_id = data.get("taskId")
#     state = str(data.get("state") or "").lower()
#     result_json = data.get("resultJson") or "{}"
#     fail_code = data.get("failCode")
#     fail_msg = data.get("failMsg")

#     if not task_id:
#         return JSONResponse({"ok": False, "error": "no_task_id"}, status_code=400)

#     await _clear_pending_marker(task_id)

#     # Эксклюзивная обработка
#     lock = await _acquire_webhook_lock(task_id, ttl=180)
#     if lock is None:
#         log.info(json.dumps({"event": "kie_webhook.skip_locked", "task_id": task_id}, ensure_ascii=False))
#         return JSONResponse({"ok": True})

#     try:
#         async with SessionLocal() as s:
#             task = (await s.execute(select(Task).where(Task.task_uuid == task_id))).scalar_one_or_none()
#             if not task:
#                 log.info(json.dumps({"event": "kie_webhook.no_task", "task_id": task_id}, ensure_ascii=False))
#                 return JSONResponse({"ok": True})

#             if getattr(task, "delivered", False):
#                 log.info(json.dumps({"event": "kie_webhook.already_delivered", "task_id": task_id}, ensure_ascii=False))
#                 return JSONResponse({"ok": True})

#             user = await s.get(User, task.user_id)
#             bot = req.app.state.bot

#             # ---- SUCCESS ----
#             if state == "success":
#                 # Парсинг результатов
#                 try:
#                     parsed = json.loads(result_json)
#                     result_urls = parsed.get("resultUrls") or []
#                 except Exception:
#                     result_urls = []

#                 if not result_urls:
#                     await _clear_wait_and_reset(bot, user.chat_id, back_to="auto")
#                     await safe_send_text(bot, user.chat_id, "⚠️ Произошла ошибка.\nНапишите в поддержку: @guard_gpt")
#                     await s.execute(update(Task).where(Task.id == task.id).values(delivered=True, status="completed"))
#                     await s.commit()
#                     log.info(json.dumps({"event": "kie_webhook.no_urls", "task_id": task_id}, ensure_ascii=False))
#                     return JSONResponse({"ok": True})

#                 # Списание кредитов
#                 credits_used = 1
#                 before = int(user.balance_credits or 0)
#                 new_balance = max(0, before - credits_used)
#                 await s.execute(
#                     update(User).where(User.id == user.id).values(balance_credits=new_balance)
#                 )
#                 await s.execute(
#                     update(Task).where(Task.id == task.id).values(status="completed", credits_used=credits_used)
#                 )
#                 await s.commit()

#                 # Маркер списания
#                 try:
#                     r = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_CACHE)
#                     await r.setex(f"credits:debited:{task_id}", 86400, "1")
#                     await r.aclose()
#                 except Exception:
#                     pass

#                 # Скачать результат
#                 image_url = result_urls[0]
#                 out_dir = "/tmp/nanobanana"
#                 os.makedirs(out_dir, exist_ok=True)
#                 local_path = os.path.join(out_dir, f"{task_id}.png")

#                 async with httpx.AsyncClient() as client:
#                     last_exc = None
#                     for _ in range(3):
#                         try:
#                             r = await client.get(image_url, timeout=120)
#                             r.raise_for_status()
#                             with open(local_path, "wb") as f:
#                                 f.write(r.content)
#                             last_exc = None
#                             break
#                         except Exception as e:
#                             last_exc = e
#                             await asyncio.sleep(2)

#                     if last_exc:
#                         await _clear_wait_and_reset(bot, user.chat_id, back_to="auto")
#                         await safe_send_text(bot, user.chat_id, "⚠️ Произошла ошибка.\nНапишите в поддержку: @guard_gpt")
#                         await s.execute(update(Task).where(Task.id == task.id).values(delivered=True))
#                         await s.commit()
#                         log.warning(json.dumps({"event": "kie_webhook.download_failed", "task_id": task_id}, ensure_ascii=False))
#                         return JSONResponse({"ok": True})

#                 # Отправить результат
#                 await send_generation_result(user.chat_id, task_id, task.prompt, image_url, local_path, bot)
#                 await s.execute(update(Task).where(Task.id == task.id).values(delivered=True))
#                 await s.commit()
#                 log.info(json.dumps({"event": "kie_webhook.success", "task_id": task_id}, ensure_ascii=False))
#                 return JSONResponse({"ok": True})

#             # ---- FAIL ----
#             if state == "fail":
#                 await _clear_wait_and_reset(bot, user.chat_id, back_to="auto")
                
#                 # Показываем сообщение ОДИН раз
#                 try:
#                     rr = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_CACHE)
#                     shown = await rr.setnx(f"msg:fail:{task_id}", "1")
#                     if shown:
#                         await rr.expire(f"msg:fail:{task_id}", 86400)
                        
#                         error_msg = "⚠️ Не удалось сгенерировать изображение. Попробуйте снова чуть позже: /gen"
#                         if fail_msg:
#                             error_msg = f"⚠️ Ошибка: {fail_msg[:200]}\n\nПопробуйте изменить промт или фото."
                        
#                         await safe_send_text(bot, user.chat_id, error_msg)
#                     await rr.aclose()
#                 except Exception:
#                     pass

#                 await s.execute(
#                     update(Task).where(Task.id == task.id).values(
#                         delivered=True,
#                         status="failed"
#                     )
#                 )
#                 await s.commit()
#                 log.info(json.dumps({
#                     "event": "kie_webhook.fail",
#                     "task_id": task_id,
#                     "fail_code": fail_code,
#                     "fail_msg": fail_msg
#                 }, ensure_ascii=False))
#                 return JSONResponse({"ok": True})

#             # ---- WAITING (промежуточный статус) ----
#             log.info(json.dumps({"event": "kie_webhook.waiting", "task_id": task_id}, ensure_ascii=False))
#             return JSONResponse({"ok": True})

#     finally:
#         await _release_webhook_lock(lock)

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional, Tuple

import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, update

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.redis import DefaultKeyBuilder, RedisStorage

from bot.routers.generation import send_generation_result
from bot.states import CreateStates, GenStates
from core.config import settings
from db.engine import SessionLocal
from db.models import Task, User
from services.telegram_safe import safe_send_text

router = APIRouter()
log = logging.getLogger("kie")


# -------------------- webhook lock --------------------

async def _acquire_webhook_lock(task_id: str, ttl: int = 180) -> Optional[Tuple[aioredis.Redis, str]]:
    r = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_CACHE)
    key = f"wb:lock:kie:{task_id}"
    try:
        ok = await r.set(key, "1", nx=True, ex=ttl)
        if ok:
            return r, key
        return None
    except Exception:
        try:
            await r.aclose()
        except Exception:
            pass
        return None


async def _release_webhook_lock(lock: Optional[Tuple[aioredis.Redis, str]]) -> None:
    if not lock:
        return
    r, key = lock
    try:
        await r.delete(key)
    except Exception:
        pass
    finally:
        try:
            await r.aclose()
        except Exception:
            pass


async def _clear_pending_marker(task_id: str) -> None:
    try:
        r = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_CACHE)
        await r.delete(f"task:pending:{task_id}")
        await r.aclose()
    except Exception:
        pass


# -------------------- FSM cleanup --------------------

async def _clear_wait_and_reset(bot, chat_id: int, *, back_to: str = "auto") -> None:
    r = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_FSM)
    try:
        storage = RedisStorage(redis=r, key_builder=DefaultKeyBuilder(with_bot_id=True))
        me = await bot.get_me()
        fsm = FSMContext(storage=storage, key=StorageKey(me.id, chat_id, chat_id))

        data = await fsm.get_data()
        wait_id = data.get("wait_msg_id")
        if wait_id:
            try:
                await bot.delete_message(chat_id, wait_id)
            except Exception:
                pass
            await fsm.update_data(wait_msg_id=None)

        mode = (data.get("mode") or "").lower()
        target = back_to
        if target == "auto":
            target = "create" if mode == "create" else "edit"

        if target == "create":
            await fsm.update_data(mode="create", edits=[], photos=[])
            await fsm.set_state(CreateStates.waiting_prompt)
        else:
            await fsm.set_state(GenStates.waiting_prompt)
    finally:
        await r.aclose()


# -------------------- webhook --------------------

@router.post("/webhook/kie")
async def kie_callback(req: Request):
    """
    Обработка webhook от KIE AI.
    """
    try:
        payload = await req.json()
    except Exception:
        log.warning(json.dumps({"event": "kie_webhook.invalid_json"}, ensure_ascii=False))
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)

    # Парсинг payload
    data = payload.get("data") or {}
    task_id = data.get("taskId")
    state = str(data.get("state") or "").lower()
    result_json = data.get("resultJson") or "{}"
    fail_code = data.get("failCode")
    fail_msg = data.get("failMsg")

    if not task_id:
        return JSONResponse({"ok": False, "error": "no_task_id"}, status_code=400)

    await _clear_pending_marker(task_id)

    # Эксклюзивная обработка
    lock = await _acquire_webhook_lock(task_id, ttl=180)
    if lock is None:
        log.info(json.dumps({"event": "kie_webhook.skip_locked", "task_id": task_id}, ensure_ascii=False))
        return JSONResponse({"ok": True})

    try:
        async with SessionLocal() as s:
            task = (await s.execute(select(Task).where(Task.task_uuid == task_id))).scalar_one_or_none()
            if not task:
                log.info(json.dumps({"event": "kie_webhook.no_task", "task_id": task_id}, ensure_ascii=False))
                return JSONResponse({"ok": True})

            if getattr(task, "delivered", False):
                log.info(json.dumps({"event": "kie_webhook.already_delivered", "task_id": task_id}, ensure_ascii=False))
                return JSONResponse({"ok": True})

            user = await s.get(User, task.user_id)
            bot = req.app.state.bot

            # ---- SUCCESS ----
            if state == "success":
                # Парсинг результатов
                try:
                    parsed = json.loads(result_json)
                    result_urls = parsed.get("resultUrls") or []
                except Exception:
                    result_urls = []

                if not result_urls:
                    await _clear_wait_and_reset(bot, user.chat_id, back_to="auto")
                    await safe_send_text(bot, user.chat_id, "⚠️ Произошла ошибка.\nНапишите в поддержку: @guard_gpt")
                    await s.execute(update(Task).where(Task.id == task.id).values(delivered=True, status="completed"))
                    await s.commit()
                    log.info(json.dumps({"event": "kie_webhook.no_urls", "task_id": task_id}, ensure_ascii=False))
                    return JSONResponse({"ok": True})

                # Списание кредитов
                credits_used = 1
                before = int(user.balance_credits or 0)
                new_balance = max(0, before - credits_used)
                await s.execute(
                    update(User).where(User.id == user.id).values(balance_credits=new_balance)
                )
                await s.execute(
                    update(Task).where(Task.id == task.id).values(status="completed", credits_used=credits_used)
                )
                await s.commit()

                # Маркер списания
                try:
                    r = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_CACHE)
                    await r.setex(f"credits:debited:{task_id}", 86400, "1")
                    await r.aclose()
                except Exception:
                    pass

                # Скачать результат
                image_url = result_urls[0]
                out_dir = "/tmp/nanobanana"
                os.makedirs(out_dir, exist_ok=True)
                local_path = os.path.join(out_dir, f"{task_id}.png")

                async with httpx.AsyncClient() as client:
                    last_exc = None
                    for attempt in range(1, 4):
                        try:
                            # ✅ ИСПРАВЛЕНО: добавлен Authorization header
                            headers = {"Authorization": f"Bearer {settings.KIE_API_KEY}"}
                            r = await client.get(image_url, headers=headers, timeout=120)
                            r.raise_for_status()
                            with open(local_path, "wb") as f:
                                f.write(r.content)
                            last_exc = None
                            log.info(json.dumps({"event": "kie_webhook.download_ok", "task_id": task_id, "attempt": attempt}, ensure_ascii=False))
                            break
                        except Exception as e:
                            last_exc = e
                            log.warning(json.dumps({"event": "kie_webhook.download_retry", "task_id": task_id, "attempt": attempt, "error": str(e)[:200]}, ensure_ascii=False))
                            await asyncio.sleep(2)

                    if last_exc:
                        await _clear_wait_and_reset(bot, user.chat_id, back_to="auto")
                        await safe_send_text(bot, user.chat_id, "⚠️ Произошла ошибка.\nНапишите в поддержку: @guard_gpt")
                        await s.execute(update(Task).where(Task.id == task.id).values(delivered=True))
                        await s.commit()
                        log.warning(json.dumps({"event": "kie_webhook.download_failed", "task_id": task_id}, ensure_ascii=False))
                        return JSONResponse({"ok": True})

                # Отправить результат
                await send_generation_result(user.chat_id, task_id, task.prompt, image_url, local_path, bot)
                await s.execute(update(Task).where(Task.id == task.id).values(delivered=True))
                await s.commit()
                log.info(json.dumps({"event": "kie_webhook.success", "task_id": task_id}, ensure_ascii=False))
                return JSONResponse({"ok": True})

            # ---- FAIL ----
            if state == "fail":
                await _clear_wait_and_reset(bot, user.chat_id, back_to="auto")
                
                # Показываем сообщение ОДИН раз
                try:
                    rr = aioredis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB_CACHE)
                    shown = await rr.setnx(f"msg:fail:{task_id}", "1")
                    if shown:
                        await rr.expire(f"msg:fail:{task_id}", 86400)
                        
                        error_msg = "⚠️ Не удалось сгенерировать изображение. Попробуйте снова чуть позже: /gen"
                        if fail_msg:
                            error_msg = f"⚠️ Ошибка: {fail_msg[:200]}\n\nПопробуйте изменить промт или фото."
                        
                        await safe_send_text(bot, user.chat_id, error_msg)
                    await rr.aclose()
                except Exception:
                    pass

                await s.execute(
                    update(Task).where(Task.id == task.id).values(
                        delivered=True,
                        status="failed"
                    )
                )
                await s.commit()
                log.info(json.dumps({
                    "event": "kie_webhook.fail",
                    "task_id": task_id,
                    "fail_code": fail_code,
                    "fail_msg": fail_msg
                }, ensure_ascii=False))
                return JSONResponse({"ok": True})

            # ---- WAITING (промежуточный статус) ----
            log.info(json.dumps({"event": "kie_webhook.waiting", "task_id": task_id}, ensure_ascii=False))
            return JSONResponse({"ok": True})

    finally:
        await _release_webhook_lock(lock)