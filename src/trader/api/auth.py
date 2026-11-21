"""Dashboard login (spec 3.1): one password, one session cookie.

``POST /api/login`` checks the single password from
``TRADER_DASHBOARD_PASSWORD`` and sets an ``HttpOnly`` `SameSite=Lax`
session cookie — ``Secure`` when the request arrived over HTTPS (the
dashboard itself is served over plain HTTP on the LAN, where forcing
``Secure`` would stop the browser storing it). Repeated wrong passwords
lock the client out for a minute after five failures.

Every other ``/api`` route depends on :func:`require_auth` and returns
401 without a valid cookie. Sessions are in-memory tokens: they do not
survive an api restart (the owner just logs in again).

Single user only — no accounts, no roles (spec 3.1: out of scope).
"""

import hmac
import secrets
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from trader.api.schemas import LoginRequest, OkOut
from trader.core.config import get_settings

SESSION_COOKIE = "trader_session"

MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 60.0

# Live tokens. Process-local on purpose (see module docstring).
_tokens: set[str] = set()

# client ip -> recent failure timestamps (monotonic clock)
_attempts: dict[str, list[float]] = defaultdict(list)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _locked_out(key: str) -> bool:
    fails = [t for t in _attempts[key] if time.monotonic() - t < LOCKOUT_SECONDS]
    _attempts[key] = fails
    return len(fails) >= MAX_ATTEMPTS


router = APIRouter()


@router.post("/api/login", response_model=OkOut)
def login(body: LoginRequest, request: Request, response: Response) -> OkOut:
    key = _client_key(request)
    if _locked_out(key):
        raise HTTPException(status_code=429, detail="too many attempts; try later")
    password = get_settings().dashboard_password
    if not password:
        raise HTTPException(
            status_code=503, detail="dashboard password is not configured"
        )
    if not hmac.compare_digest(body.password, password):
        _attempts[key].append(time.monotonic())
        raise HTTPException(status_code=401, detail="wrong password")
    _attempts.pop(key, None)
    token = secrets.token_urlsafe(32)
    _tokens.add(token)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return OkOut()


@router.post("/api/logout", response_model=OkOut)
def logout(request: Request, response: Response) -> OkOut:
    token = request.cookies.get(SESSION_COOKIE)
    if token is not None:
        _tokens.discard(token)
    response.delete_cookie(SESSION_COOKIE)
    return OkOut()


def token_live(token: str | None) -> bool:
    return token is not None and token in _tokens


def require_auth(request: Request) -> None:
    """FastAPI dependency: 401 unless the session cookie holds a live token."""
    if not token_live(request.cookies.get(SESSION_COOKIE)):
        raise HTTPException(status_code=401, detail="not logged in")


AuthRequired = Depends(require_auth)
