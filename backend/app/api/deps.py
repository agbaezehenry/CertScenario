"""Demo auth (spec §39 step 1). Tokens are ``demo-<user id>``; sign-in is by email.

This is intentionally trivial for the MVP and is isolated here so a real
identity provider can replace it without touching routes.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, WebSocket, status

from app.models.domain import User
from app.services.world import DEMO_USERS

TOKEN_PREFIX = "demo-"


def user_from_token(token: str | None) -> User | None:
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    return DEMO_USERS.get(token[len(TOKEN_PREFIX):])


def token_for(user: User) -> str:
    return f"{TOKEN_PREFIX}{user.id}"


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get("northstar_token")


def current_user(request: Request) -> User:
    user = user_from_token(_bearer(request))
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sign in required")
    return user


def ws_user(ws: WebSocket) -> User | None:
    token = ws.query_params.get("token") or ws.cookies.get("northstar_token")
    if not token:
        auth = ws.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
    return user_from_token(token)


CurrentUser = Depends(current_user)
