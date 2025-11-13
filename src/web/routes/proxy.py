from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
import logging

router = APIRouter()
log = logging.getLogger("proxy")

TEMP_DIR = Path("/app/temp_inputs")


@router.get("/proxy/image/{filename}")
async def proxy_image(filename: str):
    """
    Прокси для раздачи временных файлов KIE AI.
    """
    # Защита от path traversal
    if ".." in filename or "/" in filename:
        raise HTTPException(403, "Invalid filename")
    
    filepath = TEMP_DIR / filename
    
    if not filepath.exists():
        log.warning(f"File not found: {filename}")
        raise HTTPException(404, "File not found")
    
    # Определяем media type
    ext = filepath.suffix.lower()
    media_type_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }
    media_type = media_type_map.get(ext, "application/octet-stream")
    
    return FileResponse(
        filepath,
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=3600",
        }
    )