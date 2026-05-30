"""Pydantic schemas for the videos endpoint.

Enhanced model adds YouTube-specific fields:
  - thumbnail_url     : standard YouTube thumbnail
  - embed_url         : iframe-embeddable URL
  - watch_url         : direct watch link
  - channel_url       : channel page link
  - is_featured       : editorial flag
  - content_type      : video | playlist | short
  - learning_objectives: free-text list for study context

The ``VideosResponse`` envelope is unchanged so existing iOS clients
continue to work without modification.  The new fields are additive —
they are included in the response but will simply be ignored by older
clients.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

YOUTUBE_WATCH_BASE = "https://www.youtube.com/watch?v="
YOUTUBE_EMBED_BASE = "https://www.youtube.com/embed/"
YOUTUBE_THUMBNAIL_BASE = "https://img.youtube.com/vi/{id}/hqdefault.jpg"
YOUTUBE_CHANNEL_BASE = "https://www.youtube.com/channel/"


class Video(BaseModel):
    """A single video record returned to the client.

    Core fields mirror the upstream JSON exactly.  New computed fields
    (``thumbnail_url``, ``embed_url``, ``watch_url``) are derived from
    the ``id`` field so the upstream feed doesn't need to carry them —
    we synthesise them at serialisation time.
    """

    id: str
    title: str
    channel: str
    category: str
    duration: str
    duration_seconds: int = 0
    description: str = ""
    tags: List[str] = Field(default_factory=list)
    age_range: str = ""
    language: str | None = None

    # ── Enhanced YouTube fields (new) ────────────────────────────────
    # Optional upstream fields — absent in legacy records, present when
    # the feed is enriched by the YouTube MCP pipeline.
    channel_id: str | None = None
    published_at: str | None = None          # ISO-8601 date string
    view_count: int | None = None
    like_count: int | None = None
    caption_available: bool = False
    transcript_available: bool = False
    is_featured: bool = False
    content_type: str = "video"              # video | short | playlist
    learning_objectives: List[str] = Field(default_factory=list)
    difficulty: str | None = None            # beginner | intermediate | advanced
    curriculum_tags: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")

    @field_validator("duration_seconds", mode="before")
    @classmethod
    def _coerce_null_duration(cls, v: object) -> int:
        if v is None:
            return 0
        try:
            return int(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0

    # ── Computed / derived fields ────────────────────────────────────
    @computed_field  # type: ignore[misc]
    @property
    def watch_url(self) -> str:
        return f"{YOUTUBE_WATCH_BASE}{self.id}"

    @computed_field  # type: ignore[misc]
    @property
    def embed_url(self) -> str:
        return f"{YOUTUBE_EMBED_BASE}{self.id}"

    @computed_field  # type: ignore[misc]
    @property
    def thumbnail_url(self) -> str:
        return YOUTUBE_THUMBNAIL_BASE.format(id=self.id)

    @computed_field  # type: ignore[misc]
    @property
    def channel_url(self) -> Optional[str]:
        if self.channel_id:
            return f"{YOUTUBE_CHANNEL_BASE}{self.channel_id}"
        return None


class VideosResponse(BaseModel):
    """Top-level response envelope.

    Shape matches the spec the iOS client already consumes — no breaking
    changes.  ``total_videos`` reflects the *filtered* count.
    """

    version: int
    generated: str
    description: str
    total_videos: int
    videos: List[Video]


# ── Study-specific response models ──────────────────────────────────────

class StudyVideosResponse(BaseModel):
    """Response envelope for the /getstudy endpoint.

    Extends the base envelope with filter echo and pagination metadata
    so clients know exactly what was applied.
    """

    version: int
    generated: str
    description: str
    total_videos: int
    filters_applied: dict = Field(default_factory=dict)
    page: int = 1
    page_size: int = 50
    total_pages: int = 1
    videos: List[Video]
