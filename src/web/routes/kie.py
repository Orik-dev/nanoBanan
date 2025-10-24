# from __future__ import annotations
# import asyncio, json, os
# from fastapi import APIRouter, Request, HTTPException
# from fastapi.responses import JSONResponse
# from sqlalchemy import select
# import httpx

# from core.config import settings
# from db.engine import SessionLocal
# from db.models import Task, User
# from services.telegram_safe import safe_send_text
# from bot.routers.generation import send_generation_result

# router = APIRouter()

# @router.post("/webhook/kie")
# async def kie_callback(req: Request):
#     # простой секрет через query (?t=WEBHOOK_SECRET_TOKEN)
#     token = req.query_params.get("t")
#     if token != settings.WEBHOOK_SECRET_TOKEN:
#         raise HTTPException(403, "forbidden")

#     payload = await req.json()
#     data = payload.get("data") or {}

#     task_id = data.get("taskId")
#     state = str(data.get("state") or "").lower()
#     result_json = data.get("resultJson") or "{}"

#     if not task_id:
#         return JSONResponse({"ok": False, "error": "no_task_id"}, status_code=400)

#     try:
#         parsed = json.loads(result_json)
#     except Exception:
#         parsed = {}
#     result_urls = parsed.get("resultUrls") or []

#     async with SessionLocal() as s:
#         task = (await s.execute(select(Task).where(Task.task_uuid == task_id))).scalar_one_or_none()
#         if not task:
#             return JSONResponse({"ok": True})  # идемпотентность: неизвестная задача
#         if task.status == "completed":
#             return JSONResponse({"ok": True})  # уже отдали пользователю

#         user = await s.get(User, task.user_id)
#         bot = req.app.state.bot

#         if state == "success":
#             task.status = "completed"
#             task.credits_used = 1
#             await s.commit()

#             user.balance_credits = max(0, int(user.balance_credits) - 1)
#             await s.commit()

#             if not result_urls:
#                 await safe_send_text(bot, user.chat_id, "Произошла ошибка. Команда уже разбирается.")
#                 return JSONResponse({"ok": False, "error": "no_result_urls"})

#             image_url = result_urls[0]
#             out_dir = "/tmp/nanobanana"
#             os.makedirs(out_dir, exist_ok=True)
#             local_path = os.path.join(out_dir, f"{task_id}.png")

#             async with httpx.AsyncClient() as client:
#                 for _ in range(3):
#                     try:
#                         r = await client.get(image_url, timeout=120)
#                         r.raise_for_status()
#                         open(local_path, "wb").write(r.content)
#                         break
#                     except Exception:
#                         await asyncio.sleep(2)

#             await send_generation_result(user.chat_id, task_id, task.prompt, image_url, local_path, bot)

#         elif state == "fail":
#             task.status = "failed"
#             await s.commit()
#             await safe_send_text(bot, user.chat_id, "Произошла ошибка. Команда уже разбирается.")

#     return JSONResponse({"ok": True})
