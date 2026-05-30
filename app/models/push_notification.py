"""Pydantic schemas for the push notifications endpoint."""

from __future__ import annotations

from typing import List

from pydantic import BaseModel


class NotificationEntry(BaseModel):
    id: str
    version: str
    date: str
    type: str
    title: str
    body: str
    emoji: str = ""
    notifyUsers: bool = True


class PushNotificationsResponse(BaseModel):
    entries: List[NotificationEntry]
    total: int
