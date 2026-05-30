"""/sproutpath/api/v1/getstudy — study content webservice.

Endpoints
---------
GET  /sproutpath/api/v1/getstudy/content
    Return the full age-group → category → item hierarchy from
    study_content.json, optionally sliced to one age group.
    This drives the Study tab UI (filter chips, section headers, tiles).

GET  /sproutpath/api/v1/getstudy/content/age-groups
    Lightweight list of all age groups with min/max bounds.
    Use to build the age-group picker.

GET  /sproutpath/api/v1/getstudy/videos
    Videos for a specific ``dest`` value, filtered by age / age_group
    and language.  This is what the app calls when the user taps a tile.

Query parameters for /getstudy/videos
    dest         (required)  Navigation dest from study_content item, e.g. "math"
    age          (optional)  Child's age — resolves to the right age group
    age_group_id (optional)  Explicit age-group id ("toddler" / "early_learner" …)
    language     (optional)  ISO code or full language name
    page         (default 1)
    page_size    (default 20, max 100)
"""

from __future__ import annotations

import json
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.models.study_content import (
    AgeGroup,
    StudyContentResponse,
    StudyItemVideoResponse,
)
from app.services.study_content import (
    get_videos_for_dest,
    load_study_content,
    resolve_age_group,
    resolve_age_group_by_id,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["study"])


# ── Helper ────────────────────────────────────────────────────────────────────

def _load_or_503() -> dict:
    try:
        return load_study_content()
    except FileNotFoundError as e:
        logger.error("study_content.json missing: %s", e)
        raise HTTPException(status_code=503, detail="Study content data unavailable")
    except Exception:
        logger.exception("Failed to load study_content.json")
        raise HTTPException(status_code=500, detail="Error loading study content")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "/sproutpath/api/v1/getstudy/content",
    response_model=StudyContentResponse,
    summary="Study tab hierarchy — age groups, categories, and items",
    description=(
        "Returns the full study-content catalogue structured as:\n"
        "  age_groups → categories → items\n\n"
        "Pass ``age`` or ``age_group_id`` to get a single age group's slice.  "
        "Without any filter, all five age groups are returned."
    ),
)
async def get_study_content(
    age: Optional[int] = Query(
        default=None,
        ge=0,
        le=25,
        description="Child's age — returns only the matching age group.",
        examples=[8],
    ),
    age_group_id: Optional[str] = Query(
        default=None,
        description="Explicit age-group id: toddler | early_learner | junior | preteen | teen",
        examples=["junior"],
    ),
) -> StudyContentResponse:
    """Return the study-content hierarchy, optionally filtered to one age group."""
    content = _load_or_503()
    all_groups: List[dict] = content.get("ageGroups", [])

    if age_group_id:
        matched = resolve_age_group_by_id(age_group_id, content)
        if not matched:
            raise HTTPException(
                status_code=404,
                detail=f"Age group '{age_group_id}' not found. "
                       f"Valid values: {[ag['id'] for ag in all_groups]}",
            )
        groups = [matched]
    elif age is not None:
        matched = resolve_age_group(age, content)
        if not matched:
            raise HTTPException(
                status_code=404,
                detail=f"No age group found for age={age}.",
            )
        groups = [matched]
    else:
        groups = all_groups

    return StudyContentResponse(
        version=content.get("version", "1.0"),
        total_age_groups=len(groups),
        age_groups=[AgeGroup.model_validate(g) for g in groups],
    )


@router.get(
    "/sproutpath/api/v1/getstudy/content/age-groups",
    summary="List all age groups (id, label, min/max age)",
)
async def list_age_groups() -> dict:
    """Return a compact list of age groups — use to build the age-group picker."""
    content = _load_or_503()
    groups = [
        {
            "id":     ag["id"],
            "label":  ag["label"],
            "minAge": ag["minAge"],
            "maxAge": ag["maxAge"],
        }
        for ag in content.get("ageGroups", [])
    ]
    return {"total": len(groups), "age_groups": groups}


@router.get(
    "/sproutpath/api/v1/getstudy/videos",
    response_model=StudyItemVideoResponse,
    summary="Videos for a study item — supply dest + age (or age_group_id)",
    description=(
        "Returns paginated YouTube video records for the tapped study tile.\n\n"
        "``dest`` comes directly from the ``dest`` field of the StudyItem.\n"
        "``age`` or ``age_group_id`` narrows both which videos are age-appropriate\n"
        "and echoes the resolved age-group metadata back to the caller.\n\n"
        "**Valid dest values:** math, measurements, currency, english, "
        "motherTongue, customStories, speakingPractice, science"
    ),
)
async def get_study_videos(
    dest: str = Query(
        ...,
        description="Study item dest value, e.g. math, science, motherTongue",
        examples=["math"],
    ),
    age: Optional[int] = Query(
        default=None,
        ge=0,
        le=25,
        description="Child's age in years.",
        examples=[8],
    ),
    age_group_id: Optional[str] = Query(
        default=None,
        description="Age-group id instead of age: toddler | early_learner | junior | preteen | teen",
        examples=["junior"],
    ),
    language: Optional[str] = Query(
        default=None,
        description=(
            "Language filter.  For motherTongue/customStories dests this "
            "narrows to a specific language (e.g. 'hi', 'telugu').  "
            "For other dests it filters by video language."
        ),
        examples=["en"],
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> StudyItemVideoResponse:
    """Return videos for a study item ``dest``, age-filtered and paginated."""
    try:
        result = await get_videos_for_dest(
            dest=dest,
            age=age,
            age_group_id=age_group_id,
            language=language,
            page=page,
            page_size=page_size,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="Video catalogue unavailable")
    except Exception as exc:
        logger.exception("get_videos_for_dest failed")
        raise HTTPException(status_code=500, detail=str(exc))

    return StudyItemVideoResponse(**result)


@router.get(
    "/sproutpath/api/v1/getstudy/content/dests",
    summary="List all available dest values with their category and age-group scope",
)
async def list_dests() -> dict:
    """Utility endpoint — returns every (dest, category, age_group_id) triple."""
    content = _load_or_503()
    dests = []
    seen: set = set()
    for ag in content.get("ageGroups", []):
        for cat in ag.get("categories", []):
            for item in cat.get("items", []):
                key = (item["dest"], ag["id"])
                if key in seen:
                    continue
                seen.add(key)
                dests.append({
                    "dest":         item["dest"],
                    "item_id":      item["id"],
                    "titleKey":     item["titleKey"],
                    "emoji":        item["emoji"],
                    "color":        item["color"],
                    "category_id":  cat["id"],
                    "category_heading": cat["heading"],
                    "age_group_id": ag["id"],
                    "age_group_label": ag["label"],
                })
    return {"total": len(dests), "dests": dests}
