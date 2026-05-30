"""Endpoint tests.

These run against the bundled ``data/videos.json``, so they're true
integration tests of the whole stack (loader + flattening + filtering
+ schema validation). The reset before each test is critical — the
upstream loader caches in module state, so tests would otherwise leak
into each other.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.upstream import reset_cache_for_tests


@pytest.fixture(autouse=True)
def _reset_cache():
    """Wipe the in-process cache before every test."""
    reset_cache_for_tests()
    yield
    reset_cache_for_tests()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ─── Healthz ───────────────────────────────────────────────────────────


def test_healthz(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ─── Envelope shape ────────────────────────────────────────────────────


def test_response_envelope_shape(client: TestClient) -> None:
    """Response must match the spec envelope exactly."""
    r = client.get("/sproutpath/api/v1/getvideos")
    assert r.status_code == 200
    data = r.json()
    # Required top-level keys
    for key in ("version", "generated", "description", "total_videos", "videos"):
        assert key in data, f"missing {key!r}"
    assert isinstance(data["videos"], list)
    assert data["total_videos"] == len(data["videos"])


def test_video_record_shape(client: TestClient) -> None:
    """Each video must carry the spec fields."""
    r = client.get("/sproutpath/api/v1/getvideos")
    data = r.json()
    assert data["videos"], "Expected at least one video"
    v = data["videos"][0]
    for key in (
        "id",
        "title",
        "channel",
        "category",
        "duration",
        "duration_seconds",
        "description",
        "tags",
        "age_range",
    ):
        assert key in v, f"missing {key!r} on video record"


# ─── No filters ────────────────────────────────────────────────────────


def test_no_filters_returns_full_catalogue(client: TestClient) -> None:
    r = client.get("/sproutpath/api/v1/getvideos")
    data = r.json()
    # The bundled feed has 476 videos walked across by_language.
    assert data["total_videos"] > 400


# ─── Language filter ───────────────────────────────────────────────────


def test_language_filter_iso_code(client: TestClient) -> None:
    """ISO code ``te`` must resolve to telugu."""
    r = client.get("/sproutpath/api/v1/getvideos?languages=te")
    data = r.json()
    assert data["total_videos"] > 0
    assert all(v["language"] == "telugu" for v in data["videos"])


def test_language_filter_full_name(client: TestClient) -> None:
    r = client.get("/sproutpath/api/v1/getvideos?languages=telugu")
    data = r.json()
    assert data["total_videos"] > 0
    assert all(v["language"] == "telugu" for v in data["videos"])


def test_language_filter_repeatable(client: TestClient) -> None:
    """Repeated ``languages=`` params union their values."""
    r = client.get(
        "/sproutpath/api/v1/getvideos?languages=telugu&languages=hindi"
    )
    data = r.json()
    langs = {v["language"] for v in data["videos"]}
    assert langs == {"telugu", "hindi"}


def test_language_filter_comma_separated(client: TestClient) -> None:
    """Comma-separated single param works the same as repeated."""
    r = client.get("/sproutpath/api/v1/getvideos?languages=telugu,hindi")
    data = r.json()
    langs = {v["language"] for v in data["videos"]}
    assert langs == {"telugu", "hindi"}


def test_unknown_language_returns_empty(client: TestClient) -> None:
    """Unknown language names yield zero matches — not a 4xx."""
    r = client.get("/sproutpath/api/v1/getvideos?languages=klingon")
    assert r.status_code == 200
    assert r.json()["total_videos"] == 0


# ─── Age filter ────────────────────────────────────────────────────────


def test_age_filter_keeps_in_range(client: TestClient) -> None:
    r = client.get("/sproutpath/api/v1/getvideos?age=8")
    data = r.json()
    assert data["total_videos"] > 0
    # Every returned video must have either an empty age_range (no
    # restriction) or a range whose bounds include 8.
    for v in data["videos"]:
        ar = v["age_range"]
        if not ar:
            continue
        low, high = (int(x) for x in ar.replace("–", "-").split("-"))
        assert low <= 8 <= high, f"age 8 not in {ar} for {v['id']}"


def test_age_filter_excludes_out_of_range(client: TestClient) -> None:
    """A very young age excludes the 6-14 and 8-14 ranges."""
    r_young = client.get("/sproutpath/api/v1/getvideos?age=1")
    young = r_young.json()
    # No video with an explicit range starting above 1 should survive.
    for v in young["videos"]:
        ar = v["age_range"]
        if not ar:
            continue
        low = int(ar.replace("–", "-").split("-")[0])
        assert low <= 1, f"age 1 not in {ar} for {v['id']}"


def test_age_filter_rejects_out_of_bounds(client: TestClient) -> None:
    """Age above the validator's upper bound returns 422."""
    r = client.get("/sproutpath/api/v1/getvideos?age=999")
    assert r.status_code == 422


# ─── Combined filters ──────────────────────────────────────────────────


def test_language_and_age_combined(client: TestClient) -> None:
    r = client.get("/sproutpath/api/v1/getvideos?languages=telugu&age=8")
    data = r.json()
    for v in data["videos"]:
        assert v["language"] == "telugu"
        ar = v["age_range"]
        if ar:
            low, high = (int(x) for x in ar.replace("–", "-").split("-"))
            assert low <= 8 <= high
