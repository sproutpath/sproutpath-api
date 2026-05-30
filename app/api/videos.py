"""``/sproutpath/api/v1/getvideos`` — main video listing endpoint.

Filters:

* ``languages``: optional, repeatable. Accepts ISO codes (``te``) or
  full names (``telugu``). Comma-separated values inside a single
  ``languages=`` param are also supported (e.g. ``?languages=te,hi``).
* ``age``: optional integer. Keeps videos whose ``age_range`` covers
  the age. Videos without an explicit ``age_range`` are not dropped.

Both filters apply with AND. With no filters, the full catalogue is
returned in the requested response envelope.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.models.video import Video, VideosResponse
from app.services.filtering import filter_videos
from app.services.upstream import load_payload

logger = logging.getLogger(__name__)

# The path prefix is part of the route — not a router-level prefix —
# because the spec calls the route out by full path. Keeping the full
# path on the decorator makes the mapping obvious when grepping.
router = APIRouter(tags=["videos"])


def _split_csv(values: Optional[List[str]]) -> List[str]:
    """Expand comma-separated entries inside repeatable query params.

    FastAPI hands ``Query(...)`` lists as-is; clients sometimes encode
    multi-value params either as repeated keys (``?languages=te&languages=hi``)
    or as a single comma-separated value (``?languages=te,hi``). We
    accept both, so the endpoint Just Works regardless of how the
    caller constructs the URL.
    """
    if not values:
        return []
    out: List[str] = []
    for v in values:
        for part in v.split(","):
            part = part.strip()
            if part:
                out.append(part)
    return out


@router.get(
    "/sproutpath/api/v1/getvideos",
    response_model=VideosResponse,
    summary="List videos with optional language and age filters",
    response_model_exclude_none=False,
)
async def get_videos(
    languages: Optional[List[str]] = Query(
        default=None,
        description=(
            "One or more languages to include. Accepts ISO codes "
            "(`te`, `hi`, `en`) or full lowercase names (`telugu`, "
            "`hindi`). Repeat the param or pass comma-separated values."
        ),
        examples=["te", "telugu,hindi"],
    ),
    age: Optional[int] = Query(
        default=None,
        ge=0,
        le=120,
        description=(
            "Child's age in years. Videos whose `age_range` covers this "
            "age are kept; videos with no `age_range` are always kept."
        ),
        examples=[8],
    ),
) -> VideosResponse:
    """Return the filtered video catalogue in the standard envelope."""

    try:
        payload = await load_payload()
    except FileNotFoundError as e:
        # Misconfiguration — bubble out as a 503 so the client knows
        # the API is alive but the data isn't reachable.
        logger.error("Upstream data unavailable: %s", e)
        raise HTTPException(
            status_code=503, detail="Video catalogue is not configured"
        )
    except Exception as e:
        # Networking / JSON-decode issues with a remote feed. Same 503
        # — the API itself is fine, the dependency isn't.
        logger.exception("Failed to load videos feed")
        raise HTTPException(
            status_code=503, detail=f"Upstream video feed unavailable: {e}"
        )

    wanted_languages = _split_csv(languages)
    filtered = filter_videos(
        payload["videos"], languages=wanted_languages, age=age
    )

    # Validate each upstream record through the Video schema. This
    # double duty: it strips any unknown fields and gives us a clean
    # 500 instead of mysterious 200-with-broken-shape if the feed ever
    # ships malformed records.
    videos = [Video.model_validate(v) for v in filtered]

    return VideosResponse(
        version=payload["version"],
        generated=payload["generated"],
        description=payload["description"],
        total_videos=len(videos),
        videos=videos,
    )
