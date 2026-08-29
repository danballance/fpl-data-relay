import json
from datetime import UTC, datetime
from typing import cast

import pytest

from fpl_data_relay.adapters.outbound.postgres.connection import PoolProtocol
from fpl_data_relay.adapters.outbound.postgres.database import (
    IngestionLockError,
    PostgresDatabase,
    maintenance_window_from_row,
)
from fpl_data_relay.application.ports.administration import (
    MaintenancePhase,
    QueueDepth,
    ScheduleSnapshot,
    ScheduleState,
    ScheduleTargetSnapshot,
)

NOW = datetime(2026, 8, 24, tzinfo=UTC)


def queue() -> QueueDepth:
    return QueueDepth(
        name="fetch",
        url="https://sqs/fetch",
        visible=0,
        in_flight=0,
        delayed=0,
    )


def schedule() -> ScheduleSnapshot:
    return ScheduleSnapshot(
        name="reference",
        group_name="reference",
        state=ScheduleState.ENABLED,
        schedule_expression="cron(0/15 * * * ? *)",
        schedule_expression_timezone="UTC",
        flexible_window_mode="OFF",
        action_after_completion=None,
        description=None,
        target=ScheduleTargetSnapshot(
            arn="arn:queue",
            role_arn="arn:role",
            input='{"version":1,"kind":"reference"}',
            dead_letter_arn="arn:dlq",
            maximum_event_age_seconds=900,
            maximum_retry_attempts=3,
        ),
    )


class FakeTransaction:
    async def __aenter__(self) -> object:
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback


class FakeMaintenanceConnection:
    def __init__(self) -> None:
        self.row: dict[str, object] | None = None
        self.lock_available = True
        self.invalid_active: object | None = None
        self.force_existing = False

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()

    async def execute(self, query: str, *arguments: object) -> str:
        del query, arguments
        return "OK"

    async def fetchval(self, query: str, *arguments: object) -> object:
        del arguments
        if "pg_try_advisory_xact_lock" in query:
            return self.lock_available
        if "SELECT COUNT(*)" in query:
            return int(self.force_existing or self.row is not None)
        if "SELECT EXISTS" in query:
            if self.invalid_active is not None:
                return self.invalid_active
            return self.row is not None and self.row["phase"] != "closed"
        raise AssertionError(query)

    async def fetch(self, query: str, *arguments: object) -> list[object]:
        del arguments
        if "WHERE phase <> 'closed'" not in query:
            raise AssertionError(query)
        if self.row is None or self.row["phase"] == "closed":
            return []
        return [self.row]

    async def fetchrow(
        self,
        query: str,
        *arguments: object,
    ) -> dict[str, object] | None:
        if "INSERT INTO relay_maintenance_windows" in query:
            self.row = {
                "id": 1,
                "reason": arguments[0],
                "operator_arn": arguments[1],
                "phase": "entering",
                "schedules": arguments[2],
                "queues_before": arguments[3],
                "collector_was_running": arguments[4],
                "queues_after": "[]",
                "started_at": NOW,
                "activated_at": None,
                "closed_at": None,
                "closed_by": None,
            }
            return self.row
        if "SET phase = 'active'" in query:
            assert self.row is not None
            self.row.update(
                {
                    "phase": "active",
                    "collector_was_running": arguments[1],
                    "queues_after": arguments[2],
                    "activated_at": NOW,
                },
            )
            return self.row
        if "SET phase = 'exiting'" in query:
            assert self.row is not None
            self.row["phase"] = "exiting"
            return self.row
        if "SET phase = 'closed'" in query:
            assert self.row is not None
            self.row.update(
                {
                    "phase": "closed",
                    "closed_at": NOW,
                    "closed_by": arguments[1],
                },
            )
            return self.row
        if "ORDER BY id DESC" in query:
            return self.row
        raise AssertionError(query)


class FakeAcquire:
    def __init__(self, *, connection: FakeMaintenanceConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeMaintenanceConnection:
        return self.connection

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback


class FakePool:
    def __init__(self) -> None:
        self.connection = FakeMaintenanceConnection()

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(connection=self.connection)

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_postgres_maintenance_lifecycle_is_audited_and_queryable() -> None:
    pool = FakePool()
    database = PostgresDatabase(pool=cast("PoolProtocol", pool))
    assert await database.maintenance_active() is False
    assert await database.get_open_maintenance() is None
    assert await database.get_latest_maintenance() is None
    opened = await database.open_maintenance(
        reason="repair",
        operator_arn="arn:operator",
        schedules=[schedule()],
        queues_before=[queue()],
        collector_was_running=True,
    )
    assert opened.phase is MaintenancePhase.ENTERING
    assert opened.schedules == [schedule()]
    assert await database.maintenance_active() is True
    assert await database.get_open_maintenance() == opened
    active = await database.activate_maintenance(
        maintenance_id=opened.id,
        collector_was_running=True,
        queues_after=[queue()],
    )
    assert active.phase is MaintenancePhase.ACTIVE
    exiting = await database.begin_maintenance_exit(maintenance_id=opened.id)
    assert exiting.phase is MaintenancePhase.EXITING
    closed = await database.close_maintenance(
        maintenance_id=opened.id,
        operator_arn="arn:closer",
    )
    assert closed.phase is MaintenancePhase.CLOSED
    assert closed.closed_by == "arn:closer"
    assert await database.maintenance_active() is False
    assert await database.get_open_maintenance() is None
    assert await database.get_latest_maintenance() == closed


@pytest.mark.asyncio
async def test_postgres_maintenance_rejects_lock_overlap_and_bad_results() -> None:
    pool = FakePool()
    database = PostgresDatabase(pool=cast("PoolProtocol", pool))
    pool.connection.lock_available = False
    with pytest.raises(IngestionLockError, match="already running"):
        await database.open_maintenance(
            reason="repair",
            operator_arn="arn:operator",
            schedules=[schedule()],
            queues_before=[queue()],
            collector_was_running=None,
        )
    pool.connection.lock_available = True
    pool.connection.force_existing = True
    with pytest.raises(RuntimeError, match="already open"):
        await database.open_maintenance(
            reason="repair",
            operator_arn="arn:operator",
            schedules=[schedule()],
            queues_before=[queue()],
            collector_was_running=None,
        )
    pool.connection.force_existing = False
    pool.connection.invalid_active = "yes"
    with pytest.raises(TypeError, match="boolean"):
        await database.maintenance_active()


def test_maintenance_row_parser_accepts_native_json_values() -> None:
    parsed = maintenance_window_from_row(
        row={
            "id": 1,
            "reason": "repair",
            "operator_arn": "arn:operator",
            "phase": "active",
            "schedules": json.loads(
                json.dumps([schedule().model_dump(mode="json")]),
            ),
            "collector_was_running": False,
            "queues_before": [queue().model_dump(mode="json")],
            "queues_after": [queue().model_dump(mode="json")],
            "started_at": NOW,
            "activated_at": NOW,
            "closed_at": None,
            "closed_by": None,
        },
    )
    assert parsed.collector_was_running is False
