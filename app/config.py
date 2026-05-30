"""Application settings.

All knobs live here so the rest of the code never reaches for ``os.environ``
directly. Override anything via environment variables prefixed with
``SPROUTPATH_`` (e.g. ``SPROUTPATH_DATA_PATH=/some/where/videos.json``).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Project root — the directory that contains ``app/`` and ``data/``.
# Resolved here so the default data path works regardless of where uvicorn
# is launched from.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Runtime configuration.

    Defaults are wired so the app runs with zero env-vars during local dev —
    it reads the bundled ``data/videos.json`` file. In production, point
    ``data_path`` at a mounted file or set ``data_url`` to fetch from
    upstream (the loader picks URL over path when both are set).
    """

    # ─── Data source ────────────────────────────────────────────────────
    # Bundled file path. Used when ``data_url`` is empty.
    data_path: Path = Field(default=PROJECT_ROOT / "json" / "videos.json")

    # Optional remote URL. When non-empty, the loader fetches this once at
    # startup and caches the parsed payload in memory.
    data_url: str = Field(default="")

    # ─── HTTP behaviour ─────────────────────────────────────────────────
    request_timeout_seconds: float = Field(default=10.0)

    # ─── API metadata ───────────────────────────────────────────────────
    api_title: str = Field(default="SproutPath Videos API")
    api_version: str = Field(default="1.0.0")

    # CORS — wide-open by default for local development. Tighten in prod.
    cors_allow_origins: list[str] = Field(default_factory=lambda: ["*"])

    model_config = SettingsConfigDict(
        env_prefix="SPROUTPATH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
