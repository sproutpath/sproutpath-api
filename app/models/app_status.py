"""Pydantic schemas for the app status endpoint."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class Maintenance(BaseModel):
    enabled: bool
    message: str
    estimatedEndTime: Optional[str] = None


class VersionControl(BaseModel):
    minimumVersion: str
    allowedVersions: List[str]
    updateMessage: str


class AppStatusResponse(BaseModel):
    maintenance: Maintenance
    versionControl: VersionControl
