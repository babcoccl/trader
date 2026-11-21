"""FastAPI application: /health plus the /api dashboard routes (spec 3.1).

A background task writes one ``heartbeats`` row every 30 seconds so the
api's own silence is visible to the watchdog and the status strip like
every other service's (spec section A, spec 4.1).
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from trader.api import auth, sse
from trader.api.routes import (
    activity,
    controls,
    decisions,
    funding,
    plans,
    positions,
    reports,
    settings,
    status,
)
from trader.core.config import get_settings
from trader.core.db import session_scope
from trader.core.logging import setup
from trader.ops.heartbeat import beat

setup(level=get_settings().log_level)

log = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 30


async def _heartbeat_loop() -> None:
    while True:
        try:
            with session_scope() as session:
                beat(session, "api")
        except Exception:
            log.exception("api heartbeat failed")
        await asyncio.sleep(HEARTBEAT_SECONDS)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    task = asyncio.create_task(_heartbeat_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="trader", lifespan=_lifespan)

app.include_router(auth.router)
for r in (
    status.router,
    decisions.router,
    plans.router,
    positions.router,
    activity.router,
    funding.router,
    reports.router,
    settings.router,
    controls.router,
    sse.router,
):
    app.include_router(r)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": get_settings().mode}
