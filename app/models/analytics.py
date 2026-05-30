"""Pydantic models for the analytics endpoints.

Mobile track request
--------------------
The mobile app sends a single GET with flat query params:
  unique_id          — user's registration ID
  age                — child's age (integer)
  country            — ISO-3166-1 alpha-2  (e.g. SG)
  city               — city name
  language           — BCP-47 app language (e.g. en, hi, te)
  activities         — comma-separated activity IDs  (e.g. math,science)
  platform           — ios | android
  app_version        — semver string

Admin dashboard response models
--------------------------------
TrafficSummary, GeoBreakdownItem, LanguageBreakdownItem,
ActivityBreakdownItem, AgeGroupBreakdownItem, UserActivitySummary,
DashboardStats
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ── Stored event (internal) ───────────────────────────────────────────────────

class MobileAnalyticsRecord(BaseModel):
    """Normalised record written to the store after a mobile track call."""
    unique_id: str                          # user's registration / unique ID
    age: Optional[int] = None              # child's age
    country: str = ""                      # ISO-2 e.g. SG
    city: str = ""
    language: str = ""                     # BCP-47 e.g. en
    activities: List[str] = Field(default_factory=list)  # ["math","science"]
    platform: str = "ios"                  # ios | android
    app_version: str = ""
    timestamp: str                         # ISO-8601 UTC


# ── Admin response models ─────────────────────────────────────────────────────

class TrafficSummary(BaseModel):
    period_start: str
    period_end: str
    total_sessions: int
    unique_users: int
    events_by_day: List[Dict[str, Any]] = Field(default_factory=list)


class GeoBreakdownItem(BaseModel):
    country: str
    city: str = ""
    session_count: int
    unique_users: int


class LanguageBreakdownItem(BaseModel):
    language: str
    session_count: int
    unique_users: int


class ActivityBreakdownItem(BaseModel):
    activity: str
    session_count: int
    unique_users: int


class AgeGroupBreakdownItem(BaseModel):
    age_group: str          # e.g. "toddler (2-4)"
    min_age: int
    max_age: int
    session_count: int
    unique_users: int


class UserActivitySummary(BaseModel):
    unique_id: str
    total_sessions: int
    age: Optional[int]
    languages_used: List[str]
    countries: List[str]
    cities: List[str]
    activities_used: List[str]
    last_active: str


class DashboardStats(BaseModel):
    period_start: str
    period_end: str
    total_sessions: int
    unique_users: int
    top_countries: List[GeoBreakdownItem]
    top_languages: List[LanguageBreakdownItem]
    top_activities: List[ActivityBreakdownItem]
    age_group_breakdown: List[AgeGroupBreakdownItem]
