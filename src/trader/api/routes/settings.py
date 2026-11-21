"""Settings routes (spec 3.1): read all, update the editable ones.

Only the keys in ``EDITABLE`` may be changed through the API — each has a
validator and every write lands in ``audit_log``. Risk limits
(``risk.limits.*``) and risk state (``risk.*``) are deliberately not
editable here: the dashboard shows them read-only, and the halt lives
behind ``/api/controls``, not a settings write.

``mode`` is settable only to ``"paper"``; ``"live"`` additionally requires
``TRADER_LIVE_ENABLED=true`` (spec section A: live order placement needs
that flag).
"""

import re
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader.api.auth import AuthRequired
from trader.api.routes import SessionDep
from trader.api.schemas import SettingOut, SettingPut
from trader.core.config import get_settings
from trader.ledger.models import AuditLog, Setting

router = APIRouter(dependencies=[AuthRequired])

ACTOR = "owner"


def _hours(v: Any) -> float:
    x = float(v)
    if not 0.5 <= x <= 72:
        raise ValueError("approval_expiry_hours must be between 0.5 and 72")
    return x


def _minutes(v: Any) -> int:
    x = int(v)
    if not 1 <= x <= 1440:
        raise ValueError("silence_alarm_minutes must be between 1 and 1440")
    return x


def _hhmm(v: Any) -> str:
    s = str(v)
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", s):
        raise ValueError("digest_time_utc must be HH:MM (24h UTC)")
    return s


def _autonomy(v: Any) -> str:
    s = str(v)
    if s != "approve_every_plan":
        raise ValueError("only 'approve_every_plan' is selectable for now")
    return s


def _rate(v: Any) -> float:
    x = float(v)
    if not 0 <= x <= 1:
        raise ValueError("t_bill_rate must be between 0 and 1")
    return x


def _mode(v: Any) -> str:
    s = str(v)
    if s not in ("paper", "live"):
        raise ValueError("mode must be 'paper' or 'live'")
    if s == "live" and not get_settings().live_enabled:
        raise ValueError("live mode requires TRADER_LIVE_ENABLED=true")
    return s


EDITABLE: dict[str, tuple[Any, Any]] = {
    # key -> (default shown when no row exists, validator)
    "autonomy": ("approve_every_plan", _autonomy),
    "approval_expiry_hours": (6.0, _hours),
    "silence_alarm_minutes": (5, _minutes),
    "digest_time_utc": ("00:30", _hhmm),
    "t_bill_rate": (0.0, _rate),
    "mode": ("paper", _mode),
}


@router.get("/api/settings", response_model=list[SettingOut])
def get_all(session: Session = SessionDep) -> list[SettingOut]:
    rows = {r.key: r for r in session.scalars(select(Setting)).all()}
    out = [
        SettingOut(key=r.key, value=r.value, updated_at=r.updated_at)
        for r in rows.values()
    ]
    shown = set(rows)
    for key, (default, _) in EDITABLE.items():
        if key not in shown:
            out.append(SettingOut(key=key, value=default, updated_at=None))
    out.sort(key=lambda s: s.key)
    return out


@router.put("/api/settings/{key}", response_model=SettingOut)
def put(key: str, body: SettingPut, session: Session = SessionDep) -> SettingOut:
    if key not in EDITABLE:
        raise HTTPException(status_code=404, detail=f"setting '{key}' is not editable")
    _, validate = EDITABLE[key]
    try:
        value = validate(body.value)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    row = session.get(Setting, key)
    before = row.value if row is not None else None
    if row is None:
        row = Setting(key=key, value=value, updated_at=func.now())
        session.add(row)
    else:
        row.value = value
        row.updated_at = func.now()
    session.add(
        AuditLog(
            time=func.now(),
            actor=ACTOR,
            action=f"set {key}",
            before={key: before},
            after={key: value},
        )
    )
    session.flush()
    return SettingOut(key=row.key, value=row.value, updated_at=row.updated_at)
