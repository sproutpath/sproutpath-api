"""/sproutpath/api/v1/analytics — analytics webservice.

Mobile tracking (one GET call from the app)
-------------------------------------------
GET /sproutpath/api/v1/analytics/track

  Query params (all passed by the mobile app):
    unique_id    *  User's registration / unique ID
    age          *  Child's age (integer, e.g. 8)
    country      *  ISO-3166-1 alpha-2 geo code (e.g. SG, IN, US)
    city            City name (e.g. Singapore, Mumbai)
    language     *  BCP-47 app language code (e.g. en, hi, te)
    activities   *  Comma-separated selected activity IDs
                    (e.g. math,science  or  motherTongue,english)
    platform        ios | android  (default: ios)
    app_version     Semver string (e.g. 2.1.0)

  (* = required)

  Returns 200 with { "status": "ok", "tracked": <record> }

Admin read endpoints (dashboard / reporting)
--------------------------------------------
GET /sproutpath/api/v1/analytics/traffic
GET /sproutpath/api/v1/analytics/users/{unique_id}
GET /sproutpath/api/v1/analytics/geo
GET /sproutpath/api/v1/analytics/languages
GET /sproutpath/api/v1/analytics/activities
GET /sproutpath/api/v1/analytics/age-groups
GET /sproutpath/api/v1/analytics/dashboard

All read endpoints accept optional date range:
  ?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, status

from app.models.analytics import (
    ActivityBreakdownItem,
    AgeGroupBreakdownItem,
    DashboardStats,
    GeoBreakdownItem,
    LanguageBreakdownItem,
    MobileAnalyticsRecord,
    TrafficSummary,
    UserActivitySummary,
)
from app.services import analytics_store as store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sproutpath/api/v1/analytics", tags=["analytics"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ─────────────────────────────────────────────────────────────────────────────
#  MOBILE  — single lightweight GET the app calls
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/track",
    status_code=status.HTTP_200_OK,
    summary="📱 Mobile — track a user session with flat query params",
    description="""
Called by the iOS/Android app whenever the user interacts with study content.

**Required params:** `unique_id`, `age`, `country`, `language`, `activities`

**`activities`** is a comma-separated list of the study dest IDs the user
selected in this session, e.g. `math,science` or `motherTongue,english`.

Example:
```
GET /sproutpath/api/v1/analytics/track
    ?unique_id=usr_abc123
    &age=8
    &country=SG
    &city=Singapore
    &language=en
    &activities=math,science
    &platform=ios
    &app_version=2.1.0
```
""",
)
async def track_session(
    unique_id: str = Query(
        ...,
        description="User's registration / unique ID",
        examples=["usr_abc123"],
    ),
    age: int = Query(
        ...,
        ge=1,
        le=25,
        description="Child's age in years",
        examples=[8],
    ),
    country: str = Query(
        ...,
        min_length=2,
        max_length=3,
        description="ISO-3166-1 alpha-2 country code, e.g. SG",
        examples=["SG"],
    ),
    language: str = Query(
        ...,
        description="BCP-47 app language code, e.g. en, hi, te",
        examples=["en"],
    ),
    activities: str = Query(
        ...,
        description=(
            "Comma-separated list of selected study activity IDs. "
            "Valid values: math, measurements, currency, english, "
            "motherTongue, customStories, speakingPractice, science"
        ),
        examples=["math,science"],
    ),
    city: Optional[str] = Query(
        default=None,
        description="City name derived from device geo, e.g. Singapore",
        examples=["Singapore"],
    ),
    platform: Optional[str] = Query(
        default="ios",
        description="Device platform: ios | android",
        examples=["ios"],
    ),
    app_version: Optional[str] = Query(
        default=None,
        description="App semver string, e.g. 2.1.0",
        examples=["2.1.0"],
    ),
) -> Dict[str, Any]:
    """Track a mobile user session. Called by the app on each study interaction."""

    # Parse and normalise activities
    activity_list = [a.strip() for a in activities.split(",") if a.strip()]
    if not activity_list:
        raise HTTPException(
            status_code=422,
            detail="'activities' must contain at least one activity ID.",
        )

    record = MobileAnalyticsRecord(
        unique_id=unique_id,
        age=age,
        country=country.upper(),
        city=city or "",
        language=language.lower(),
        activities=activity_list,
        platform=(platform or "ios").lower(),
        app_version=app_version or "",
        timestamp=_now_iso(),
    )

    store.append(record.model_dump())
    logger.info(
        "track: unique_id=%s age=%d country=%s lang=%s activities=%s",
        unique_id, age, country, language, activity_list,
    )

    return {
        "status": "ok",
        "tracked": record.model_dump(),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN  — read / reporting endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/traffic",
    response_model=TrafficSummary,
    summary="Admin — overall traffic summary",
)
async def get_traffic(
    start_date:  Optional[str] = Query(default=None, examples=["2026-01-01"]),
    end_date:    Optional[str] = Query(default=None, examples=["2026-12-31"]),
    unique_id:   Optional[str] = Query(default=None, description="Filter to one user"),
    country:     Optional[str] = Query(default=None, description="ISO-2 e.g. SG"),
    language:    Optional[str] = Query(default=None, description="BCP-47 e.g. en"),
) -> TrafficSummary:
    records = store.query(
        unique_id=unique_id,
        country=country,
        language=language,
        start_date=start_date,
        end_date=end_date,
    )

    unique_users = len({r.get("unique_id") for r in records})

    # Group by calendar day
    day_counts: Dict[str, int] = defaultdict(int)
    for r in records:
        day = r.get("timestamp", "")[:10]
        if day:
            day_counts[day] += 1
    events_by_day = sorted(
        [{"date": d, "count": c} for d, c in day_counts.items()],
        key=lambda x: x["date"],
    )

    return TrafficSummary(
        period_start=start_date or "all",
        period_end=end_date or _today(),
        total_sessions=len(records),
        unique_users=unique_users,
        events_by_day=events_by_day,
    )


@router.get(
    "/users/{unique_id}",
    response_model=UserActivitySummary,
    summary="Admin — analytics for a specific user",
)
async def get_user_analytics(
    unique_id:  str,
    start_date: Optional[str] = Query(default=None),
    end_date:   Optional[str] = Query(default=None),
) -> UserActivitySummary:
    records = store.query(
        unique_id=unique_id,
        start_date=start_date,
        end_date=end_date,
    )

    if not records:
        raise HTTPException(
            status_code=404,
            detail=f"No analytics found for unique_id={unique_id}",
        )

    languages  = sorted({r.get("language", "")  for r in records if r.get("language")})
    countries  = sorted({r.get("country",  "")  for r in records if r.get("country")})
    cities     = sorted({r.get("city",     "")  for r in records if r.get("city")})
    last_active = max((r.get("timestamp", "") for r in records), default="")

    # Collect all activities this user has ever used
    all_activities: Dict[str, int] = defaultdict(int)
    for r in records:
        for a in r.get("activities", []):
            all_activities[a.lower()] += 1
    activities_used = sorted(all_activities, key=lambda x: -all_activities[x])

    # Use the most-recent age value
    ages = [r.get("age") for r in records if r.get("age") is not None]
    age  = ages[-1] if ages else None

    return UserActivitySummary(
        unique_id=unique_id,
        total_sessions=len(records),
        age=age,
        languages_used=languages,
        countries=countries,
        cities=cities,
        activities_used=activities_used,
        last_active=last_active,
    )


@router.get(
    "/geo",
    summary="Admin — traffic breakdown by geo location",
)
async def get_geo_breakdown(
    start_date: Optional[str] = Query(default=None),
    end_date:   Optional[str] = Query(default=None),
    group_by:   str           = Query(default="country", description="country | city"),
    limit:      int           = Query(default=20, ge=1, le=100),
) -> Dict[str, Any]:
    records = store.query(start_date=start_date, end_date=end_date)
    field   = "city" if group_by == "city" else "country"
    raw     = store.aggregate_by(field, records=records, limit=limit)

    breakdown = [
        GeoBreakdownItem(
            country=item["key"] if field == "country" else "",
            city=item["key"]    if field == "city"    else "",
            session_count=item["count"],
            unique_users=item["unique_users"],
        )
        for item in raw
    ]

    return {
        "group_by":      group_by,
        "total_sessions": len(records),
        "period_start":  start_date or "all",
        "period_end":    end_date   or _today(),
        "breakdown":     [b.model_dump() for b in breakdown],
    }


@router.get(
    "/languages",
    summary="Admin — traffic breakdown by app language",
)
async def get_language_breakdown(
    start_date: Optional[str] = Query(default=None),
    end_date:   Optional[str] = Query(default=None),
    limit:      int           = Query(default=20, ge=1, le=100),
) -> Dict[str, Any]:
    records = store.query(start_date=start_date, end_date=end_date)
    raw     = store.aggregate_by("language", records=records, limit=limit)

    breakdown = [
        LanguageBreakdownItem(
            language=item["key"],
            session_count=item["count"],
            unique_users=item["unique_users"],
        )
        for item in raw
    ]

    return {
        "total_sessions": len(records),
        "period_start":   start_date or "all",
        "period_end":     end_date   or _today(),
        "breakdown":      [b.model_dump() for b in breakdown],
    }


@router.get(
    "/activities",
    summary="Admin — most-used activities across all users",
)
async def get_activity_breakdown(
    start_date: Optional[str] = Query(default=None),
    end_date:   Optional[str] = Query(default=None),
    unique_id:  Optional[str] = Query(default=None, description="Filter to one user"),
    country:    Optional[str] = Query(default=None),
    language:   Optional[str] = Query(default=None),
    limit:      int           = Query(default=20, ge=1, le=50),
) -> Dict[str, Any]:
    records = store.query(
        unique_id=unique_id,
        country=country,
        language=language,
        start_date=start_date,
        end_date=end_date,
    )
    raw = store.aggregate_activities(records=records, limit=limit)

    breakdown = [
        ActivityBreakdownItem(
            activity=item["key"],
            session_count=item["count"],
            unique_users=item["unique_users"],
        )
        for item in raw
    ]

    return {
        "total_sessions":  len(records),
        "period_start":    start_date or "all",
        "period_end":      end_date   or _today(),
        "filters_applied": {
            "unique_id": unique_id,
            "country":   country,
            "language":  language,
        },
        "breakdown": [b.model_dump() for b in breakdown],
    }


@router.get(
    "/age-groups",
    summary="Admin — session count broken down by age group",
)
async def get_age_group_breakdown(
    start_date: Optional[str] = Query(default=None),
    end_date:   Optional[str] = Query(default=None),
    country:    Optional[str] = Query(default=None),
    language:   Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    records = store.query(
        country=country,
        language=language,
        start_date=start_date,
        end_date=end_date,
    )
    raw = store.aggregate_age_groups(records=records)

    breakdown = [
        AgeGroupBreakdownItem(
            age_group=item["age_group"],
            min_age=item["min_age"],
            max_age=item["max_age"],
            session_count=item["count"],
            unique_users=item["unique_users"],
        )
        for item in raw
    ]

    return {
        "total_sessions": len(records),
        "period_start":   start_date or "all",
        "period_end":     end_date   or _today(),
        "breakdown":      [b.model_dump() for b in breakdown],
    }


@router.get(
    "/dashboard",
    response_model=DashboardStats,
    summary="Admin — all key metrics in one call",
)
async def get_dashboard(
    start_date: Optional[str] = Query(default=None),
    end_date:   Optional[str] = Query(default=None),
) -> DashboardStats:
    records      = store.query(start_date=start_date, end_date=end_date)
    unique_users = len({r.get("unique_id") for r in records})

    # Top countries
    raw_geo  = store.aggregate_by("country",  records=records, limit=5)
    raw_lang = store.aggregate_by("language", records=records, limit=5)
    raw_act  = store.aggregate_activities(records=records, limit=5)
    raw_age  = store.aggregate_age_groups(records=records)

    top_countries = [
        GeoBreakdownItem(
            country=i["key"], session_count=i["count"], unique_users=i["unique_users"]
        )
        for i in raw_geo
    ]
    top_languages = [
        LanguageBreakdownItem(
            language=i["key"], session_count=i["count"], unique_users=i["unique_users"]
        )
        for i in raw_lang
    ]
    top_activities = [
        ActivityBreakdownItem(
            activity=i["key"], session_count=i["count"], unique_users=i["unique_users"]
        )
        for i in raw_act
    ]
    age_groups = [
        AgeGroupBreakdownItem(
            age_group=i["age_group"],
            min_age=i["min_age"],
            max_age=i["max_age"],
            session_count=i["count"],
            unique_users=i["unique_users"],
        )
        for i in raw_age
    ]

    return DashboardStats(
        period_start=start_date or "all",
        period_end=end_date or _today(),
        total_sessions=len(records),
        unique_users=unique_users,
        top_countries=top_countries,
        top_languages=top_languages,
        top_activities=top_activities,
        age_group_breakdown=age_groups,
    )
