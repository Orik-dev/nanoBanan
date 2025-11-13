# # src/vendors/freepik.py
# from __future__ import annotations
# import asyncio, base64, hashlib, hmac, json, logging, time
# from typing import Any, Dict, List, Optional
# import httpx
# from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# from core.config import settings

# log = logging.getLogger("freepik")

# class FreepikError(Exception): ...

# def _j(event: str, **fields): return json.dumps({"event": event, **fields}, ensure_ascii=False)

# class FreepikClient:
#     """
#     Обертка над Freepik Gemini 2.5 Flash Image Preview:
#       POST {BASE}             -> создать задачу
#       GET  {BASE}/{task_id}   -> статус/результат
#     """
#     def __init__(self):
#         self.base = settings.FREEPIK_BASE.rstrip("/")
#         self.headers = {
#             "x-freepik-api-key": settings.FREEPIK_API_KEY,
#             "Content-Type": "application/json",
#         }
#         self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=30.0, connect=10.0))

#     async def aclose(self):
#         try:
#             await self._client.aclose()
#         except Exception:
#             pass

#     @retry(stop=stop_after_attempt(3),
#            wait=wait_exponential(multiplier=0.8, max=8),
#            retry=retry_if_exception_type(httpx.HTTPError),
#            reraise=True)
#     async def create_task(self, prompt: str, *, reference_images: Optional[List[str]] = None,
#                           webhook_url: Optional[str] = None, cid: Optional[str] = None) -> str:
#         """
#         reference_images: список строк (base64 без data:, либо URL), до 3 шт.
#         Возвращает task_id.
#         """
#         payload: Dict[str, Any] = {"prompt": (prompt or "").strip()}
#         if not payload["prompt"]:
#             raise ValueError("prompt is empty")

#         if reference_images:
#             payload["reference_images"] = reference_images[:3]
#         if webhook_url:
#             payload["webhook_url"] = webhook_url

#         log.info(_j("freepik.create.request", cid=cid, prompt_len=len(payload["prompt"]),
#                     refs=len(payload.get("reference_images") or [])))

#         r = await self._client.post(self.base, headers=self.headers, json=payload)

#         # типичные коды из доки
#         if r.status_code == 401:
#             raise FreepikError("Неверный API-ключ (401).")
#         if r.status_code == 429:
#             delay = 3
#             try:
#                 ra = r.headers.get("Retry-After")
#                 if ra and ra.isdigit(): delay = int(ra)
#             except Exception:
#                 pass
#             log.warning(_j("freepik.create.rate_limited", cid=cid, retry_after=delay))
#             await asyncio.sleep(delay)
#             r = await self._client.post(self.base, headers=self.headers, json=payload)

#         if 500 <= r.status_code < 600:
#             log.error(_j("freepik.create.5xx", cid=cid, status=r.status_code, body=r.text[:400]))
#         if r.status_code == 400:
#             # отдаём текст пользователю (укороченный)
#             raise FreepikError(f"Запрос отклонён (400): {r.text[:180]}")

#         r.raise_for_status()
#         data = r.json() or {}
#         data = data.get("data") or data  # иногда есть обертка data
#         task_id = data.get("task_id") or data.get("id")
#         if not task_id:
#             raise FreepikError(f"Не вернулся task_id: {data}")
#         log.info(_j("freepik.create.ok", cid=cid, task_id=task_id))
#         return task_id

#     @retry(stop=stop_after_attempt(5),
#            wait=wait_exponential(multiplier=0.8, max=8),
#            retry=retry_if_exception_type(httpx.HTTPError),
#            reraise=True)
#     async def get_status(self, task_id: str, *, cid: Optional[str] = None) -> Dict[str, Any]:
#         url = f"{self.base}/{task_id}"
#         r = await self._client.get(url, headers={"x-freepik-api-key": settings.FREEPIK_API_KEY})
#         if r.status_code == 401:
#             raise FreepikError("Неверный API-ключ (401).")
#         if r.status_code == 404:
#             raise FreepikError("Задача не найдена (404).")
#         if r.status_code == 429:
#             await asyncio.sleep(2)
#             r = await self._client.get(url, headers={"x-freepik-api-key": settings.FREEPIK_API_KEY})
#         r.raise_for_status()

#         data = r.json() or {}
#         data = data.get("data") or data
#         # Унифицируем
#         status = str(data.get("status") or "").upper() or "UNKNOWN"
#         generated = data.get("generated") or data.get("result") or []
#         return {"status": status, "generated": generated, **data}

#     async def wait_until_done(self, task_id: str, timeout_s: int, *, cid: Optional[str] = None) -> Dict[str, Any]:
#         terminal = {"COMPLETED", "FAILED", "ERROR", "MODERATION_BLOCKED"}
#         start, delay = time.time(), 2.0
#         while time.time() - start < timeout_s:
#             d = await self.get_status(task_id, cid=cid)
#             st = str(d.get("status") or "").upper()
#             if (not st) or (st in terminal):
#                 return d
#             await asyncio.sleep(delay)
#             delay = min(delay + 0.5, 6.0)
#         raise FreepikError("Превышено время ожидания результата.")

# # --- верификация вебхука Freepik ---

# def verify_webhook(raw_body: bytes, headers: dict) -> bool:
#     wid = (headers.get("webhook-id") or headers.get("Webhook-Id") or "").strip()
#     ts  = (headers.get("webhook-timestamp") or headers.get("Webhook-Timestamp") or "").strip()
#     sig_header = (headers.get("webhook-signature") or headers.get("Webhook-Signature") or "").strip()
#     if not (wid and ts and sig_header):
#         return False

#     content = f"{wid}.{ts}.{raw_body.decode('utf-8')}"
#     expected = hmac.new(settings.FREEPIK_WEBHOOK_SECRET.encode("utf-8"),
#                         content.encode("utf-8"),
#                         digestmod="sha256").digest()
#     expected_b64 = base64.b64encode(expected).decode("utf-8")

#     # поддержка нескольких версий ключей
#     pairs = [p.strip() for p in sig_header.split() if p.strip()]
#     for p in pairs:
#         try:
#             _v, sig = p.split(",", 1)
#             if sig.strip() == expected_b64:
#                 return True
#         except Exception:
#             continue
#     return False


from __future__ import annotations
import asyncio
import base64
import hmac
import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from core.config import settings

log = logging.getLogger("freepik")


class FreepikError(Exception):
    ...


def _j(event: str, **fields) -> str:
    return json.dumps({"event": event, **fields}, ensure_ascii=False)


class FreepikClient:
    """
    Обёртка над Freepik Gemini 2.5 Flash – Image Preview:
      POST {BASE}           -> создать задачу
      GET  {BASE}/{task_id} -> статус/результат
    """
    def __init__(self) -> None:
        self.base = settings.FREEPIK_BASE.rstrip("/")
        self.headers = {
            "x-freepik-api-key": settings.FREEPIK_API_KEY,
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, read=30.0, connect=10.0)
        )

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except Exception:
            pass

    async def create_task(
        self,
        prompt: str,
        *,
        reference_images: Optional[List[str]] = None,
        webhook_url: Optional[str] = None,
        cid: Optional[str] = None,
    ) -> str:
        """
        reference_images: список строк (base64 без data:, либо URL), до 3 шт.
        Возвращает task_id.
        """
        prompt = (prompt or "").strip()
        if not prompt:
            raise ValueError("prompt is empty")

        payload: Dict[str, Any] = {"prompt": prompt}
        if reference_images:
            payload["reference_images"] = reference_images[:3]
        if webhook_url:
            payload["webhook_url"] = webhook_url

        log.info(
            _j(
                "freepik.create.request",
                cid=cid,
                prompt_len=len(prompt),
                refs=len(payload.get("reference_images") or []),
            )
        )

        # Управляемая обработка rate limit (429)
        max_tries = 6          # 1 + 5 ретраев
        delay = 1.5            # базовая задержка без Retry-After

        for attempt in range(1, max_tries + 1):
            r = await self._client.post(self.base, headers=self.headers, json=payload)

            if r.status_code == 429:
                ra = r.headers.get("Retry-After")
                try:
                    wait_s = float(ra) if (ra and str(ra).isdigit()) else delay
                except Exception:
                    wait_s = delay
                log.warning(
                    _j(
                        "freepik.create.rate_limited",
                        cid=cid,
                        retry_after=wait_s,
                        attempt=attempt,
                    )
                )
                await asyncio.sleep(wait_s)
                delay = min(delay * 1.6 + 0.4, 15.0)
                if attempt == max_tries:
                    raise FreepikError("rate_limited")
                continue

            if r.status_code == 401:
                raise FreepikError("invalid_api_key")

            if r.status_code == 400:
                raise FreepikError(f"bad_request:{r.text[:200]}")

            if 500 <= r.status_code < 600:
                log.error(
                    _j(
                        "freepik.create.5xx",
                        cid=cid,
                        status=r.status_code,
                        body=(r.text or "")[:400],
                    )
                )
                if attempt == max_tries:
                    raise FreepikError("upstream_5xx")
                await asyncio.sleep(delay)
                delay = min(delay * 1.6 + 0.4, 15.0)
                continue

            r.raise_for_status()
            data = r.json() or {}
            data = data.get("data") or data
            task_id = data.get("task_id") or data.get("id")
            if not task_id:
                raise FreepikError("no_task_id")
            log.info(_j("freepik.create.ok", cid=cid, task_id=task_id))
            return task_id

        # не должно дойти
        raise FreepikError("unknown")

    async def get_status(self, task_id: str, *, cid: Optional[str] = None) -> Dict[str, Any]:
        url = f"{self.base}/{task_id}"
        r = await self._client.get(url, headers={"x-freepik-api-key": settings.FREEPIK_API_KEY})
        if r.status_code == 401:
            raise FreepikError("invalid_api_key")
        if r.status_code == 404:
            raise FreepikError("not_found")
        if r.status_code == 429:
            await asyncio.sleep(2.0)
            r = await self._client.get(url, headers={"x-freepik-api-key": settings.FREEPIK_API_KEY})
        r.raise_for_status()
        data = r.json() or {}
        data = data.get("data") or data
        status = str(data.get("status") or "").upper() or "UNKNOWN"
        generated = data.get("generated") or data.get("result") or []
        log.info(_j("freepik.status.ok", cid=cid, task_id=task_id, status=status))
        return {"status": status, "generated": generated, **data}

    async def wait_until_done(self, task_id: str, timeout_s: int, *, cid: Optional[str] = None) -> Dict[str, Any]:
        terminal = {"COMPLETED", "FAILED", "ERROR", "MODERATION_BLOCKED"}
        start = time.time()
        delay = 2.0
        while time.time() - start < timeout_s:
            d = await self.get_status(task_id, cid=cid)
            st = str(d.get("status") or "").upper()
            if (not st) or (st in terminal):
                log.info(_j("freepik.done", cid=cid, task_id=task_id, final_status=st))
                return d
            await asyncio.sleep(delay)
            delay = min(delay + 0.5, 6.0)
        raise FreepikError("timeout")


# --- Проверка подписи вебхука Freepik (HMAC-SHA256 + base64) ---
def verify_webhook(raw_body: bytes, headers: dict) -> bool:
    """
    Headers:
      webhook-id, webhook-timestamp, webhook-signature="v1,BASE64 v2,BASE64 ..."
    Подпись по строке: "<id>.<timestamp>.<raw_body>"
    """
    wid = (headers.get("webhook-id") or headers.get("Webhook-Id") or "").strip()
    ts = (headers.get("webhook-timestamp") or headers.get("Webhook-Timestamp") or "").strip()
    sig_header = (headers.get("webhook-signature") or headers.get("Webhook-Signature") or "").strip()
    if not (wid and ts and sig_header):
        return False

    content_to_sign = f"{wid}.{ts}.{raw_body.decode('utf-8')}"
    digest = hmac.new(
        settings.FREEPIK_WEBHOOK_SECRET.encode("utf-8"),
        content_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected_b64 = base64.b64encode(digest).decode("utf-8")

    # Поддержка ротации ключей: "v1,xxxx v2,yyyy"
    pairs = [p.strip() for p in sig_header.split() if p.strip()]
    for p in pairs:
        try:
            _version, sig = p.split(",", 1)
            if sig.strip() == expected_b64:
                return True
        except Exception:
            continue
    return False
