"""Upstream loader for the videos feed.

Responsibilities:

* Read the source JSON from either a local file (``data_path``) or a
  remote URL (``data_url``), with URL taking precedence when set.
* Flatten the nested ``by_language → category → [videos]`` shape into a
  single list of records. The downstream filtering code wants a flat
  list so it can apply predicates uniformly; the per-language buckets
  in the upstream feed are just an organisational convenience.
* Cache the parsed payload in memory after the first load so the file
  isn't re-read on every request. The cache is process-local, which is
  fine for a single uvicorn worker; behind multiple workers each gets
  its own copy (memory cost is small — the whole feed is ~half a meg).
* Thread-safe initialisation via ``asyncio.Lock`` so concurrent first
  requests don't all try to fetch at the same time.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


# ─── Module-level cache ────────────────────────────────────────────────
# ``_cache`` holds the parsed-and-flattened payload after first load. We
# never invalidate it during a process lifetime; restart the app to pick
# up new data. (Adding a TTL or admin-triggered refresh is a future
# concern — not part of the initial spec.)
_cache: Optional[Dict[str, Any]] = None
_cache_lock = asyncio.Lock()


async def load_payload() -> Dict[str, Any]:
    """Return the parsed feed.

    Result shape:

        {
            "version": int,
            "generated": str,
            "description": str,
            "categories_included": list[str],   # may be absent
            "videos": list[dict],               # flattened from by_language
        }

    The flattened ``videos`` list is what downstream filtering operates
    on; ``by_language`` is dropped after flattening.
    """
    global _cache
    if _cache is not None:
        return _cache

    async with _cache_lock:
        # Re-check after acquiring the lock — another coroutine may have
        # populated the cache while we were waiting.
        if _cache is not None:
            return _cache

        raw = await _read_raw()
        _cache = _flatten(raw)
        logger.info(
            "Loaded video feed: version=%s, %d videos",
            _cache.get("version"),
            len(_cache.get("videos", [])),
        )
        return _cache


# ─── Internals ─────────────────────────────────────────────────────────


async def _read_raw() -> Dict[str, Any]:
    """Read the source JSON from URL or local file.

    URL wins over path when both are configured — this is the natural
    deployment pattern: the bundled file is for development/testing, and
    production points at the live URL.
    """
    if settings.data_url:
        logger.info("Fetching videos feed from %s", settings.data_url)
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            resp = await client.get(settings.data_url)
            resp.raise_for_status()
            return resp.json()

    path = Path(settings.data_path)
    if not path.is_file():
        raise FileNotFoundError(
            f"Video feed not found at {path}. Either bundle a file there "
            f"or set SPROUTPATH_DATA_URL."
        )
    logger.info("Reading videos feed from %s", path)
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _flatten(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Collapse ``by_language → category → [videos]`` into a flat list.

    Each yielded video record carries an explicit ``language`` field
    upstream, so we don't need to re-stamp it during flattening — just
    trust what's there. Top-level metadata (``version``, ``generated``,
    ``description``) is preserved.
    """
    flat_videos: List[Dict[str, Any]] = []
    by_language = raw.get("by_language", {})
    if not isinstance(by_language, dict):
        raise ValueError("Expected 'by_language' to be an object")

    for _language, categories in by_language.items():
        if not isinstance(categories, dict):
            continue
        for _category, vids in categories.items():
            if not isinstance(vids, list):
                continue
            flat_videos.extend(vids)

    return {
        "version": raw.get("version", 0),
        "generated": raw.get("generated", ""),
        "description": raw.get("description", ""),
        "categories_included": raw.get("categories_included", []),
        "videos": flat_videos,
    }


def reset_cache_for_tests() -> None:
    """Clear the in-process cache. Tests use this; production never calls it."""
    global _cache
    _cache = None
