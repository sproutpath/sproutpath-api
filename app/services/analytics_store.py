"""In-process analytics store for mobile session records.

Each record is a flat dict matching MobileAnalyticsRecord.
Swap this module for a real DB (PostgreSQL, ClickHouse) without
touching the API layer.

Public API
----------
append(record_dict)          — store one record
query(...)                   — filtered list of records
aggregate_by(field, ...)     — count + unique_users grouped by field value
count_store()                — total records in store
clear_for_tests()            — reset (tests only)
"""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional


_store: List[Dict[str, Any]] = []
_lock  = threading.Lock()


# ── Write ─────────────────────────────────────────────────────────────────────

def append(record: Dict[str, Any]) -> None:
    with _lock:
        _store.append(record)

def append_many(records: List[Dict[str, Any]]) -> None:
    with _lock:
        _store.extend(records)


# ── Read ──────────────────────────────────────────────────────────────────────

def _parse_ts(ts: str) -> datetime:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def query(
    unique_id:   Optional[str]  = None,
    country:     Optional[str]  = None,
    city:        Optional[str]  = None,
    language:    Optional[str]  = None,
    activity:    Optional[str]  = None,   # checks if value is in activities list
    platform:    Optional[str]  = None,
    age_min:     Optional[int]  = None,
    age_max:     Optional[int]  = None,
    start_date:  Optional[str]  = None,
    end_date:    Optional[str]  = None,
    extra_filter: Optional[Callable[[Dict[str, Any]], bool]] = None,
) -> List[Dict[str, Any]]:
    """Return records matching all supplied (non-None) criteria."""
    start_dt = _parse_ts(start_date) if start_date else None
    end_dt   = _parse_ts(end_date)   if end_date   else None

    with _lock:
        snapshot = list(_store)

    results = []
    for r in snapshot:
        if unique_id and r.get("unique_id") != unique_id:
            continue
        if country  and r.get("country",  "").upper() != country.upper():
            continue
        if city     and r.get("city",     "").lower()  != city.lower():
            continue
        if language and r.get("language", "").lower()  != language.lower():
            continue
        if platform and r.get("platform", "").lower()  != platform.lower():
            continue
        if activity:
            # activity filter: record must include this activity in its list
            if activity.lower() not in [a.lower() for a in r.get("activities", [])]:
                continue
        age = r.get("age")
        if age_min is not None and (age is None or age < age_min):
            continue
        if age_max is not None and (age is None or age > age_max):
            continue
        ts = _parse_ts(r.get("timestamp", ""))
        if start_dt and ts < start_dt:
            continue
        if end_dt   and ts > end_dt:
            continue
        if extra_filter and not extra_filter(r):
            continue
        results.append(r)

    return results


def aggregate_by(
    field: str,                           # top-level field name e.g. "country"
    records: Optional[List[Dict[str, Any]]] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Group by a top-level string field. Returns [{key, count, unique_users}]."""
    if records is None:
        with _lock:
            records = list(_store)

    counts: Dict[str, int] = defaultdict(int)
    users:  Dict[str, set] = defaultdict(set)

    for r in records:
        key = str(r.get(field, "") or "")
        counts[key] += 1
        users[key].add(r.get("unique_id", "anon"))

    result = [
        {"key": k, "count": counts[k], "unique_users": len(users[k])}
        for k in counts if k
    ]
    result.sort(key=lambda x: x["count"], reverse=True)
    return result[:limit]


def aggregate_activities(
    records: Optional[List[Dict[str, Any]]] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Explode the activities list per record and aggregate."""
    if records is None:
        with _lock:
            records = list(_store)

    counts: Dict[str, int] = defaultdict(int)
    users:  Dict[str, set] = defaultdict(set)

    for r in records:
        uid = r.get("unique_id", "anon")
        for act in r.get("activities", []):
            act = act.strip().lower()
            if act:
                counts[act] += 1
                users[act].add(uid)

    result = [
        {"key": k, "count": counts[k], "unique_users": len(users[k])}
        for k in counts
    ]
    result.sort(key=lambda x: x["count"], reverse=True)
    return result[:limit]


# ── Age group bucketing ───────────────────────────────────────────────────────

_AGE_GROUPS = [
    ("toddler",        2,  4),
    ("early_learner",  5,  7),
    ("junior",         8,  10),
    ("preteen",        11, 13),
    ("teen",           14, 99),
]

def aggregate_age_groups(
    records: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Bucket records by age group and return counts."""
    if records is None:
        with _lock:
            records = list(_store)

    counts: Dict[str, int] = defaultdict(int)
    users:  Dict[str, set] = defaultdict(set)

    for r in records:
        age = r.get("age")
        if age is None:
            continue
        uid = r.get("unique_id", "anon")
        for label, lo, hi in _AGE_GROUPS:
            if lo <= age <= hi:
                counts[label] += 1
                users[label].add(uid)
                break

    return [
        {
            "age_group": f"{label} ({lo}-{hi})",
            "min_age": lo,
            "max_age": hi,
            "count": counts[label],
            "unique_users": len(users[label]),
        }
        for label, lo, hi in _AGE_GROUPS
    ]


def count_store() -> int:
    with _lock:
        return len(_store)

def clear_for_tests() -> None:
    global _store
    with _lock:
        _store = []
