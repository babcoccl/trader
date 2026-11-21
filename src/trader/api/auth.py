"""Dashboard login (spec 3.1): one password, one session cookie.

``POST /api/login`` checks the single password from
``TRADER_DASHBOARD_PASSWORD`` and sets an ``HttpOnly`` session cookie.
Every other ``/api`` route depends on :func:`require_auth` and returns
401 without a valid cookie. Sessions are in-memory tokens: they do not
survive an api restart (the owner just logs in again).

Single user only — no accounts, no roles (spec 3.1: out of scope).
"""

import hmac
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from trader.api.schemas import LoginRequest, OkOut
from trader.core.config import get_settings

SESSION_COOKIE = "trader_session"

# Live tokens. Process-local on purpose (see module docstring).
_tokens: set[str] = set()

router = APIRouter()


@router.post("/api/login", response_model=OkOut)
def login(body: LoginRequest, response: Response) -> OkOut:
    password = get_settings().dashboard_password
    if not password:
        raise HTTPException(
            status_code=503, detail="dashboard password is not configured"
        )
    if not hmac.compare_digest(body.password, password):
        raise HTTPException(status_code=401, detail="wrong password")
    token = secrets.token_urlsafe(32)
    _tokens.add(token)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax")
    return OkOut()


@router.post("/api/logout", response_model=OkOut)
def logout(request: Request, response: Response) -> OkOut:
    token = request.cookies.get(SESSION_COOKIE)
    if token is not None:
        _tokens.discard(token)
    response.delete_cookie(SESSION_COOKIE)
    return OkOut()


def require_auth(request: Request) -> None:
    """FastAPI dependency: 401 unless the session cookie holds a live token."""
    token = request.cookies.get(SESSION_COOKIE)
    if token is None or token not in _tokens:
        raise HTTPException(status_code=401, detail="not logged in")


AuthRequired = Depends(require_auth)
