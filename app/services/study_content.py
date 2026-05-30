"""Study content loader and dest→video mapper.

Responsibilities
----------------
1. Load and cache ``json/study_content.json`` (age-group / category / item
   hierarchy used to render the Study tab UI).
2. Map a ``dest`` value (e.g. ``"math"``, ``"science"``, ``"motherTongue"``)
   to actual video records from the upstream feed, applying age and language
   filters along the way.
3. Resolve which ``AgeGroup`` a given age integer falls into.

Dest → video category mapping
------------------------------
The study_content items use navigation ``dest`` keys that don't always
match video ``category`` names 1-to-1.  This module owns the mapping so
neither the API nor the model layer has to.

  dest              → video categories (case-insensitive OR match)
  ──────────────────────────────────────────────────────────────────
  math              → Math
  measurements      → Measurements
  currency          → (no direct video category — returns empty list)
  english           → Activities, Art & Crafts, Autism Support,
                       Communication, Cooking, Experiments, Music & Relax,
                       Science, Social Skills, Sports, Yoga & Exercise,
                       Drawing*, Painting*, Swimming*, Athletics, Badminton,
                       Basketball, Cricket, Ice Skating, Kabaddi,
                       Martial Arts, Roller Skating, Skating*, Table Tennis
  motherTongue      → *Stories  (Bengali Stories, Hindi Stories, …)
  customStories     → *Stories  (same as motherTongue — custom subset)
  speakingPractice  → Communication
  science           → Science, Experiments
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import PROJECT_ROOT
from app.services.filtering import filter_videos
from app.services.upstream import load_payload

logger = logging.getLogger(__name__)

_CONTENT_PATH = PROJECT_ROOT / "json" / "study_content.json"

# ── In-process cache ──────────────────────────────────────────────────────────
_content_cache: Optional[Dict[str, Any]] = None


def load_study_content() -> Dict[str, Any]:
    """Return parsed study_content.json (cached after first read)."""
    global _content_cache
    if _content_cache is not None:
        return _content_cache
    if not _CONTENT_PATH.is_file():
        raise FileNotFoundError(f"study_content.json not found at {_CONTENT_PATH}")
    with _CONTENT_PATH.open("r", encoding="utf-8") as f:
        _content_cache = json.load(f)
    logger.info("Loaded study_content.json: %d age groups", len(_content_cache.get("ageGroups", [])))
    return _content_cache


def reset_cache_for_tests() -> None:
    global _content_cache
    _content_cache = None


# ── Age group resolution ──────────────────────────────────────────────────────

def resolve_age_group(age: int, content: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the AgeGroup dict whose [minAge, maxAge] bracket contains ``age``."""
    for ag in content.get("ageGroups", []):
        if ag["minAge"] <= age <= ag["maxAge"]:
            return ag
    return None


def resolve_age_group_by_id(age_group_id: str, content: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the AgeGroup dict matching the given id string."""
    for ag in content.get("ageGroups", []):
        if ag["id"] == age_group_id:
            return ag
    return None


# ── Dest → video category mapping ────────────────────────────────────────────

# Language dest values that should be matched by video.language instead
# of video.category.
_LANGUAGE_DESTS = {"motherTongue", "customStories"}

# Explicit category lists for each dest.  All comparisons are lowercase.
_DEST_TO_CATEGORIES: Dict[str, List[str]] = {
    "math": ["math"],
    "measurements": ["measurements"],
    "currency": [],          # no video category exists yet
    "english": [
        "activities",
        "art & crafts",
        "autism support",
        "communication",
        "cooking",
        "experiments",
        "music & relax",
        "science",
        "social skills",
        "sports",
        "yoga & exercise",
        "athletics",
        "badminton",
        "basketball",
        "cricket",
        "drawing — drawing faces & expressions",
        "drawing — drawing with shapes",
        "ice skating",
        "kabaddi",
        "martial arts",
        "painting — finger painting",
        "painting — watercolour basics",
        "roller skating",
        "skating — first steps on skates",
        "skating — roller skating basics",
        "swimming",
        "swimming — learning freestyle",
        "swimming — water safety first",
        "table tennis",
    ],
    "speakingPractice": ["communication"],
    "science": ["science", "experiments"],
}

# Language names that count as "mother tongue" (non-English) in the video feed
_MOTHER_TONGUE_LANGUAGES = {
    "telugu", "hindi", "tamil", "kannada", "malayalam",
    "marathi", "bengali", "gujarati", "punjabi", "odia", "assamese",
}


def _filter_by_dest(
    videos: List[Dict[str, Any]],
    dest: str,
    language_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Filter the flat video list to records that match ``dest``.

    Special cases
    -------------
    * motherTongue / customStories: match non-English story videos.
      If ``language_filter`` is supplied, further narrow to that language.
    * english: match all English-language videos across many categories.
    * All others: match by the category list in ``_DEST_TO_CATEGORIES``.
    """
    dest_lower = dest.strip()

    # ── Mother tongue / custom stories ────────────────────────────────
    if dest_lower in _LANGUAGE_DESTS:
        results = []
        for v in videos:
            lang = v.get("language", "").lower()
            cat  = v.get("category", "").lower()
            # Only non-English story/devotional content
            if lang not in _MOTHER_TONGUE_LANGUAGES:
                continue
            if "stories" not in cat and "devotional" not in cat:
                continue
            if language_filter:
                # Normalise: accept ISO code or full name
                from app.services.filtering import _LANG_CODE_TO_NAME
                wanted = _LANG_CODE_TO_NAME.get(language_filter.lower(), language_filter.lower())
                if lang != wanted:
                    continue
            results.append(v)
        return results

    # ── English-language content ───────────────────────────────────────
    if dest_lower == "english":
        allowed_cats = set(_DEST_TO_CATEGORIES.get("english", []))
        return [
            v for v in videos
            if v.get("language", "").lower() == "english"
            and v.get("category", "").lower() in allowed_cats
        ]

    # ── Direct category match ─────────────────────────────────────────
    allowed = set(_DEST_TO_CATEGORIES.get(dest_lower, []))
    if not allowed:
        return []
    filtered = [v for v in videos if v.get("category", "").lower() in allowed]
    # Optionally narrow by language
    if language_filter:
        from app.services.filtering import _LANG_CODE_TO_NAME
        wanted_lang = _LANG_CODE_TO_NAME.get(language_filter.lower(), language_filter.lower())
        filtered = [v for v in filtered if (v.get("language") or "").lower() == wanted_lang]
    return filtered


# ── Public API ────────────────────────────────────────────────────────────────

async def get_videos_for_dest(
    dest: str,
    age: Optional[int] = None,
    age_group_id: Optional[str] = None,
    language: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    """
    Return paginated video records for a study ``dest`` value,
    optionally filtered by age / age_group and language.

    Returns a dict ready to be validated into ``StudyItemVideoResponse``.
    """
    payload  = await load_payload()
    content  = load_study_content()

    # Determine the age band to use for the age filter
    resolved_ag: Optional[Dict[str, Any]] = None
    if age_group_id:
        resolved_ag = resolve_age_group_by_id(age_group_id, content)
    if resolved_ag is None and age is not None:
        resolved_ag = resolve_age_group(age, content)

    # Age filter strategy:
    # - If an explicit age was passed by the caller, use it directly.
    # - If only an age_group_id was given (no numeric age), skip the age
    #   filter entirely.  The study_content.json already defines which
    #   dest values appear per age group, so we trust the content spec
    #   rather than double-filtering — this avoids misses when a video's
    #   age_range doesn't perfectly align with a group's minAge.
    filter_age = age  # may be None — that's OK, filter_videos skips it

    # Start from the full flattened feed
    all_videos = payload["videos"]

    # Apply language + age filter from the existing service
    lang_list = [language] if language else []
    age_filtered = filter_videos(all_videos, languages=lang_list, age=filter_age)

    # Apply dest mapping
    dest_filtered = _filter_by_dest(age_filtered, dest, language_filter=language)

    # Paginate
    total = len(dest_filtered)
    total_pages = max(1, math.ceil(total / page_size))
    start  = (page - 1) * page_size
    items  = dest_filtered[start : start + page_size]

    # Enrich with YouTube URLs (same logic as study_videos.json generation)
    WATCH = "https://www.youtube.com/watch?v={id}"
    EMBED = "https://www.youtube.com/embed/{id}"
    THUMB = "https://img.youtube.com/vi/{id}/hqdefault.jpg"

    enriched = []
    for v in items:
        vid_id = v.get("id", "")
        enriched.append({
            **v,
            "watch_url":    WATCH.format(id=vid_id),
            "embed_url":    EMBED.format(id=vid_id),
            "thumbnail_url": THUMB.format(id=vid_id),
        })

    return {
        "dest": dest,
        "age_group_id":    resolved_ag["id"]    if resolved_ag else None,
        "age_group_label": resolved_ag["label"] if resolved_ag else None,
        "language": language,
        "total_videos": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "videos": enriched,
    }
