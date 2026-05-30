"""
fetch_videos.py
---------------
Fetches YouTube videos across 49 categories (target: 10 per category, ~490
total) using the YouTube MCP Server
(https://github.com/temiedani/youtube-mcp-server) over stdio.

Every video record carries the full schema:

  {
    "id":          "aBcDeFgHiJk",          # YouTube video id
    "title":       "...",
    "channel":     "...",
    "category":    "Swimming",             # category bucket
    "language":    "english",              # english / telugu / hindi / ...
    "description": "...",                  # safety-filtered, trimmed
    "tags":        ["athletics", "..."],   # from controlled vocabulary
    "age_range":   "7-10"                  # ALWAYS populated
  }

Behavior
--------
  1. Spawns the MCP server as a subprocess.
  2. Initializes an MCP session via the official `mcp` Python SDK.
  3. For each (category, search-query) pair, calls `get_videos`, applies a
     safety blocklist, validates language for Indian-language categories
     (script or romanized keyword must be present), and stamps each record
     with `language` and a category-default `age_range`.
  4. Optional Claude enrichment (--enrich) refines description, tags, and
     age_range using your controlled vocabulary. Or use --describe to ONLY
     generate kid-friendly descriptions (no tag/age changes).
  5. Optional filters:
        --languages all                       (default; include everything)
        --languages telugu,hindi              (restrict to these languages)
        --tags yoga,music
  6. Optional grouping: --grouped emits {"by_language": {lang: {cat: [...]}}}.
  7. Deduplicates by video ID and writes everything to `videos.json`.

Setup
-----
1. Clone & install the MCP server:
       git clone https://github.com/temiedani/youtube-mcp-server.git
       cd youtube-mcp-server
       uv pip install -e .

2. Create a `.env` in that folder with:
       YOUTUBE_API_KEY=<your_api_key>

3. Install the client-side dep here:
       pip install "mcp[cli]" python-dotenv anthropic

4. Point this script at the server entrypoint and run:
       export YT_MCP_SERVER_PATH=/abs/path/to/youtube-mcp-server/mcp_videos.py
       python fetch_videos.py                                # default (all langs)
       python fetch_videos.py --describe                     # fill blank descriptions only
       python fetch_videos.py --rewrite-descriptions         # rewrite ALL descriptions
       python fetch_videos.py --enrich --grouped             # full enrichment + grouped
       python fetch_videos.py --enrich --grouped --languages all
       python fetch_videos.py --languages telugu,hindi       # restrict
       python fetch_videos.py --tags yoga,music              # restrict by tag
       python fetch_videos.py --debug                        # verbose diagnostics
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


# --------------------------------------------------------------------------- #
# 1. CATEGORY -> SEARCH QUERIES                                               #
# --------------------------------------------------------------------------- #
# Each query is phrased to bias YouTube toward kid-safe / educational results.
# Numbers are tuned so the deduped total lands near 200 videos.
# --------------------------------------------------------------------------- #

CATEGORY_QUERIES: dict[str, list[tuple[str, int]]] = {
    # Each category sums to exactly 10 target videos.
    "Autism Support": [
        ("autism social stories for kids", 4),
        ("speech therapy autism children", 3),
        ("autism awareness kids", 3),
    ],
    "Yoga & Exercise": [
        ("Cosmic Kids Yoga", 4),
        ("kids exercise GoNoodle", 3),
        ("yoga for children beginners", 3),
    ],
    "Social Skills": [
        ("Sesame Street social skills", 4),
        ("Daniel Tiger feelings kids", 3),
        ("social skills for kids friendship", 3),
    ],
    "Communication": [
        ("speech and language for kids", 4),
        ("Ms Rachel toddler learning", 3),
        ("communication skills for children", 3),
    ],
    "Cooking": [
        ("easy cooking recipes for kids", 4),
        ("kids cooking show", 3),
        ("simple recipes kids can make", 3),
    ],
    "Sports": [
        ("kids sports highlights", 4),
        ("introduction to sports for kids", 3),
        ("sports skills for beginners kids", 3),
    ],
    "Math": [
        ("Khan Academy Kids math", 4),
        ("Numberblocks counting", 3),
        ("Math Antics for kids", 3),
    ],
    "Science": [
        ("SciShow Kids science", 4),
        ("National Geographic Kids science", 3),
        ("science for kids easy", 3),
    ],
    "Experiments": [
        ("easy science experiments for kids", 4),
        ("kitchen science experiments kids", 3),
        ("DIY science experiments children", 3),
    ],
    "Music & Relax": [
        ("lullaby for babies", 4),
        ("kids relaxing music", 3),
        ("calm music for children", 3),
    ],
    "Art & Crafts": [
        ("Art for Kids Hub drawing", 4),
        ("easy crafts for kids", 3),
        ("art project for children", 3),
    ],
    "Activities": [
        ("Blippi learning activities", 4),
        ("sensory activities for toddlers", 3),
        ("indoor activities for kids", 3),
    ],
    "Measurements": [
        ("measurement for kids", 4),
        ("learn measurement length weight kids", 3),
        ("units of measurement children", 3),
    ],

    # ---------- Indian language stories & devotional ----------
    "Telugu Stories": [
        ("Telugu stories for kids", 4),
        ("Telugu moral stories children", 3),
        ("తెలుగు కథలు పిల్లల", 3),
    ],
    "Telugu Devotional": [
        ("Telugu bhakti songs for kids", 4),
        ("Telugu devotional stories children", 3),
        ("Telugu bhajan children", 3),
    ],
    "Hindi Stories": [
        ("Hindi stories for kids ChuChu TV", 4),
        ("Hindi moral stories children", 3),
        ("Hindi kahani for kids", 3),
    ],
    "Hindi Devotional": [
        ("Hindi bhajan for kids", 4),
        ("Krishna stories Hindi for children", 3),
        ("Ram katha Hindi for kids", 3),
    ],
    "Tamil Stories": [
        ("Tamil stories for kids", 4),
        ("Tamil moral stories children", 3),
        ("Tamil kathaigal for kids", 3),
    ],
    "Tamil Devotional": [
        ("Tamil bhakti songs kids", 4),
        ("Tamil devotional stories children", 3),
        ("Tamil bhajan for kids", 3),
    ],
    "Kannada Stories": [
        ("Kannada stories for kids", 4),
        ("Kannada moral stories children", 3),
        ("Kannada kathegalu kids", 3),
    ],
    "Kannada Devotional": [
        ("Kannada bhakti songs kids", 4),
        ("Kannada devotional stories children", 3),
        ("Kannada bhajan for kids", 3),
    ],
    "Malayalam Stories": [
        ("Malayalam stories for kids", 4),
        ("Malayalam moral stories children", 3),
        ("Malayalam kadhakal kids", 3),
    ],
    "Malayalam Devotional": [
        ("Malayalam devotional songs kids", 4),
        ("Malayalam bhakti songs children", 3),
        ("Malayalam bhajan for kids", 3),
    ],
    "Marathi Stories": [
        ("Marathi goshti for kids", 4),
        ("Marathi stories children", 3),
        ("Marathi balgeet stories", 3),
    ],
    "Marathi Devotional": [
        ("Marathi bhajan for kids", 4),
        ("Marathi bhakti geet children", 3),
        ("Marathi devotional songs kids", 3),
    ],
    "Bengali Stories": [
        ("Bengali stories for kids", 4),
        ("Bengali rupkothar golpo children", 3),
        ("Bengali golpo for kids", 3),
    ],
    "Bengali Devotional": [
        ("Bengali devotional songs kids", 4),
        ("Bengali bhajan for children", 3),
        ("Bengali kirtan for kids", 3),
    ],
    "Gujarati Stories": [
        ("Gujarati stories for kids", 4),
        ("Gujarati varta children", 3),
        ("Gujarati balvarta kids", 3),
    ],
    "Gujarati Devotional": [
        ("Gujarati bhajan for kids", 4),
        ("Gujarati devotional songs children", 3),
        ("Gujarati bhakti geet kids", 3),
    ],
    "Punjabi Stories": [
        ("Punjabi stories for kids", 4),
        ("Punjabi kahani children", 3),
        ("Punjabi sakhi for kids", 3),
    ],
    "Punjabi Devotional": [
        ("Punjabi shabad for kids", 4),
        ("Punjabi gurbani children", 3),
        ("Punjabi bhajan for kids", 3),
    ],

    # ---------- Sports sub-categories ----------
    "Cricket": [
        ("cricket basics for kids", 4),
        ("how to play cricket for beginners", 3),
        ("cricket coaching kids tutorial", 3),
    ],
    "Badminton": [
        ("badminton basics for kids", 4),
        ("badminton tutorial beginners", 3),
        ("badminton coaching for children", 3),
    ],
    "Swimming": [
        ("swimming lessons for kids", 4),
        ("learn swimming beginner", 3),
        ("swimming techniques for children", 3),
    ],
    "Athletics": [
        ("athletics for kids tutorial", 4),
        ("running form for kids", 3),
        ("track and field beginners children", 3),
    ],
    "Basketball": [
        ("basketball drills for kids", 4),
        ("basketball fundamentals for beginners", 3),
        ("basketball coaching kids", 3),
    ],
    "Table Tennis": [
        ("table tennis basics for kids", 4),
        ("table tennis tutorial beginner", 3),
        ("table tennis coaching children", 3),
    ],
    "Kabaddi": [
        ("kabaddi rules for kids", 4),
        ("kabaddi basics beginners", 3),
        ("kabaddi tutorial children", 3),
    ],
    "Ice Skating": [
        ("ice skating for beginners kids", 4),
        ("ice skating tutorial children", 3),
        ("learn ice skating kids", 3),
    ],
    "Roller Skating": [
        ("roller skating for kids tutorial", 4),
        ("roller skating beginners children", 3),
        ("learn roller skating kids", 3),
    ],
    "Martial Arts": [
        ("karate for kids beginner", 4),
        ("taekwondo for kids basics", 3),
        ("martial arts kids tutorial", 3),
    ],

    # ---------- Activity lessons ----------
    "Skating — First Steps on Skates": [
        ("first time ice skating kids tutorial", 4),
        ("ice skating first lesson children", 3),
        ("beginner ice skating kids", 3),
    ],
    "Skating — Roller Skating Basics": [
        ("roller skating basics for beginners", 4),
        ("roller skating first steps kids", 3),
        ("beginner roller skating children", 3),
    ],
    "Painting — Watercolour Basics": [
        ("watercolor painting for kids beginners", 4),
        ("watercolor basics children", 3),
        ("easy watercolor for kids", 3),
    ],
    "Painting — Finger Painting": [
        ("finger painting for toddlers", 4),
        ("finger painting ideas kids", 3),
        ("finger painting activity children", 3),
    ],
    "Drawing — Drawing with Shapes": [
        ("draw with shapes for kids", 4),
        ("drawing using shapes children", 3),
        ("shape drawing tutorial kids", 3),
    ],
    "Drawing — Drawing Faces & Expressions": [
        ("how to draw faces kids easy", 4),
        ("drawing facial expressions children", 3),
        ("how to draw cartoon faces kids", 3),
    ],
    "Swimming — Water Safety First": [
        ("water safety for kids swimming", 4),
        ("pool safety rules children", 3),
        ("swim safety basics kids", 3),
    ],
    "Swimming — Learning Freestyle": [
        ("freestyle swimming for kids", 4),
        ("freestyle stroke beginners children", 3),
        ("learn freestyle swimming kids", 3),
    ],
}


# --------------------------------------------------------------------------- #
# 2. SAFETY BLOCKLIST                                                         #
# --------------------------------------------------------------------------- #
# Title/description containing any of these terms (case-insensitive) is
# dropped. This is the only content filter applied.
# --------------------------------------------------------------------------- #

BLOCK_TERMS: list[str] = [
    # violence / weapons
    "kill", "murder", "shoot", "gun", "weapon", "blood", "gore",
    "violence", "violent", "war crime",
    # adult / romance
    "sexy", "sexual", "porn", "nude", "naked", "erotic", "adult only",
    "18+", "nsfw",
    # substances
    "alcohol", "drunk", "smoking", "tobacco", "weed", "cocaine",
    # horror
    "horror", "nightmare", "creepy", "haunted",
    # profanity
    "f**k", "s**t",
]


# --------------------------------------------------------------------------- #
# 3. CATEGORY -> DEFAULT TAGS                                                 #
# --------------------------------------------------------------------------- #

CATEGORY_TAGS: dict[str, list[str]] = {
    "Autism Support": ["socialSkills", "communication", "calm"],
    "Yoga & Exercise": ["yoga", "calm", "movement", "bodyAwareness"],
    "Social Skills": ["socialSkills", "communication", "happy"],
    "Communication": ["communication", "reading"],
    "Cooking": ["cooking", "taste", "smell"],
    "Sports": ["athletics"],
    "Math": ["reading"],
    "Science": ["surprised", "excited"],
    "Experiments": ["surprised", "excited", "sight"],
    "Music & Relax": ["music", "singing", "calm", "sound"],
    "Art & Crafts": ["drawing", "sight", "touch"],
    "Activities": ["movement", "happy"],
    "Measurements": ["reading"],

    "Telugu Stories":      ["telugu", "stories", "reading"],
    "Telugu Devotional":   ["telugu", "devotional", "calm"],
    "Hindi Stories":       ["hindi", "stories", "reading"],
    "Hindi Devotional":    ["hindi", "devotional", "calm"],
    "Tamil Stories":       ["tamil", "stories", "reading"],
    "Tamil Devotional":    ["tamil", "devotional", "calm"],
    "Kannada Stories":     ["kannada", "stories", "reading"],
    "Kannada Devotional":  ["kannada", "devotional", "calm"],
    "Malayalam Stories":   ["malayalam", "stories", "reading"],
    "Malayalam Devotional":["malayalam", "devotional", "calm"],
    "Marathi Stories":     ["marathi", "stories", "reading"],
    "Marathi Devotional":  ["marathi", "devotional", "calm"],
    "Bengali Stories":     ["bengali", "stories", "reading"],
    "Bengali Devotional":  ["bengali", "devotional", "calm"],
    "Gujarati Stories":    ["gujarati", "stories", "reading"],
    "Gujarati Devotional": ["gujarati", "devotional", "calm"],
    "Punjabi Stories":     ["punjabi", "stories", "reading"],
    "Punjabi Devotional":  ["punjabi", "devotional", "calm"],

    "Cricket":      ["cricket", "athletics", "excited"],
    "Badminton":    ["badminton", "athletics"],
    "Swimming":     ["swimming", "athletics", "movement"],
    "Athletics":    ["athletics", "movement"],
    "Basketball":   ["basketball", "athletics"],
    "Table Tennis": ["tabletennis", "athletics"],
    "Kabaddi":      ["kabaddi", "athletics"],
    "Ice Skating":  ["skating", "movement"],
    "Roller Skating":["rollerSkating", "movement"],
    "Martial Arts": ["martialArts", "bodyAwareness", "proud"],

    "Skating — First Steps on Skates":   ["skating", "movement", "bodyAwareness"],
    "Skating — Roller Skating Basics":   ["rollerSkating", "movement"],
    "Painting — Watercolour Basics":     ["drawing", "sight", "touch"],
    "Painting — Finger Painting":        ["drawing", "touch", "happy"],
    "Drawing — Drawing with Shapes":     ["drawing", "sight"],
    "Drawing — Drawing Faces & Expressions": ["drawing", "sight"],
    "Swimming — Water Safety First":     ["swimming", "calm", "bodyAwareness"],
    "Swimming — Learning Freestyle":     ["swimming", "movement", "bodyAwareness"],
}


# --------------------------------------------------------------------------- #
# 3b. CATEGORY -> LANGUAGE                                                    #
# --------------------------------------------------------------------------- #
# Maps each category to its primary spoken language. Used to:
#   - stamp every video with a `language` field
#   - optionally filter via --languages
#   - group output via --grouped
# --------------------------------------------------------------------------- #

CATEGORY_LANGUAGE: dict[str, str] = {
    # Indian-language buckets
    "Telugu Stories": "telugu",      "Telugu Devotional": "telugu",
    "Hindi Stories": "hindi",        "Hindi Devotional": "hindi",
    "Tamil Stories": "tamil",        "Tamil Devotional": "tamil",
    "Kannada Stories": "kannada",    "Kannada Devotional": "kannada",
    "Malayalam Stories": "malayalam","Malayalam Devotional": "malayalam",
    "Marathi Stories": "marathi",    "Marathi Devotional": "marathi",
    "Bengali Stories": "bengali",    "Bengali Devotional": "bengali",
    "Gujarati Stories": "gujarati",  "Gujarati Devotional": "gujarati",
    "Punjabi Stories": "punjabi",    "Punjabi Devotional": "punjabi",
}
# Everything else defaults to english
DEFAULT_LANGUAGE = "english"


# --------------------------------------------------------------------------- #
# 3c. CATEGORY -> DEFAULT AGE RANGE                                           #
# --------------------------------------------------------------------------- #
# Always-populated fallback. Claude can override these when --enrich is on,
# but the field is never blank in the output.
# --------------------------------------------------------------------------- #

CATEGORY_AGE_RANGE: dict[str, str] = {
    # Toddler / preschool content
    "Music & Relax": "1-5",
    "Communication": "1-5",
    "Activities": "2-6",
    "Painting — Finger Painting": "2-5",

    # Early childhood
    "Yoga & Exercise": "3-10",
    "Social Skills": "3-8",
    "Autism Support": "3-12",
    "Art & Crafts": "4-12",
    "Drawing — Drawing with Shapes": "4-8",
    "Drawing — Drawing Faces & Expressions": "6-12",
    "Painting — Watercolour Basics": "6-12",
    "Cooking": "5-12",

    # School age — core learning
    "Math": "5-12",
    "Science": "6-14",
    "Experiments": "6-14",
    "Measurements": "5-10",

    # Stories / devotional — broad family viewing
    "Telugu Stories": "3-12",      "Telugu Devotional": "3-15",
    "Hindi Stories": "3-12",       "Hindi Devotional": "3-15",
    "Tamil Stories": "3-12",       "Tamil Devotional": "3-15",
    "Kannada Stories": "3-12",     "Kannada Devotional": "3-15",
    "Malayalam Stories": "3-12",   "Malayalam Devotional": "3-15",
    "Marathi Stories": "3-12",     "Marathi Devotional": "3-15",
    "Bengali Stories": "3-12",     "Bengali Devotional": "3-15",
    "Gujarati Stories": "3-12",    "Gujarati Devotional": "3-15",
    "Punjabi Stories": "3-12",     "Punjabi Devotional": "3-15",

    # Sports — general intro to coached basics
    "Sports": "5-14",
    "Cricket": "6-14",       "Badminton": "6-14",
    "Swimming": "4-12",      "Athletics": "6-14",
    "Basketball": "6-14",    "Table Tennis": "7-14",
    "Kabaddi": "8-14",       "Ice Skating": "5-12",
    "Roller Skating": "5-12","Martial Arts": "6-14",

    # Activity lessons — leaning beginner / younger
    "Skating — First Steps on Skates": "4-8",
    "Skating — Roller Skating Basics": "5-10",
    "Swimming — Water Safety First": "3-8",
    "Swimming — Learning Freestyle": "6-12",
}
DEFAULT_AGE_RANGE = "5-12"


# --------------------------------------------------------------------------- #
# 4. HELPERS                                                                  #
# --------------------------------------------------------------------------- #

VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})")

# The temiedani youtube-mcp-server returns results as plain-text records like:
#
#   Title: Some video name
#   Channel: SomeChannel
#   Duration: 4:32
#   Views: 12,345
#   Description: Some text...
#   URL: https://www.youtube.com/watch?v=abcDEF12345
#
# Records are separated by blank lines. The parser below handles this shape
# (case-insensitive, tolerant of '-' or '–' separators, alternate field names
# like "Author"/"Uploader" for the channel).
_FIELD_ALIASES = {
    "title":       "title",
    "name":        "title",
    "channel":     "channel",
    "channel_title":"channel",
    "channeltitle":"channel",
    "channel name":"channel",
    "uploader":    "channel",
    "author":      "channel",
    "by":          "channel",
    "description": "description",
    "desc":        "description",
    "summary":     "description",
    "duration":    "duration",
    "length":      "duration",
    "views":       "views",
    "view count":  "views",
    "viewcount":   "views",
    "url":         "url",
    "link":        "url",
    "video url":   "url",
    "watch":       "url",
}
TEXT_FIELD_RE = re.compile(
    r"""^\s*
        [-*\u2022]?\s*            # optional bullet
        (?:\*\*)?                 # optional markdown bold
        (?P<key>[A-Za-z][A-Za-z _-]{0,25}?)
        (?:\*\*)?
        \s*[:\-\u2013]\s+         # ':' or '-' or '–'
        (?P<val>.*\S)\s*$""",
    re.VERBOSE,
)


def parse_text_record(text: str) -> list[dict[str, Any]]:
    """Parse the server's plain-text format into one dict per video.

    Records are separated by blank lines. Each line is `Field: value` (or
    `Field - value`, `Field – value`). Field names are matched case-insensitively
    against a small alias table.
    """
    records: list[dict[str, Any]] = []
    current: dict[str, str] = {}

    def flush() -> None:
        if not current:
            return
        out: dict[str, Any] = {k: v.strip() for k, v in current.items()}
        url = out.get("url", "")
        if url:
            m = VIDEO_ID_RE.search(url)
            if m:
                out["id"] = m.group(1)
        # Also recognise embedded IDs in a "URL" line that might be just an ID
        if "id" not in out and url and len(url) == 11 and re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
            out["id"] = url
        # Clean a noisy "No description available" sentinel some servers emit
        if out.get("description", "").lower().strip() in {
            "no description available", "n/a", "none", "-"
        }:
            out["description"] = ""
        if out.get("id"):
            records.append(out)

    for line in text.splitlines():
        if not line.strip():
            flush()
            current = {}
            continue
        m = TEXT_FIELD_RE.match(line)
        if not m:
            # If we're already collecting a record and this line has no field
            # marker, append it to the description (handles wrapped lines).
            if current.get("description"):
                current["description"] += " " + line.strip()
            continue
        key_raw = m.group("key").strip().lower()
        canonical = _FIELD_ALIASES.get(key_raw)
        if not canonical:
            continue
        current[canonical] = m.group("val").strip()
    flush()
    return records



def extract_video_id(record: dict[str, Any]) -> str | None:
    """Pull a YouTube video ID out of an MCP `get_videos` record.

    Handles several shapes the YouTube API / MCP server can return.
    """
    # Direct ID keys
    for key in ("video_id", "videoId", "id"):
        val = record.get(key)
        if isinstance(val, str) and len(val) == 11:
            return val
        # YouTube search API returns id as {"kind": "...", "videoId": "..."}
        if isinstance(val, dict):
            vid = val.get("videoId")
            if isinstance(vid, str) and len(vid) == 11:
                return vid
    # Pull from URL
    url = record.get("url") or record.get("video_url") or record.get("link") or ""
    if isinstance(url, str):
        m = VIDEO_ID_RE.search(url)
        if m:
            return m.group(1)
    return None


def is_safe(title: str, description: str) -> bool:
    blob = f"{title}\n{description}".lower()
    return not any(term in blob for term in BLOCK_TERMS)


def duration_to_seconds(text: str) -> int | None:
    """Convert '3:14' or '1:02:30' to total seconds. Returns None on failure.

    Also handles ISO-8601 like 'PT1H2M30S' that YouTube sometimes returns.
    """
    if not text:
        return None
    text = text.strip()
    # ISO-8601 duration
    iso = re.fullmatch(r"P?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", text)
    if iso and any(iso.groups()):
        h, m, s = (int(g) if g else 0 for g in iso.groups())
        if h or m or s:
            return h * 3600 + m * 60 + s
    # Colon form mm:ss or hh:mm:ss
    parts = text.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    return None


# --------------------------------------------------------------------------- #
# 4b. LANGUAGE / SCRIPT DETECTION                                             #
# --------------------------------------------------------------------------- #
# For Indian-language categories the title is often in the native script.
# These Unicode ranges let us check whether content appears to match the
# expected language (used as a soft signal, not a hard filter).
# --------------------------------------------------------------------------- #

LANGUAGE_SCRIPTS: dict[str, tuple[int, int]] = {
    "hindi":     (0x0900, 0x097F),  # Devanagari
    "marathi":   (0x0900, 0x097F),  # Devanagari (shared)
    "bengali":   (0x0980, 0x09FF),  # Bengali
    "punjabi":   (0x0A00, 0x0A7F),  # Gurmukhi
    "gujarati":  (0x0A80, 0x0AFF),  # Gujarati
    "tamil":     (0x0B80, 0x0BFF),  # Tamil
    "telugu":    (0x0C00, 0x0C7F),  # Telugu
    "kannada":   (0x0C80, 0x0CFF),  # Kannada
    "malayalam": (0x0D00, 0x0D7F),  # Malayalam
}

# Romanized keywords that strongly suggest a given language even when the
# title is in Latin script (e.g. "telugu rhymes", "hindi kahani").
LANGUAGE_KEYWORDS: dict[str, list[str]] = {
    "telugu":    ["telugu"],
    "hindi":     ["hindi", "kahani", "kahaniyan"],
    "tamil":     ["tamil"],
    "kannada":   ["kannada"],
    "malayalam": ["malayalam"],
    "marathi":   ["marathi", "goshti"],
    "bengali":   ["bengali", "bangla", "rupkothar"],
    "gujarati":  ["gujarati"],
    "punjabi":   ["punjabi", "gurmukhi", "shabad"],
}


def detect_language_signal(text: str, expected: str) -> bool:
    """Return True if `text` shows evidence of `expected` language.

    Combines two signals:
      - Native script: any character in the expected Unicode range.
      - Romanized keyword: the language name (or known cognate) in the text.
    """
    if not expected or expected == "english":
        return True  # don't gate english content
    blob = text.lower()
    for kw in LANGUAGE_KEYWORDS.get(expected, []):
        if kw in blob:
            return True
    rng = LANGUAGE_SCRIPTS.get(expected)
    if rng:
        lo, hi = rng
        for ch in text:
            if lo <= ord(ch) <= hi:
                return True
    return False


def infer_extra_tags(title: str, description: str) -> list[str]:
    blob = f"{title} {description}".lower()
    candidates = {
        "happy": ["happy", "joy", "smile"],
        "sad": ["sad"],
        "calm": ["calm", "relax", "lullaby", "meditat"],
        "excited": ["excited", "amazing", "wow"],
        "proud": ["proud", "achievement"],
        "grateful": ["grateful", "thank"],
        "sound": ["sound", "music", "song", "rhyme"],
        "touch": ["touch", "feel", "tactile"],
        "sight": ["color", "colour", "draw", "paint", "watch"],
        "movement": ["dance", "jump", "run", "exercise", "yoga"],
        "bodyAwareness": ["body", "posture", "balance"],
        "drama": ["drama", "play", "act"],
        "chess": ["chess"],
        "gardening": ["garden"],
        "photography": ["photo", "camera"],
        "coding": ["coding", "programming", "robot"],
        "dance": ["dance"],
        "singing": ["sing", "song"],
        "drawing": ["draw", "sketch", "paint"],
        "music": ["music", "song", "rhyme", "bhajan"],
        "devotional": ["bhajan", "bhakti", "devotional", "shabad", "stotra"],
        "stories": ["story", "stories", "tale", "fable", "goshti"],
        "yoga": ["yoga"],
        "cooking": ["recipe", "cook", "bake"],
    }
    found = []
    for tag, kws in candidates.items():
        if any(k in blob for k in kws):
            found.append(tag)
    return found


# --------------------------------------------------------------------------- #
# 5. MCP DRIVER                                                               #
# --------------------------------------------------------------------------- #

async def hydrate_via_mcp(
    session: ClientSession, video_id: str, *, debug: bool = False
) -> dict[str, Any]:
    """Call the server's get_video_info tool to fetch real metadata.

    Returns a dict with at least title / channel_title / description, or
    an empty dict on failure.
    """
    try:
        result = await session.call_tool(
            "get_video_info", arguments={"video_id": video_id}
        )
    except Exception as exc:  # noqa: BLE001
        if debug:
            print(f"    hydrate[{video_id}] failed: {exc}", file=sys.stderr)
        return {}

    payload = parse_tool_result(result, debug=False)
    if not payload:
        if debug:
            print(f"    hydrate[{video_id}] returned no payload", file=sys.stderr)
        return {}
    # get_video_info returns a single record
    return payload[0]


# --------------------------------------------------------------------------- #
# 6. CLAUDE ENRICHMENT (optional)                                             #
# --------------------------------------------------------------------------- #
# After hydrating metadata, optionally pass each record through Claude to:
#   - rewrite the description in a kid-friendly summary (1-2 sentences),
#   - refine tags from the user's controlled vocabulary,
#   - estimate an age range like "5-8" or "10-14".
# --------------------------------------------------------------------------- #

ALLOWED_TAG_VOCAB = sorted({
    # Activity / sport tags
    "cricket", "badminton", "swimming", "skating", "rollerSkating", "athletics",
    "basketball", "tabletennis", "kabaddi", "martialArts", "chess", "gymnastics",
    "dance", "singing", "photography", "coding", "reading", "drama", "debate",
    "gardening", "yoga", "cooking", "drawing", "music", "football",
    "devotional", "stories",
    # Language tags
    "telugu", "hindi", "tamil", "kannada", "malayalam", "marathi",
    "bengali", "gujarati", "punjabi", "assamese", "odia",
    # Emotions
    "happy", "sad", "angry", "scared", "surprised", "calm", "excited",
    "proud", "worried", "bored", "grateful", "lonely", "disgusted",
    "confused", "embarrassed", "jealous", "frustrated", "hopeful",
    "shy", "tired",
    # Sensory profile
    "sound", "touch", "sight", "smell", "taste", "movement", "bodyAwareness",
    # Misc
    "socialSkills", "communication",
})


CLAUDE_SYSTEM_PROMPT = """You enrich children's YouTube video metadata for an \
educational app. You will receive a batch of videos as JSON. For each video, \
return a JSON object with these fields:

  - id: string, copy unchanged
  - description: string, EXACTLY 1-2 sentences (15-40 words), kid-friendly \
summary of what the video teaches or shows. This field is REQUIRED and must \
NEVER be empty. Use the title, channel, category, and language together to \
write a meaningful description. Reason from context: a "Cosmic Kids Yoga" \
video under "Yoga & Exercise" with title "Squish the Fish" is a guided \
underwater-themed yoga adventure; a "Numberblocks" video under "Math" \
titled "Counting to Ten" teaches early counting through animated number \
characters. Stay factual about the FORMAT (animation, tutorial, song, \
demonstration) and GOAL (teaches X, demonstrates Y, helps with Z); do not \
invent plot details or claim specific facts you cannot infer. If the title \
is fully opaque, write a generic-but-useful category description (e.g. \
"Tamil moral story for children with simple language and a clear lesson").
  - tags: array of strings, ONLY from this allowed vocabulary: {vocab}. \
Pick the 3-7 most relevant tags. Always include any language/sport/activity \
tags that apply.
  - age_range: string like "1-3", "4-6", "7-10", "11-14", or "15-18" \
estimating the target audience. Use the duration as a signal (very short \
videos lean younger, longer tutorials lean older).

Return ONLY a JSON array of objects, one per input video, in the same order. \
No prose, no markdown fences."""


DESCRIBE_ONLY_SYSTEM_PROMPT = """You write short, kid-friendly descriptions \
for children's YouTube videos in an educational app. For each video in the \
batch, return a JSON object:

  - id: string, copy unchanged
  - description: string, EXACTLY 1-2 sentences (15-40 words). Required, \
must NEVER be empty.

Write the description by reasoning from the title, channel, category, and \
language together. Describe FORMAT (animation / song / tutorial / \
demonstration / story) and GOAL (what the viewer learns or experiences). \
Stay factual; do not invent plot points, character names, or claims you \
cannot infer. If the title is opaque, write a generic-but-useful description \
appropriate to the category (e.g. "An animated Hindi moral story for \
children with simple language and a clear lesson.").

Tone: warm and parent-friendly. No marketing language ("amazing!", \
"must-watch!"). No emoji. No quotes around the description.

Return ONLY a JSON array of {{id, description}} objects, same order as \
input. No prose, no markdown fences."""


def _chunked(items: list[Any], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _get_anthropic_client():
    """Return an Anthropic client, or None if SDK/key missing.

    Prints a single warning either way; callers should bail out gracefully.
    """
    try:
        import anthropic
    except ImportError:
        print(
            "WARN: `anthropic` package not installed; skipping Claude step. "
            "Install with `pip install anthropic`.",
            file=sys.stderr,
        )
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "WARN: ANTHROPIC_API_KEY not set; skipping Claude step.",
            file=sys.stderr,
        )
        return None
    return anthropic.Anthropic()


def _call_claude_json_array(
    client: Any,
    *,
    model: str,
    system_prompt: str,
    user_msg: str,
    max_tokens: int = 2000,
    debug: bool = False,
) -> list[dict[str, Any]] | None:
    """Call Claude, expect a JSON array back, parse it tolerantly."""
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: Claude call failed: {exc}", file=sys.stderr)
        return None

    raw_text = "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    ).strip()
    # Strip any fenced code block
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        if raw_text.lower().startswith("json"):
            raw_text = raw_text.split("\n", 1)[1] if "\n" in raw_text else raw_text[4:]
        raw_text = raw_text.strip()

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", raw_text, flags=re.DOTALL)
        if not m:
            print(f"WARN: could not parse Claude response: {raw_text[:200]!r}", file=sys.stderr)
            return None
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError as exc:
            print(f"WARN: nested parse also failed: {exc}", file=sys.stderr)
            return None
    if not isinstance(parsed, list):
        print(f"WARN: Claude response was not a list: {type(parsed).__name__}", file=sys.stderr)
        return None
    return parsed


def _local_fallback_description(record: dict[str, Any]) -> str:
    """Generate a deterministic, never-empty description from local context.

    Used when Claude is unreachable or doesn't return a description.
    Uses category, language, and (optionally) channel to compose a sensible
    one-liner without inventing specifics.
    """
    cat = record.get("category", "").strip() or "children's"
    lang = (record.get("language") or "english").lower()
    title = record.get("title", "").strip()
    channel = record.get("channel", "").strip()

    # Format-of-content hint by category
    cat_lower = cat.lower()
    if "devotional" in cat_lower:
        format_hint = "devotional content"
    elif "stories" in cat_lower or "story" in cat_lower:
        format_hint = "story for children"
    elif "yoga" in cat_lower or "exercise" in cat_lower:
        format_hint = "guided movement activity for children"
    elif "music" in cat_lower or "relax" in cat_lower:
        format_hint = "music or calming audio for children"
    elif "math" in cat_lower:
        format_hint = "early-math learning video for children"
    elif "science" in cat_lower or "experiment" in cat_lower:
        format_hint = "science learning video for children"
    elif "cricket" in cat_lower or "swimming" in cat_lower or "athletics" in cat_lower \
         or "basketball" in cat_lower or "kabaddi" in cat_lower or "skating" in cat_lower \
         or "tennis" in cat_lower or "martial" in cat_lower or "badminton" in cat_lower \
         or "sports" in cat_lower:
        format_hint = "sports lesson for children and beginners"
    elif "drawing" in cat_lower or "painting" in cat_lower or "art" in cat_lower:
        format_hint = "art activity for children"
    elif "communication" in cat_lower or "social" in cat_lower:
        format_hint = "social or communication skills lesson for children"
    elif "autism" in cat_lower:
        format_hint = "support resource focused on autism and children"
    elif "cooking" in cat_lower:
        format_hint = "simple cooking activity for children"
    else:
        format_hint = "educational video for children"

    lang_prefix = "" if lang == "english" else f"A {lang.title()}-language "
    if lang_prefix:
        base = f"{lang_prefix}{format_hint}"
    else:
        # Pick correct article based on first sound of format_hint
        article = "An" if format_hint[:1].lower() in "aeiou" else "A"
        base = f"{article} {format_hint}"

    if channel:
        base += f" from {channel}"
    if title and len(title) < 80:
        base += f': "{title}".'
    else:
        base += "."
    return base


def describe_with_claude(
    records: list[dict[str, Any]],
    *,
    model: str = "claude-haiku-4-5",
    batch_size: int = 6,
    max_tokens: int = 2500,
    overwrite: bool = False,
    debug: bool = False,
) -> list[dict[str, Any]]:
    """Generate or fill in `description` for every record using Claude.

    By default only fills records whose `description` is empty; pass
    `overwrite=True` to rewrite even non-empty descriptions in the consistent
    house style.

    Any record Claude does not supply a description for is filled by a
    deterministic local fallback so the field is NEVER empty in the output.
    """
    client = _get_anthropic_client()

    targets = [
        r for r in records
        if overwrite or not (r.get("description") or "").strip()
    ]

    if not targets:
        if debug:
            print(
                "    describe_with_claude: nothing to do (all records have descriptions)",
                file=sys.stderr,
            )
        return records

    by_id: dict[str, str] = {}

    if client is not None:
        print(
            f"Generating descriptions for {len(targets)} / {len(records)} records "
            f"via Claude ({model}, batch={batch_size}, max_tokens={max_tokens})…",
            file=sys.stderr,
        )

        for batch in _chunked(targets, batch_size):
            batch_input = [
                {
                    "id": r["id"],
                    "title": r.get("title", ""),
                    "channel": r.get("channel", ""),
                    "category": r.get("category", ""),
                    "language": r.get("language", ""),
                    "duration": r.get("duration", ""),
                }
                for r in batch
            ]
            user_msg = (
                "Write a description for each video. Output ONLY a JSON array "
                "of {id, description} objects, one per input video, same order. "
                "Every object MUST include a non-empty description.\n\n"
                + json.dumps(batch_input, ensure_ascii=False)
            )
            parsed = _call_claude_json_array(
                client,
                model=model,
                system_prompt=DESCRIBE_ONLY_SYSTEM_PROMPT,
                user_msg=user_msg,
                max_tokens=max_tokens,
                debug=debug,
            )
            if parsed is None:
                if debug:
                    ids = [r["id"] for r in batch]
                    print(f"    Claude failed for batch ids={ids}", file=sys.stderr)
                continue
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                vid = item.get("id")
                desc = (item.get("description") or "").strip()
                if vid and desc:
                    by_id[vid] = desc
            # Identify any IDs in the batch that Claude didn't respond for
            missing = [r["id"] for r in batch if r["id"] not in by_id]
            if missing and debug:
                print(
                    f"    Claude returned no description for {len(missing)} id(s): {missing}",
                    file=sys.stderr,
                )

        # Retry pass: any target still missing a description gets ONE more try,
        # one record per request (smallest possible payload).
        still_missing = [r for r in targets if r["id"] not in by_id]
        if still_missing:
            print(
                f"  retry pass for {len(still_missing)} record(s) Claude missed…",
                file=sys.stderr,
            )
            for r in still_missing:
                single = [{
                    "id": r["id"],
                    "title": r.get("title", ""),
                    "channel": r.get("channel", ""),
                    "category": r.get("category", ""),
                    "language": r.get("language", ""),
                    "duration": r.get("duration", ""),
                }]
                user_msg = (
                    "Write a description for this video. Output ONLY a JSON "
                    "array with one {id, description} object.\n\n"
                    + json.dumps(single, ensure_ascii=False)
                )
                parsed = _call_claude_json_array(
                    client,
                    model=model,
                    system_prompt=DESCRIBE_ONLY_SYSTEM_PROMPT,
                    user_msg=user_msg,
                    max_tokens=400,
                    debug=debug,
                )
                if parsed and isinstance(parsed, list) and parsed:
                    desc = (parsed[0].get("description") or "").strip()
                    if desc:
                        by_id[r["id"]] = desc
    else:
        # No client available — we'll fill everything with the local fallback
        print(
            f"  Claude unavailable; using local fallback for {len(targets)} description(s)",
            file=sys.stderr,
        )

    # Merge back. For any target that STILL lacks a description, build a
    # deterministic local fallback so the field is never empty.
    merged: list[dict[str, Any]] = []
    n_claude, n_fallback = 0, 0
    target_ids = {r["id"] for r in targets}
    for r in records:
        if r["id"] not in target_ids:
            merged.append(r)
            continue
        new = dict(r)
        new_desc = by_id.get(r["id"])
        if new_desc:
            new["description"] = new_desc
            n_claude += 1
        else:
            new["description"] = _local_fallback_description(r)
            n_fallback += 1
        merged.append(new)
    print(
        f"  descriptions: {n_claude} from Claude, {n_fallback} from local fallback",
        file=sys.stderr,
    )
    return merged


def enrich_with_claude(
    records: list[dict[str, Any]],
    *,
    model: str = "claude-haiku-4-5",
    batch_size: int = 10,
    debug: bool = False,
) -> list[dict[str, Any]]:
    """Send records through Claude to fill in richer description/tags/age.

    Requires:
      - ANTHROPIC_API_KEY in the environment
      - `pip install anthropic`

    If the SDK isn't installed or the key is missing, returns records unchanged
    (with a warning to stderr).
    """
    client = _get_anthropic_client()
    if client is None:
        return records

    system_prompt = CLAUDE_SYSTEM_PROMPT.format(vocab=", ".join(ALLOWED_TAG_VOCAB))

    enriched_by_id: dict[str, dict[str, Any]] = {}

    for batch in _chunked(records, batch_size):
        batch_input = [
            {
                "id": r["id"],
                "title": r.get("title", ""),
                "channel": r.get("channel", ""),
                "category": r.get("category", ""),
                "language": r.get("language", ""),
                "current_age_range": r.get("age_range", ""),
                "original_description": r.get("description", ""),
                "current_tags": r.get("tags", []),
            }
            for r in batch
        ]
        user_msg = (
            "Enrich these videos. Output ONLY a JSON array.\n\n"
            + json.dumps(batch_input, ensure_ascii=False)
        )
        parsed = _call_claude_json_array(
            client,
            model=model,
            system_prompt=system_prompt,
            user_msg=user_msg,
            max_tokens=2000,
            debug=debug,
        )
        if parsed is None:
            continue
        for item in parsed:
            if isinstance(item, dict) and item.get("id"):
                enriched_by_id[item["id"]] = item
        if debug:
            print(
                f"    Claude enriched {len(parsed)} / {len(batch)} in batch",
                file=sys.stderr,
            )

    # Merge enrichment back onto records
    merged: list[dict[str, Any]] = []
    for r in records:
        enriched = enriched_by_id.get(r["id"])
        if not enriched:
            merged.append(r)
            continue
        new_record = dict(r)
        new_desc = (enriched.get("description") or "").strip()
        if new_desc:
            new_record["description"] = new_desc
        new_tags = enriched.get("tags")
        if isinstance(new_tags, list) and new_tags:
            # Keep only tags from the allowed vocab
            allowed_set = set(ALLOWED_TAG_VOCAB)
            filtered = [t for t in new_tags if t in allowed_set]
            if filtered:
                new_record["tags"] = sorted(set(filtered))
        age_range = enriched.get("age_range")
        if isinstance(age_range, str) and age_range:
            new_record["age_range"] = age_range
        merged.append(new_record)

    return merged


def parse_tool_result(result: Any, *, debug: bool = False) -> list[dict[str, Any]]:
    """Turn an MCP CallToolResult into a flat list of video dicts.

    The server can return its payload in several shapes:
      - A single text content block whose .text is a JSON list of dicts.
      - A single text content block whose .text is a JSON object wrapping
        the list under "result" / "videos" / "items" / "data".
      - Multiple text blocks, one JSON dict per video.
      - A structuredContent attribute (newer MCP SDKs).
    """
    payload: list[dict[str, Any]] = []

    def absorb(obj: Any) -> None:
        if isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict):
                    payload.append(item)
                elif isinstance(item, str):
                    # Some servers wrap each video as a JSON string
                    try:
                        sub = json.loads(item)
                        if isinstance(sub, dict):
                            payload.append(sub)
                    except json.JSONDecodeError:
                        pass
        elif isinstance(obj, dict):
            for key in ("result", "videos", "items", "data", "results"):
                if isinstance(obj.get(key), list):
                    absorb(obj[key])
                    return
            payload.append(obj)

    # Newer MCP SDK exposes parsed structured output here
    structured = getattr(result, "structuredContent", None) or getattr(
        result, "structured_content", None
    )
    if structured is not None:
        absorb(structured)

    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is None:
            data = getattr(block, "data", None)
            if data is not None:
                absorb(data)
            continue
        # Try JSON first
        try:
            absorb(json.loads(text))
            continue
        except json.JSONDecodeError:
            pass
        # Try Python literal (handles single-quoted reprs)
        try:
            absorb(ast.literal_eval(text))
            continue
        except (ValueError, SyntaxError):
            pass
        # Plain-text "Title: ... Channel: ... URL: ..." format (this is what
        # the temiedani youtube-mcp-server emits)
        text_records = parse_text_record(text)
        if text_records:
            payload.extend(text_records)
            continue
        # Absolute last resort: just scrape IDs (loses metadata, so debug log
        # this so the user knows the structured parsers all missed)
        scraped_ids = list(VIDEO_ID_RE.finditer(text))
        if scraped_ids and debug:
            print(
                f"    !! parser fell through to URL-scrape ({len(scraped_ids)} ids); "
                f"first 300 chars: {text[:300]!r}",
                file=sys.stderr,
            )
        for m in scraped_ids:
            payload.append({"id": m.group(1), "title": "", "description": ""})

    return payload


async def call_get_videos(
    session: ClientSession, query: str, max_results: int, *, debug: bool = False
) -> list[dict[str, Any]]:
    result = await session.call_tool(
        "get_videos",
        arguments={"search": query, "max_results": max_results},
    )
    payload = parse_tool_result(result, debug=debug)

    if debug:
        n_content = len(getattr(result, "content", None) or [])
        print(
            f"    -> server returned {n_content} content block(s), "
            f"{len(payload)} parsed records",
            file=sys.stderr,
        )
        if not payload and n_content:
            for i, block in enumerate(getattr(result, "content", None) or []):
                t = getattr(block, "text", "")
                print(
                    f"    -> block[{i}] type={getattr(block, 'type', '?')}, "
                    f"len(text)={len(t)}, first 200 chars: {t[:200]!r}",
                    file=sys.stderr,
                )
        if payload:
            sample = payload[0]
            print(f"    -> sample keys: {sorted(sample.keys())[:12]}", file=sys.stderr)

    return payload


async def normalize_record(
    raw: dict[str, Any],
    category: str,
    *,
    drop_stats: dict[str, int],
    session: ClientSession | None = None,
    hydrate: bool = True,
    keep_empty: bool = False,
    debug: bool = False,
) -> dict[str, Any] | None:
    vid = extract_video_id(raw)
    if not vid:
        drop_stats["no_id"] += 1
        if debug:
            print(f"    skip[no_id]: keys={list(raw.keys())[:8]}", file=sys.stderr)
        return None

    title = (raw.get("title") or raw.get("name") or "").strip()
    channel = (
        raw.get("channel_title")
        or raw.get("channelTitle")
        or raw.get("channel")
        or raw.get("channel_name")
        or ""
    ).strip()
    description = (raw.get("description") or raw.get("summary") or "").strip()
    duration = (raw.get("duration") or raw.get("length") or "").strip()

    # If the search-result record was a stub (no title/channel/description),
    # hydrate it by calling get_video_info on the same MCP session.
    if hydrate and session is not None and not (title and channel):
        info = await hydrate_via_mcp(session, vid, debug=debug)
        if info:
            title = title or (info.get("title") or "").strip()
            channel = channel or (
                info.get("channel_title")
                or info.get("channelTitle")
                or info.get("channel")
                or ""
            ).strip()
            description = description or (info.get("description") or "").strip()
            duration = duration or (
                info.get("duration") or info.get("length") or ""
            ).strip()
            drop_stats["hydrated"] = drop_stats.get("hydrated", 0) + 1
        elif debug:
            print(f"    hydrate[{vid}] yielded nothing", file=sys.stderr)

    # If hydration still left us with no title or channel, drop the record by
    # default rather than emitting a half-empty stub. Pass --keep-empty to
    # override (useful for debugging which IDs were lost).
    if not (title and channel) and not keep_empty:
        drop_stats["empty_after_hydrate"] = drop_stats.get("empty_after_hydrate", 0) + 1
        if debug:
            print(
                f"    skip[empty_after_hydrate]: id={vid} "
                f"title={title!r} channel={channel!r}",
                file=sys.stderr,
            )
        return None

    if not is_safe(title, description):
        drop_stats["unsafe"] += 1
        if debug:
            print(f"    skip[unsafe]: {title[:60]!r}", file=sys.stderr)
        return None

    # Language gate: if the category is tied to a non-English language but
    # neither the native script nor a romanized keyword appears, treat the
    # result as a misfire and drop it. (English categories are never gated.)
    language = CATEGORY_LANGUAGE.get(category, DEFAULT_LANGUAGE)
    if language != DEFAULT_LANGUAGE and title:
        if not detect_language_signal(f"{title} {description}", language):
            drop_stats["wrong_language"] = drop_stats.get("wrong_language", 0) + 1
            if debug:
                print(
                    f"    skip[wrong_language={language}]: {title[:60]!r}",
                    file=sys.stderr,
                )
            return None

    base_tags = CATEGORY_TAGS.get(category, [])
    extra = infer_extra_tags(title, description)
    tags = sorted(set(base_tags + extra))

    short_desc = description[:280].rsplit(" ", 1)[0] if len(description) > 280 else description

    return {
        "id": vid,
        "title": title,
        "channel": channel,
        "category": category,
        "language": language,
        "duration": duration,
        "duration_seconds": duration_to_seconds(duration),
        "description": short_desc,
        "tags": tags,
        "age_range": CATEGORY_AGE_RANGE.get(category, DEFAULT_AGE_RANGE),
    }


async def fetch_all(
    *, debug: bool = False, hydrate: bool = True, keep_empty: bool = False
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    server_path = os.environ.get("YT_MCP_SERVER_PATH")
    if not server_path:
        print(
            "ERROR: set YT_MCP_SERVER_PATH to the absolute path of "
            "youtube-mcp-server/mcp_videos.py",
            file=sys.stderr,
        )
        sys.exit(1)
    if not Path(server_path).exists():
        print(f"ERROR: MCP server script not found at {server_path}", file=sys.stderr)
        sys.exit(1)

    command = "uv" if os.environ.get("USE_UV", "1") == "1" else sys.executable
    args = ["run", server_path] if command == "uv" else [server_path]

    params = StdioServerParameters(
        command=command,
        args=args,
        env=os.environ.copy(),
    )

    videos: dict[str, dict[str, Any]] = {}
    drop_stats = {"kept": 0, "duplicate": 0, "unsafe": 0, "no_id": 0, "empty_query": 0}

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("MCP session initialized.", file=sys.stderr)

            for category, queries in CATEGORY_QUERIES.items():
                print(f"\n[{category}]", file=sys.stderr)
                for query, n in queries:
                    try:
                        raw_list = await call_get_videos(
                            session, query, max_results=max(n * 3, 8), debug=debug
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(f"  ! query failed: {query!r} -> {exc}", file=sys.stderr)
                        continue

                    if not raw_list:
                        drop_stats["empty_query"] += 1

                    kept = 0
                    for raw in raw_list:
                        if kept >= n:
                            break
                        record = await normalize_record(
                            raw,
                            category,
                            drop_stats=drop_stats,
                            session=session,
                            hydrate=hydrate,
                            keep_empty=keep_empty,
                            debug=debug,
                        )
                        if record is None:
                            continue
                        if record["id"] in videos:
                            drop_stats["duplicate"] += 1
                            continue
                        videos[record["id"]] = record
                        drop_stats["kept"] += 1
                        kept += 1
                    print(
                        f"  + {kept:>2} kept (of {len(raw_list):>2} raw) "
                        f"from {query!r}",
                        file=sys.stderr,
                    )

    return {"videos": list(videos.values())}, drop_stats


# --------------------------------------------------------------------------- #
# 7. OUTPUT SHAPING                                                           #
# --------------------------------------------------------------------------- #

def filter_by_languages(
    videos: list[dict[str, Any]], languages: set[str]
) -> list[dict[str, Any]]:
    """Keep only videos whose `language` is in `languages` (lowercased).

    A `languages` set of {"all"} (or empty) is treated as 'include everything'.
    """
    if not languages or "all" in {lang.lower().strip() for lang in languages}:
        return videos
    wanted = {lang.lower().strip() for lang in languages}
    return [v for v in videos if (v.get("language") or "").lower() in wanted]


def filter_by_tags(
    videos: list[dict[str, Any]], tags: set[str]
) -> list[dict[str, Any]]:
    """Keep only videos that have at least one matching tag."""
    if not tags:
        return videos
    wanted = {t.strip() for t in tags}
    return [v for v in videos if wanted & set(v.get("tags") or [])]


def group_by_language_and_category(
    videos: list[dict[str, Any]],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Return {language: {category: [videos]}} with categories alphabetized."""
    out: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for v in videos:
        lang = v.get("language") or DEFAULT_LANGUAGE
        cat = v.get("category") or "Uncategorized"
        out.setdefault(lang, {}).setdefault(cat, []).append(v)
    # Sort inner categories alphabetically and languages with english first
    sorted_out: dict[str, dict[str, list[dict[str, Any]]]] = {}
    langs = sorted(out.keys(), key=lambda x: (x != "english", x))
    for lang in langs:
        sorted_out[lang] = {
            cat: out[lang][cat] for cat in sorted(out[lang].keys())
        }
    return sorted_out


# --------------------------------------------------------------------------- #
# 8. TOP-LEVEL OUTPUT SHAPE                                                   #
# --------------------------------------------------------------------------- #
# Matches the v9 schema (sproutpath_videos_flat_v9.json):
#   { version, generated, description, total_videos, categories_included,
#     videos: [ {id, title, channel, category, duration, duration_seconds,
#                description, tags, age_range} ] }
# `language` and `age_range` (when populated) are extra fields the original
# v9 left blank; we keep them populated and add them to each record.
# --------------------------------------------------------------------------- #

OUTPUT_VERSION = 10
OUTPUT_DESCRIPTION = (
    "Videos only accessible via the Videos tab category filter chips — "
    "no dedicated tile in Dashboard, Study or Activities tabs."
)


def _ordered_record(v: dict[str, Any]) -> dict[str, Any]:
    """Reorder a video dict so JSON output matches the v9 field order."""
    return {
        "id":               v.get("id", ""),
        "title":            v.get("title", ""),
        "channel":          v.get("channel", ""),
        "category":         v.get("category", ""),
        "language":         v.get("language", DEFAULT_LANGUAGE),
        "duration":         v.get("duration", ""),
        "duration_seconds": v.get("duration_seconds"),
        "description":     v.get("description", ""),
        "tags":             v.get("tags", []),
        "age_range":       v.get("age_range", ""),
    }


def build_top_level_output(
    videos: list[dict[str, Any]],
    *,
    grouped: bool = False,
    generated: str | None = None,
) -> dict[str, Any]:
    """Wrap the kept videos in the v9-style top-level schema.

    Produces:
      {
        "version": 10,
        "generated": "YYYY-MM-DD",
        "description": "...",
        "total_videos": N,
        "categories_included": [unique category names, in canonical order],
        "videos": [...]                    # default (flat)
        "by_language": {...}               # only when grouped=True
      }
    """
    from datetime import datetime, timezone

    if generated is None:
        generated = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # categories_included: preserve the CATEGORY_QUERIES key order so the
    # listing matches the script's source-of-truth ordering, but drop any
    # that have no videos in the kept set.
    present_cats = {v.get("category") for v in videos if v.get("category")}
    categories_included = [c for c in CATEGORY_QUERIES.keys() if c in present_cats]
    # Append any categories that aren't in CATEGORY_QUERIES (shouldn't happen,
    # but be safe) so nothing is silently dropped.
    extras = sorted(present_cats - set(categories_included))
    categories_included.extend(extras)

    ordered_videos = [_ordered_record(v) for v in videos]

    top: dict[str, Any] = {
        "version":             OUTPUT_VERSION,
        "generated":           generated,
        "description":         OUTPUT_DESCRIPTION,
        "total_videos":        len(ordered_videos),
        "categories_included": categories_included,
    }
    if grouped:
        top["by_language"] = group_by_language_and_category(ordered_videos)
    else:
        top["videos"] = ordered_videos
    return top


def parse_csv_arg(argv: list[str], flag: str) -> set[str]:
    """Pull `--flag a,b,c` out of argv; returns set of values lowercased."""
    for i, tok in enumerate(argv):
        if tok == flag and i + 1 < len(argv):
            return {x.strip().lower() for x in argv[i + 1].split(",") if x.strip()}
        if tok.startswith(flag + "="):
            return {x.strip().lower() for x in tok.split("=", 1)[1].split(",") if x.strip()}
    return set()


def main() -> None:
    argv = sys.argv[1:]
    debug = "--debug" in argv or os.environ.get("DEBUG") == "1"
    enrich = "--enrich" in argv or os.environ.get("ENRICH") == "1"
    describe = "--describe" in argv or os.environ.get("DESCRIBE") == "1"
    rewrite_desc = "--rewrite-descriptions" in argv
    hydrate = "--no-hydrate" not in argv  # hydration on by default
    grouped = "--grouped" in argv or os.environ.get("GROUPED") == "1"
    keep_empty = "--keep-empty" in argv or os.environ.get("KEEP_EMPTY") == "1"

    # --languages telugu,hindi,english   (or via env LANGUAGES=...)
    langs_arg = parse_csv_arg(argv, "--languages")
    if not langs_arg and os.environ.get("LANGUAGES"):
        langs_arg = {x.strip().lower() for x in os.environ["LANGUAGES"].split(",") if x.strip()}

    # --tags yoga,music
    tags_arg = parse_csv_arg(argv, "--tags")

    output, drop_stats = asyncio.run(
        fetch_all(debug=debug, hydrate=hydrate, keep_empty=keep_empty)
    )

    # Optional Claude enrichment (still runs before filtering so refined tags
    # are available for the --tags filter)
    model = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5")
    if enrich and output["videos"]:
        print(
            f"\nEnriching {len(output['videos'])} videos via Claude…",
            file=sys.stderr,
        )
        output["videos"] = enrich_with_claude(
            output["videos"], model=model, debug=debug
        )

    # Standalone description step. Implied by --enrich too, but explicit
    # --describe lets you fill descriptions without the full enrichment.
    if (describe or enrich or rewrite_desc) and output["videos"]:
        output["videos"] = describe_with_claude(
            output["videos"],
            model=model,
            overwrite=rewrite_desc,
            debug=debug,
        )

    # Apply filters
    n_before = len(output["videos"])
    if langs_arg and "all" not in langs_arg:
        output["videos"] = filter_by_languages(output["videos"], langs_arg)
        print(
            f"Language filter ({sorted(langs_arg)}): "
            f"{n_before} -> {len(output['videos'])}",
            file=sys.stderr,
        )
    elif langs_arg and "all" in langs_arg:
        print(
            "Language filter: 'all' (no filtering applied)",
            file=sys.stderr,
        )
    if tags_arg:
        n_pre_tag = len(output["videos"])
        output["videos"] = filter_by_tags(output["videos"], tags_arg)
        print(
            f"Tag filter ({sorted(tags_arg)}): "
            f"{n_pre_tag} -> {len(output['videos'])}",
            file=sys.stderr,
        )

    # Sort flat list: language, then category, then title
    output["videos"].sort(
        key=lambda v: (
            v.get("language") or "",
            v.get("category") or "",
            (v.get("title") or "").lower(),
        )
    )

    # Wrap in the v9-style top-level schema
    final = build_top_level_output(output["videos"], grouped=grouped)

    out_path = Path(os.environ.get("OUTPUT_PATH", "json/videos.json"))
    out_path.write_text(
        json.dumps(final, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nTotals: {drop_stats}", file=sys.stderr)
    print(
        f"Wrote {final['total_videos']} videos to {out_path.resolve()}"
        + (" (grouped)" if grouped else ""),
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()