"""FastAPI application. Currently exposes only /health."""

from fastapi import FastAPI

from trader.core.config import get_settings
from trader.core.logging import setup

setup(level=get_settings().log_level)

app = FastAPI(title="trader")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": get_settings().mode}
