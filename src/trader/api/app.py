"""FastAPI application: /health plus the /api dashboard routes (spec 3.1)."""

from fastapi import FastAPI

from trader.api import auth, sse
from trader.api.routes import (
    activity,
    controls,
    decisions,
    plans,
    positions,
    reports,
    settings,
    status,
)
from trader.core.config import get_settings
from trader.core.logging import setup

setup(level=get_settings().log_level)

app = FastAPI(title="trader")

app.include_router(auth.router)
for r in (
    status.router,
    decisions.router,
    plans.router,
    positions.router,
    activity.router,
    reports.router,
    settings.router,
    controls.router,
    sse.router,
):
    app.include_router(r)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": get_settings().mode}
