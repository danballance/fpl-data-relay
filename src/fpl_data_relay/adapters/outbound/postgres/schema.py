"""Shared PostgreSQL schema constants and packaged migration resources."""

from importlib.resources import files

ADVISORY_LOCK_ID = 9_722_024_001

SCHEMA_SQL = (
    files("fpl_data_relay.adapters.outbound.postgres")
    .joinpath("migrations/0001_initial.sql")
    .read_text(encoding="utf-8")
)
