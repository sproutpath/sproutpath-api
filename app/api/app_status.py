"""``/sproutpath/api/v1/appstatus`` — app status endpoint.

Returns maintenance flags and version control information read from
``json/app_status.json``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from app.config import PROJECT_ROOT
from app.models.app_status import AppStatusResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["app-status"])

_DATA_PATH = PROJECT_ROOT / "json" / "app_status.json"

_cache: Optional[Dict[str, Any]] = None


def _load_app_status() -> Dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    if not _DATA_PATH.is_file():
        raise FileNotFoundError(f"app_status.json not found at {_DATA_PATH}")
    with _DATA_PATH.open("r", encoding="utf-8") as f:
        _cache = json.load(f)
    return _cache


@router.get(
    "/sproutpath/api/v1/appstatus",
    response_model=AppStatusResponse,
    summary="App maintenance status and version control",
)
async def get_app_status() -> AppStatusResponse:
    """Return current maintenance status and minimum version requirements."""
    try:
        data = _load_app_status()
    except FileNotFoundError as e:
        logger.error("app_status.json missing: %s", e)
        raise HTTPException(status_code=503, detail="App status data unavailable")
    except Exception as e:
        logger.exception("Failed to load app_status.json")
        raise HTTPException(status_code=500, detail=f"Error reading app status: {e}")

    return AppStatusResponse.model_validate(data)
