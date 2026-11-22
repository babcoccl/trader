"""The test-database guard accepts only names ending in ``_test``."""

import pytest

from tests.conftest import check_test_database_url


def test_accepts_test_database() -> None:
    check_test_database_url(
        "postgresql+psycopg://trader:trader@localhost:5432/trader_test"
    )


def test_accepts_missing_url() -> None:
    check_test_database_url(None)
    check_test_database_url("")


def test_rejects_working_database() -> None:
    with pytest.raises(ValueError, match="got 'trader'"):
        check_test_database_url(
            "postgresql+psycopg://trader:trader@localhost:5432/trader"
        )


def test_rejects_service_database() -> None:
    with pytest.raises(ValueError, match="got 'postgres'"):
        check_test_database_url(
            "postgresql+psycopg://trader:trader@localhost:5432/postgres"
        )
