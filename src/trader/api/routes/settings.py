"""Settings routes (spec 3.1): read all, update the editable ones.

Only the keys in ``EDITABLE`` may be changed through the API — each has a
validator and every write lands in ``audit_log``. Risk limits
(``risk.limits.*``) and risk state (``risk.*``) are deliberately not
editable here: the dashboard shows them read-only, and the halt lives
behind ``/api/controls``, not a settings write.

``autonomy``, ``approval_expiry_hours`` and ``mode`` are reported but not
editable: they are fixed by the code and ``TRADER_MODE`` (spec R7 — the
stored rows did nothing).
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader.api.auth import AuthRequired
from trader.api.routes import SessionDep
from trader.api.schemas import SettingOut, SettingPut
from trader.core.config import get_settings
from trader.ledger.models import AuditLog, Setting
from trader.plans.builder import APPROVAL_EXPIRY

router = APIRouter(dependencies=[AuthRequired])

ACTOR = "owner"


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


def _rate(v: Any) -> float:
    x = float(v)
    if not 0 <= x <= 1:
        raise ValueError("t_bill_rate must be between 0 and 1")
    return x


def _strict_bool(key: str) -> Any:
    def validate(v: Any) -> bool:
        if not isinstance(v, bool):
            raise ValueError(f"{key} must be a boolean")
        return v

    return validate


def _ranged(key: str, lo: float, hi: float) -> Any:
    def validate(v: Any) -> float:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{key} must be a number")
        x = float(v)
        if not lo <= x <= hi:
            raise ValueError(f"{key} must be between {lo} and {hi}")
        return x

    return validate


def _ranged_decimal(key: str, lo: str, hi: str) -> Any:
    """Monetary/rate validator: stores the canonical decimal string, per the
    repo convention that money is Decimal, never a rounded float."""
    lo_d, hi_d = Decimal(lo), Decimal(hi)

    def validate(v: Any) -> str:
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            raise ValueError(f"{key} must be a number")
        try:
            d = Decimal(str(v))
        except InvalidOperation as e:
            raise ValueError(f"{key} must be a number") from e
        if not d.is_finite():
            raise ValueError(f"{key} must be finite")
        if not lo_d <= d <= hi_d:
            raise ValueError(f"{key} must be between {lo} and {hi}")
        return str(d)

    return validate


# Monetary keys whose values are stored as canonical decimal strings.
_DECIMAL_KEYS = {
    "subsidy_target_volume_usd",
    "subsidy_daily_cap_usd",
    "subsidy_order_notional_usd",
    "auto_approve_max_notional_usd",
    "auto_approve_max_loss_pct",
}

# Sentinel: the default comes from get_settings() (the .env value), not a
# hardcoded constant, so GET /api/settings never drifts from the env config.
_ENV = object()

EDITABLE: dict[str, tuple[Any, Any]] = {
    # key -> (default shown when no row exists, validator)
    "silence_alarm_minutes": (5, _minutes),
    "digest_time_utc": ("00:30", _hhmm),
    "t_bill_rate": (0.0, _rate),
    "subsidy_enabled": (_ENV, _strict_bool("subsidy_enabled")),
    "subsidy_target_volume_usd": (
        _ENV,
        _ranged_decimal("subsidy_target_volume_usd", "100", "10000000"),
    ),
    "subsidy_daily_cap_usd": (
        _ENV,
        _ranged_decimal("subsidy_daily_cap_usd", "50", "1000000"),
    ),
    "subsidy_adx_max": (_ENV, _ranged("subsidy_adx_max", 1.0, 100.0)),
    "subsidy_order_notional_usd": (
        _ENV,
        _ranged_decimal("subsidy_order_notional_usd", "10", "50000"),
    ),
    "auto_approve_enabled": (_ENV, _strict_bool("auto_approve_enabled")),
    "auto_approve_max_notional_usd": (
        _ENV,
        _ranged_decimal("auto_approve_max_notional_usd", "10", "100000"),
    ),
    "auto_approve_max_loss_pct": (
        _ENV,
        _ranged_decimal("auto_approve_max_loss_pct", "0.001", "0.10"),
    ),
}


def _default(key: str) -> Any:
    default = EDITABLE[key][0]
    if default is not _ENV:
        return default
    value = getattr(get_settings(), key)
    if key in _DECIMAL_KEYS:
        return str(value)
    return float(value) if isinstance(value, Decimal) else value


def _fixed() -> dict[str, Any]:
    # Reported read-only values; a stored row for one of these is ignored.
    return {
        "autonomy": "approve_every_plan",
        "approval_expiry_hours": APPROVAL_EXPIRY.total_seconds() / 3600,
        "mode": get_settings().mode,
    }


@router.get("/api/settings", response_model=list[SettingOut])
def get_all(session: Session = SessionDep) -> list[SettingOut]:
    fixed = _fixed()
    rows = {
        r.key: r for r in session.scalars(select(Setting)).all() if r.key not in fixed
    }
    out = [
        SettingOut(key=r.key, value=r.value, updated_at=r.updated_at)
        for r in rows.values()
    ]
    shown = set(rows)
    for key in EDITABLE:
        if key not in shown:
            out.append(SettingOut(key=key, value=_default(key), updated_at=None))
    for key, value in fixed.items():
        out.append(SettingOut(key=key, value=value, updated_at=None))
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
