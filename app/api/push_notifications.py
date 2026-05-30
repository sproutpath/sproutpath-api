"""``/sproutpath/api/v1/notifications`` — push notifications endpoint.

Returns the list of app update notifications read from
``json/push_notification.json``.

Query parameters:
  - ``notify_only``: if true, return only entries where ``notifyUsers`` is true.
  - ``version``: filter entries for a specific app version string.
  - ``type``: filter by notification type (``feature``, ``bugfix``,
    ``enhancement``, ``security``).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from app.config import PROJECT_ROOT
from app.models.push_notification import NotificationEntry, PushNotificationsResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["notifications"])

_DATA_PATH = PROJECT_ROOT / "json" / "push_notification.json"

_cache: Optional[List[Dict[str, Any]]] = None


def _load_notifications() -> List[Dict[str, Any]]:
    global _cache
    if _cache is not None:
        return _cache
    if not _DATA_PATH.is_file():
        raise FileNotFoundError(f"push_notification.json not found at {_DATA_PATH}")
    with _DATA_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    _cache = data.get("entries", [])
    return _cache


@router.get(
    "/sproutpath/api/v1/notifications",
    response_model=PushNotificationsResponse,
    summary="Push notification entries",
)
async def get_notifications(
    notify_only: bool = Query(
        default=False,
        description="When true, return only entries where notifyUsers is true.",
    ),
    version: Optional[str] = Query(
        default=None,
        description="Filter entries for a specific app version string (e.g. '1.3.0').",
    ),
    type: Optional[str] = Query(
        default=None,
        description="Filter by type: feature, bugfix, enhancement, security.",
    ),
) -> PushNotificationsResponse:
    """Return push notification entries, optionally filtered."""
    entries = _load_notifications()

    if notify_only:
        entries = [e for e in entries if e.get("notifyUsers", True)]
    if version:
        entries = [e for e in entries if e.get("version") == version]
    if type:
        entries = [e for e in entries if e.get("type") == type.lower()]

    validated = [NotificationEntry.model_validate(e) for e in entries]
    return PushNotificationsResponse(entries=validated, total=len(validated))
