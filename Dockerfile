FROM python:3.12-slim AS base

WORKDIR /app

COPY pyproject.toml ./
COPY src/ src/
COPY alembic.ini ./
COPY migrations/ migrations/
COPY research/fixtures/ research/fixtures/
RUN pip install --no-cache-dir .

CMD ["uvicorn", "trader.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
