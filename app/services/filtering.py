"""Filtering for the videos endpoint.

Two filters, both optional and independent:

* ``languages`` — keep videos whose ``language`` field matches one of the
  requested names. Accepts both full names ("telugu") and ISO codes
  ("te"), normalised to lowercase. Unknown values are silently ignored
  rather than rejected — clients shouldn't have a list of valid values
  fail the whole request.

* ``age`` — keep videos whose ``age_range`` covers the requested age.
  Upstream format is ``"low-high"`` (e.g. ``"3-12"``). Empty
  ``age_range`` means "no age restriction" and is always kept; this
  matches the upstream convention where general-audience content lacks
  an explicit range.

Filters compose with AND — both must pass for a video to be kept.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Set

logger = logging.getLogger(__name__)


# ─── Language normalisation ────────────────────────────────────────────
# Map ISO 639-1 codes (and a couple of common variants) to the canonical
# lowercase language names that appear in the upstream ``language``
# field. Anything not in this map is assumed to already be a canonical
# name and is just lowercased.

_LANG_CODE_TO_NAME: Dict[str, str] = {
    "en": "english",
    "hi": "hindi",
    "te": "telugu",
    "ta": "tamil",
    "kn": "kannada",
    "ml": "malayalam",
    "mr": "marathi",
    "bn": "bengali",
    "gu": "gujarati",
    "pa": "punjabi",
    # The upstream feed doesn't currently ship Odia/Assamese videos, but
    # the iOS app exposes those languages — keep the codes mapped so
    # future feeds work without a code change.
    "or": "odia",
    "as": "assamese",
}


def _normalize_languages(raw: Iterable[str]) -> Set[str]:
    """Lowercase, trim, and resolve ISO codes to language names.

    Returns a set so duplicate inputs collapse. Empty/whitespace entries
    are dropped; this lets the endpoint accept comma-separated query
    values like ``"te, , hi"`` without choking.
    """
    out: Set[str] = set()
    for item in raw:
        token = item.strip().lower()
        if not token:
            continue
        out.add(_LANG_CODE_TO_NAME.get(token, token))
    return out


# ─── Age range parsing ─────────────────────────────────────────────────
# Upstream uses dash-separated ranges: "3-12", "5-10", etc. We accept
# both ASCII hyphen and en/em dashes to be defensive against feed
# formatting drift.

_AGE_RANGE_RE = re.compile(r"^\s*(\d+)\s*[-–—]\s*(\d+)\s*$")


def _parse_age_range(value: str) -> Optional[tuple[int, int]]:
    """Parse ``"3-12"`` → ``(3, 12)``. Returns ``None`` on no match.

    Callers treat ``None`` as "no usable range" — which we choose to
    interpret as "matches every age" downstream, so videos with empty
    or malformed ranges aren't quietly dropped.
    """
    m = _AGE_RANGE_RE.match(value or "")
    if not m:
        return None
    low, high = int(m.group(1)), int(m.group(2))
    if low > high:  # defensive — upstream could in theory invert
        low, high = high, low
    return low, high


def _matches_age(age_range: str, age: int) -> bool:
    """Return True if ``age`` falls inside ``age_range``.

    Empty or unparseable ranges match everything. This is the safe
    default: it means "we don't know" rather than "exclude" — the
    alternative would silently hide most of the catalogue, because
    plenty of upstream records have ``age_range == ""``.
    """
    parsed = _parse_age_range(age_range)
    if parsed is None:
        return True
    low, high = parsed
    return low <= age <= high


# ─── Public entry point ────────────────────────────────────────────────


def filter_videos(
    videos: List[Dict[str, Any]],
    languages: Optional[List[str]] = None,
    age: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Apply the language and age filters to a flat video list.

    Both filters are optional. Passing ``None`` (or an empty list, for
    languages) skips the corresponding predicate. The order of input is
    preserved in the output.
    """
    wanted_languages = _normalize_languages(languages or [])
    out: List[Dict[str, Any]] = []

    for v in videos:
        # Language filter
        if wanted_languages:
            lang = str(v.get("language", "")).strip().lower()
            if lang not in wanted_languages:
                continue

        # Age filter
        if age is not None:
            if not _matches_age(str(v.get("age_range", "")), age):
                continue

        out.append(v)

    logger.debug(
        "filter_videos: in=%d out=%d languages=%s age=%s",
        len(videos),
        len(out),
        sorted(wanted_languages) if wanted_languages else None,
        age,
    )
    return out
