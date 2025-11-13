from __future__ import annotations
import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from core.config import settings

log = logging.getLogger("kie")


class KieError(Exception):
    ...


def _j(event: str, **fields) -> str:
    return json.dumps({"event": event, **fields}, ensure_ascii=False)


class KieClient:
    """
    KIE AI Client для работы с google/nano-banana и google/nano-banana-edit
    """
    def __init__(self):
        self.base = settings.KIE_BASE.rstrip("/")
        self.create_url = f"{self.base}/jobs/createTask"
        self.status_url = f"{self.base}/jobs/recordInfo"
        self.headers = {
            "Authorization": f"Bearer {settings.KIE_API_KEY}",
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(90.0, read=90.0, connect=15.0)
        )

    async def aclose(self):
        try:
            await self._client.aclose()
        except Exception:
            pass

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.8, max=8),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def create_task(
        self,
        prompt: str,
        image_urls: Optional[List[str]] = None,
        callback_url: Optional[str] = None,
        *,
        output_format: Optional[str] = None,
        image_size: Optional[str] = None,
        cid: Optional[str] = None,
    ) -> str:
        """
        Создание задачи генерации
        - Если image_urls пустой/None -> используется google/nano-banana (создание)
        - Если image_urls есть -> используется google/nano-banana-edit (редактирование)
        """
        prompt = (prompt or "").strip()
        if not prompt:
            raise ValueError("prompt is empty")

        # Выбор модели
        has_images = bool(image_urls)
        model = "google/nano-banana-edit" if has_images else "google/nano-banana"

        payload: Dict[str, Any] = {
            "model": model,
            "input": {
                "prompt": prompt,
                "output_format": output_format or settings.KIE_OUTPUT_FORMAT,
                "image_size": image_size or settings.KIE_IMAGE_SIZE,
            }
        }

        # Добавляем image_urls только для edit модели
        if has_images:
            payload["input"]["image_urls"] = image_urls[:5]  # макс 5 фото

        if callback_url:
            payload["callBackUrl"] = callback_url

        log.info(_j(
            "kie.create.request",
            cid=cid,
            model=model,
            urls=len(image_urls) if image_urls else 0,
            prompt_len=len(prompt)
        ))

        # Retry logic с обработкой rate limit
        delay = 1.5
        for attempt in range(1, 4):
            r = await self._client.post(self.create_url, headers=self.headers, json=payload)

            if r.status_code == 429:
                ra = r.headers.get("Retry-After")
                wait_s = float(ra) if (ra and str(ra).isdigit()) else delay
                log.warning(_j("kie.create.rate_limited", cid=cid, attempt=attempt, retry_after=wait_s))
                await asyncio.sleep(wait_s)
                delay = min(delay * 1.6 + 0.4, 12.0)
                continue

            if 500 <= r.status_code < 600:
                log.error(_j("kie.create.5xx", cid=cid, status=r.status_code, body=(r.text or "")[:400]))
                await asyncio.sleep(delay)
                delay = min(delay * 1.6 + 0.4, 12.0)
                if attempt == 3:
                    raise KieError("upstream_5xx")
                continue

            # Парсинг ответа
            try:
                data = r.json()
            except Exception:
                data = {"code": r.status_code, "message": r.text}

            if r.status_code != 200 or int(data.get("code", 0)) != 200:
                msg = (data.get("message") or data.get("msg") or r.text or "failed")[:200]
                log.error(_j("kie.create.bad_response", cid=cid, status=r.status_code, msg=msg))
                raise KieError(f"bad_request:{msg}")

            task_id = (data.get("data") or {}).get("taskId")
            if not task_id:
                raise KieError("no_task_id")

            log.info(_j("kie.create.ok", cid=cid, task_id=task_id, model=model))
            return task_id

        raise KieError("create_failed")

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=0.8, max=8),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def get_status(self, task_id: str, *, cid: Optional[str] = None) -> Dict[str, Any]:
        """Получение статуса задачи"""
        r = await self._client.get(
            self.status_url,
            headers=self.headers,
            params={"taskId": task_id}
        )

        try:
            data = r.json()
        except Exception:
            data = {"code": r.status_code, "message": r.text}

        if r.status_code != 200 or int(data.get("code", 0)) != 200:
            msg = (data.get("message") or data.get("msg") or r.text or "failed")[:200]
            log.error(_j("kie.status.bad_response", cid=cid, status=r.status_code, msg=msg))
            raise KieError(f"status_failed:{msg}")

        task_data = data.get("data") or {}
        state = str(task_data.get("state") or "").lower()

        # Парсинг результатов
        result_urls: List[str] = []
        if state == "success":
            result_json = task_data.get("resultJson")
            if result_json:
                try:
                    parsed = json.loads(result_json)
                    result_urls = parsed.get("resultUrls") or []
                except Exception:
                    pass

        log.info(_j("kie.status.ok", cid=cid, task_id=task_id, state=state, n=len(result_urls)))
        return {
            "state": state,
            "result_urls": result_urls,
            "fail_code": task_data.get("failCode"),
            "fail_msg": task_data.get("failMsg"),
            "raw": task_data
        }

    async def wait_until_done(
        self,
        task_id: str,
        timeout_s: int,
        *,
        cid: Optional[str] = None
    ) -> Dict[str, Any]:
        """Ожидание завершения задачи"""
        terminal = {"success", "fail"}
        start = time.time()
        delay = 2.0

        while time.time() - start < timeout_s:
            d = await self.get_status(task_id, cid=cid)
            state = d.get("state")

            if state in terminal:
                log.info(_j("kie.done", cid=cid, task_id=task_id, final_state=state))
                return d

            await asyncio.sleep(delay)
            delay = min(delay + 0.5, 6.0)

        raise KieError("timeout")