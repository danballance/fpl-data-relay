"""Composition root for the local production administration CLI."""

import time
from datetime import UTC, datetime
from pathlib import Path

from fpl_data_relay.adapters.inbound.cli.admin import create_admin_app
from fpl_data_relay.adapters.outbound.aws_administration import (
    AwsBotoAdministration,
)
from fpl_data_relay.adapters.outbound.nas_administration import (
    NasSshAdministration,
)
from fpl_data_relay.adapters.outbound.postgres.database import PostgresDatabase
from fpl_data_relay.application.administration import AdministrationService
from fpl_data_relay.application.ports.administration import (
    AwsAdministration,
    NasAdministration,
    ProductionAdministrationDatabase,
)
from fpl_data_relay.config import AdminSettings, load_admin_settings


class ProductionAdminRuntime:
    """Own production administration adapters for one CLI invocation."""

    def __init__(
        self,
        *,
        settings: AdminSettings,
    ) -> None:
        self.settings = settings
        self._aws: AwsBotoAdministration | None = None
        self._nas: NasSshAdministration | None = None
        self._database: PostgresDatabase | None = None
        self._service: AdministrationService | None = None

    @property
    def aws(self) -> AwsAdministration:
        """Construct AWS control-plane clients from the configured profile."""
        return self._aws_adapter()

    @property
    def nas(self) -> NasAdministration:
        """Construct the NAS adapter only when a NAS command needs it."""
        if self._nas is None:
            self._nas = NasSshAdministration(settings=self.settings)
        return self._nas

    @property
    def database(self) -> ProductionAdministrationDatabase:
        """Resolve database resources only for commands that use the database."""
        if self._database is None:
            self._database = self._aws_adapter().database()
        return self._database

    @property
    def service(self) -> AdministrationService:
        """Construct the composed service on first operational use."""
        if self._service is None:
            self._service = AdministrationService(
                aws=self._aws_adapter(),
                nas=self.nas,
                database=self.database,
                drain_timeout_seconds=self.settings.drain_timeout_seconds,
                drain_poll_seconds=self.settings.drain_poll_seconds,
                drain_stable_seconds=self.settings.drain_stable_seconds,
                monotonic_clock=time.monotonic,
                sleeper=time.sleep,
                clock=lambda: datetime.now(tz=UTC),
            )
        return self._service

    def _aws_adapter(self) -> AwsBotoAdministration:
        if self._aws is None:
            self._aws = AwsBotoAdministration(settings=self.settings)
        return self._aws


def build_admin_runtime(path: Path) -> ProductionAdminRuntime:
    """Build an explicit AWS-profile and SSH-backed administration runtime."""
    settings = load_admin_settings(path=path)
    return ProductionAdminRuntime(settings=settings)


app = create_admin_app(runtime_factory=build_admin_runtime)
