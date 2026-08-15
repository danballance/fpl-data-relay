"""Shared PostgreSQL schema constants and packaged migration resources."""

from importlib.resources import files

ADVISORY_LOCK_ID = 9_722_024_001

SCHEMA_SQL = (
    files("fpl_data_relay.adapters.outbound.postgres")
    .joinpath("migrations/0001_initial.sql")
    .read_text(encoding="utf-8")
)

ACCURATE_CHANGES_SQL = (
    files("fpl_data_relay.adapters.outbound.postgres")
    .joinpath("migrations/0002_accurate_changes.sql")
    .read_text(encoding="utf-8")
)

COMMUNITY_REPORTS_SQL = (
    files("fpl_data_relay.adapters.outbound.postgres")
    .joinpath("migrations/0003_community_reports.sql")
    .read_text(encoding="utf-8")
)

COMMUNITY_EXTRACTION_CACHE_SQL = (
    files("fpl_data_relay.adapters.outbound.postgres")
    .joinpath("migrations/0004_community_extraction_cache.sql")
    .read_text(encoding="utf-8")
)
