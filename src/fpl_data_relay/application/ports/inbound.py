"""Inbound use-case contracts used by driving adapters."""

from typing import Protocol

from fpl_data_relay.application.ingestion.service import IngestionResult


class IngestionRunner(Protocol):
    """Run one reference or live ingestion cycle."""

    async def ingest_reference_once(self) -> IngestionResult:
        """Run one reference ingestion cycle."""
        ...

    async def ingest_live_once(
        self,
        *,
        target_event_id: int | None,
        fixture_id: int | None,
    ) -> IngestionResult:
        """Run one live ingestion cycle."""
        ...
