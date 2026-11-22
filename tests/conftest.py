"""Guard: refuse to run the suite against a working database.

The database tests drop and re-create every table, so a
``TEST_DATABASE_URL`` that names the paper account's database would
erase it. Every test database must end in ``_test`` (finding 6)."""

import os

import pytest
from sqlalchemy.engine import make_url


def check_test_database_url(url: str | None) -> None:
    if not url:
        return
    name = make_url(url).database or ""
    if not name.endswith("_test"):
        raise ValueError(
            f"TEST_DATABASE_URL must name a database ending in _test; got '{name}'"
        )


def pytest_configure(config: pytest.Config) -> None:
    try:
        check_test_database_url(os.environ.get("TEST_DATABASE_URL"))
    except ValueError as error:
        pytest.exit(str(error), returncode=2)
