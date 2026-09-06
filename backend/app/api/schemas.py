from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str


class LoginResponse(BaseModel):
    token: str
    user: dict[str, Any]


class IncidentPatch(BaseModel):
    status: str | None = None
    note: str | None = None
    resolution: str | None = None
    impact: str | None = None
    root_cause: str | None = None


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class PostmortemIn(BaseModel):
    impact: str
    timeline: str
    root_cause: str
    resolution: str
    contributing_factors: str = ""
    preventative_actions: str = ""


class ExecIn(BaseModel):
    device: str
    command: str = Field(min_length=1, max_length=2000)
