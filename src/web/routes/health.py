from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

router = APIRouter()

@router.get("/healthz")
async def healthz():
    return PlainTextResponse("ok")
