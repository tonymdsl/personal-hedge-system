from __future__ import annotations

from pathlib import Path

from common.config import PROJECT_ROOT
from common.db import connect, fetch_metadata, table_exists, upsert_metadata


TEST_DB = PROJECT_ROOT / "cache" / "test_meridian.sqlite3"


def _cleanup(path: Path = TEST_DB) -> None:
    if path.exists():
        path.unlink()


def test_connect_initializes_base_schema() -> None:
    _cleanup()
    try:
        with connect(TEST_DB) as connection:
            assert table_exists(connection, "schema_migrations")
            assert table_exists(connection, "app_metadata")
            assert table_exists(connection, "run_log")
    finally:
        _cleanup()


def test_metadata_upsert_round_trip() -> None:
    _cleanup()
    try:
        with connect(TEST_DB) as connection:
            upsert_metadata(connection, "test_key", "initial")
            assert fetch_metadata(connection, "test_key") == "initial"

            upsert_metadata(connection, "test_key", "updated")
            assert fetch_metadata(connection, "test_key") == "updated"
    finally:
        _cleanup()
