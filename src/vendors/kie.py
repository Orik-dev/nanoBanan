# from __future__ import annotations
# import asyncio, json, logging, time
# from typing import Any, Dict, List, Optional
# import httpx
# from core.config import settings

# log = logging.getLogger("kie")

# class KieError(Exception): ...

# def _j(event: str, **fields) -> str:
#     return json.dumps({"event": event, **fields}, ensure_ascii=False)

# class KieClient:
#     def __init__(self):
#         base = settings.KIE_BASE.rstrip("/")
#         self.create_url = f"{base}/jobs/createTask"
#         self.record_url = f"{base}/jobs/recordInfo"
#         self.hdr_json = {
#             "Authorization": f"Bearer {settings.KIE_API_KEY}",
#             "Content-Type": "application/json",
#         }
#         self._client = httpx.AsyncClient(
#             timeout=httpx.Timeout(90.0, read=90.0, connect=15.0)
#         )

#     async def aclose(self):
#         try:
#             await self._client.aclose()
#         except Exception:
#             pass

#     async def create_task(
#         self,
#         prompt: str,
#         image_urls: List[str],
#         call_back_url: Optional[str],
#         *,
#         output_format: Optional[str] = None,
#         image_size: Optional[str] = None,
#         cid: Optional[str] = None,
#     ) -> str:
#         prompt = (prompt or "").strip()
#         if not prompt:
#             raise ValueError("prompt is empty")
#         if not image_urls:
#             raise ValueError("image_urls empty")

#         payload: Dict[str, Any] = {
#             "model": settings.KIE_MODEL,
#             "input": {
#                 "prompt": prompt,
#                 "image_urls": image_urls[:5],
#                 "output_format": (output_format or settings.KIE_OUTPUT_FORMAT),
#                 "image_size": (image_size or settings.KIE_IMAGE_SIZE),
#             },
#         }
#         if call_back_url:
#             payload["callBackUrl"] = call_back_url

#         log.info(_j("kie.create.request", cid=cid, urls=len(image_urls), prompt_len=len(prompt)))

#         # простая защита от rate-limit: 3 попытки + honor Retry-After
#         delay = 1.5
#         for attempt in range(1, 4):
#             r = await self._client.post(self.create_url, headers=self.hdr_json, json=payload)
#             if r.status_code == 429:
#                 ra = r.headers.get("Retry-After")
#                 wait_s = float(ra) if (ra and str(ra).isdigit()) else delay
#                 log.warning(_j("kie.create.rate_limited", cid=cid, attempt=attempt, retry_after=wait_s))
#                 await asyncio.sleep(wait_s)
#                 delay = min(delay * 1.6 + 0.4, 12.0)
#                 continue
#             if 500 <= r.status_code < 600:
#                 log.error(_j("kie.create.5xx", cid=cid, status=r.status_code, body=(r.text or "")[:400]))
#                 await asyncio.sleep(delay)
#                 delay = min(delay * 1.6 + 0.4, 12.0)
#                 if attempt == 3:
#                     raise KieError("upstream_5xx")
#                 continue

#             # 200/4xx
#             try:
#                 data = r.json()
#             except Exception:
#                 data = {"code": r.status_code, "message": r.text}
#             if r.status_code != 200 or int(data.get("code", 0)) != 200:
#                 msg = (data.get("message") or data.get("msg") or r.text or "failed")[:200]
#                 log.error(_j("kie.create.bad_response", cid=cid, status=r.status_code, msg=msg))
#                 raise KieError(f"bad_request:{msg}")

#             task_id = (data.get("data") or {}).get("taskId")
#             if not task_id:
#                 raise KieError("no_task_id")
#             log.info(_j("kie.create.ok", cid=cid, task_id=task_id))
#             return task_id

#         raise KieError("create_failed")

#     async def get_status(self, task_id: str, *, cid: Optional[str] = None) -> Dict[str, Any]:
#         r = await self._client.get(self.record_url, headers=self.hdr_json, params={"taskId": task_id})
#         try:
#             data = r.json()
#         except Exception:
#             data = {"code": r.status_code, "message": r.text}
#         if r.status_code != 200 or int(data.get("code", 0)) != 200:
#             msg = (data.get("message") or data.get("msg") or r.text or "failed")[:200]
#             log.error(_j("kie.status.bad_response", cid=cid, status=r.status_code, msg=msg))
#             raise KieError(f"status_failed:{msg}")

#         d = data.get("data") or {}
#         state = str(d.get("state") or "").lower()  # waiting|queuing|generating|success|fail
#         result_json = d.get("resultJson")
#         result_urls: List[str] = []
#         if state == "success" and result_json:
#             try:
#                 parsed = json.loads(result_json)
#                 result_urls = parsed.get("resultUrls") or []
#             except Exception:
#                 pass
#         log.info(_j("kie.status.ok", cid=cid, task_id=task_id, state=state, n=len(result_urls)))
#         return {"state": state, "result_urls": result_urls, "raw": d}

#     async def wait_until_done(self, task_id: str, timeout_s: int, *, cid: Optional[str] = None) -> Dict[str, Any]:
#         start = time.time()
#         delay = 2.0
#         while time.time() - start < timeout_s:
#             d = await self.get_status(task_id, cid=cid)
#             st = d.get("state")
#             if st in {"success", "fail"}:
#                 log.info(_j("kie.done", cid=cid, task_id=task_id, final_state=st))
#                 return d
#             await asyncio.sleep(delay)
#             delay = min(delay + 0.5, 6.0)
#         raise KieError("timeout")
