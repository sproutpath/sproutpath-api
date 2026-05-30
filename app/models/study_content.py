"""Pydantic models for the study content endpoint.

These mirror the ``study_content.json`` structure exactly so the iOS
app can drive its Study tab UI directly from the API response.

Hierarchy:
  StudyContentResponse
    └── AgeGroup          (toddler / early_learner / junior / preteen / teen)
          └── Category    (numbers_logic / language_stories / science_discovery)
                └── StudyItem  (math / measurements / english / science …)

The ``/getstudy/content`` endpoint returns the full catalogue or a
slice filtered to a single age group.  The ``/getstudy/videos``
endpoint maps a ``dest`` value to real video records from the feed.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class StudyItem(BaseModel):
    """A single tappable tile in the Study tab UI."""
    id: str
    titleKey: str
    emoji: str
    color: str
    dest: str               # navigation destination used by the app router

    model_config = ConfigDict(extra="ignore")


class Category(BaseModel):
    """A grouping of study items shown as a section header."""
    id: str
    heading: str
    emoji: str
    items: List[StudyItem]

    model_config = ConfigDict(extra="ignore")


class AgeGroup(BaseModel):
    """One age band — maps to a filter chip in the Study tab."""
    id: str
    label: str
    minAge: int
    maxAge: int
    categories: List[Category]

    model_config = ConfigDict(extra="ignore")


class StudyContentResponse(BaseModel):
    """Top-level response for GET /sproutpath/api/v1/getstudy/content."""
    version: str
    total_age_groups: int
    age_groups: List[AgeGroup]


class StudyItemVideoResponse(BaseModel):
    """Response for GET /sproutpath/api/v1/getstudy/videos — videos for a dest."""
    dest: str
    age_group_id: Optional[str]
    age_group_label: Optional[str]
    language: Optional[str]
    total_videos: int
    page: int
    page_size: int
    total_pages: int
    videos: List[dict]          # Video dicts (full enhanced model)
