"""Track the configured asset list (spec 5.3, spec D2).

Changing ``TRADER_SYMBOLS`` starts a new portfolio: the four-week count
of spec 5.3 restarts, and reports must mark the boundary rather than
silently compare across it. The engine records the configured list in
``settings`` at startup; the first start with a different list writes
one ``asset_list_changed`` event that reports use as the restart mark.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader.core.clock import Clock
from trader.data.sources.base import SYMBOLS
from trader.ledger.models import Event, Setting

SETTING_KEY = "assets.symbols"
KIND = "asset_list_changed"
# Before spec D2 the asset list was fixed in code, so a stored row
# absent on an existing deployment means that pair.
_PRE_D2_LIST = ["BTC-USD", "ETH-USD"]


def record_change(session: Session, clock: Clock) -> None:
    """Store the configured symbol list; on a change write one
    ``asset_list_changed`` event naming the old and new lists."""
    current = list(SYMBOLS)
    row = session.get(Setting, SETTING_KEY)
    stored = list(row.value) if row is not None else _PRE_D2_LIST
    if row is None:
        session.add(Setting(key=SETTING_KEY, value=current, updated_at=clock.now()))
    elif stored != current:
        row.value = current
        row.updated_at = clock.now()
    if stored != current:
        session.add(
            Event(
                time=clock.now(),
                kind=KIND,
                severity="info",
                message=(
                    f"Asset list changed: {', '.join(stored)} -> {', '.join(current)}."
                ),
                details={"from": stored, "to": current},
            )
        )


def last_change(session: Session) -> datetime | None:
    """Time of the most recent ``asset_list_changed`` event, or None."""
    return session.scalar(select(func.max(Event.time)).where(Event.kind == KIND))
