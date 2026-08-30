"""Safe production administration workflows."""

import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict

from fpl_data_relay.application.community_jobs import CommunityDispatchJob
from fpl_data_relay.application.database import SCHEMA_VERSION
from fpl_data_relay.application.jobs import (
    WINDOW_BEFORE_KICKOFF,
    LiveJob,
    ReferenceJob,
)
from fpl_data_relay.application.ports.administration import (
    AdministrationProgressReporter,
    AdministrationReason,
    AdministrationWorkflow,
    AdministrationWorkflowProgress,
    AdministrationWorkflowStep,
    AdministrationWorkflowStepState,
    AwsAdministration,
    AwsIdentity,
    ChangeFeedRebaselineResult,
    MaintenancePhase,
    MaintenanceWindow,
    NasAdministration,
    NasCollectorStatus,
    ProductionAdministrationDatabase,
    QueueDepth,
    QueueDrainProgress,
    QueueDrainStage,
    ScheduleSnapshot,
    ScheduleState,
    SchemaStatus,
)
from fpl_data_relay.domain.rules import LIVE_WINDOW_AFTER_KICKOFF

PAGE_SIZE = 200
LIVE_SCHEDULE_PREFIX = "fpl-live-"
AT_EXPRESSION = re.compile(r"^at\((\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\)$")


def discard_administration_progress(event: object) -> None:
    """Discard progress for callers which only need the final result."""
    del event


class ProductionStatus(BaseModel):
    """Combined status displayed by the administrator CLI."""

    model_config = ConfigDict(frozen=True)

    schema_status: SchemaStatus
    maintenance: MaintenanceWindow | None
    queues: list[QueueDepth]
    schedules: list[ScheduleSnapshot]
    collector: NasCollectorStatus


class AdministrationService:
    """Coordinate explicit AWS, NAS, and production administration actions."""

    def __init__(
        self,
        *,
        aws: AwsAdministration,
        nas: NasAdministration,
        database: ProductionAdministrationDatabase,
        drain_timeout_seconds: int,
        drain_poll_seconds: int,
        drain_stable_seconds: int,
        monotonic_clock: Callable[[], float],
        sleeper: Callable[[float], None],
        clock: Callable[[], datetime],
    ) -> None:
        self._aws = aws
        self._nas = nas
        self._database = database
        self._drain_timeout_seconds = drain_timeout_seconds
        self._drain_poll_seconds = drain_poll_seconds
        self._drain_stable_seconds = drain_stable_seconds
        self._monotonic_clock = monotonic_clock
        self._sleeper = sleeper
        self._clock = clock

    def aws_doctor(self) -> AwsIdentity:
        """Validate the configured AWS control plane without mutation."""
        identity = self._aws.identity()
        self._aws.resources()
        self._aws.queue_depths(include_dead_letters=True)
        self._aws.schedule_snapshots()
        return identity

    def nas_doctor(self) -> None:
        """Validate the configured NAS control plane without mutation."""
        self._nas.doctor()

    async def production_status(self) -> ProductionStatus:
        """Read a coherent cross-system status summary."""
        self._aws.identity()
        return ProductionStatus(
            schema_status=await self._database.schema_status(),
            maintenance=await self._database.get_open_maintenance(),
            queues=self._aws.queue_depths(include_dead_letters=True),
            schedules=self._aws.schedule_snapshots(),
            collector=self._nas.status(),
        )

    async def latest_maintenance(self) -> MaintenanceWindow | None:
        """Return the open or most recent maintenance record."""
        open_window = await self._database.get_open_maintenance()
        if open_window is not None:
            return open_window
        return await self._database.get_latest_maintenance()

    async def apply_schema(self) -> SchemaStatus:
        """Apply and verify every pending production migration."""
        self._aws.identity()
        await self._database.apply_schema()
        await self._database.check_schema_version(expected_version=SCHEMA_VERSION)
        return await self._database.schema_status()

    async def send_reference(self, *, allow_maintenance: bool) -> str:
        """Send one strict reference job."""
        await self._require_send_allowed(allow_maintenance=allow_maintenance)
        return self._aws.send_fetch_job(
            message_body=ReferenceJob(version=1, kind="reference").model_dump_json(),
        )

    async def send_current_live(self, *, allow_maintenance: bool) -> str:
        """Send one strict current-event live job derived from normalized data."""
        await self._require_send_allowed(allow_maintenance=allow_maintenance)
        return self._aws.send_fetch_job(
            message_body=(await self._current_live_job()).model_dump_json(),
        )

    async def send_community(self, *, allow_maintenance: bool) -> str:
        """Send one strict community dispatch job for the current UTC instant."""
        await self._require_send_allowed(allow_maintenance=allow_maintenance)
        scheduled_at = self._aware_now()
        return self._aws.send_community_job(
            message_body=CommunityDispatchJob(
                version=1,
                kind="community_dispatch",
                scheduled_at=scheduled_at,
            ).model_dump_json(),
        )

    def drain_queues(self) -> list[QueueDepth]:
        """Wait until all working queues are stably empty."""
        return self.drain_queues_with_progress(
            stage=QueueDrainStage.STANDALONE,
            progress=discard_administration_progress,
        )

    def drain_queues_with_progress(
        self,
        *,
        stage: QueueDrainStage,
        progress: AdministrationProgressReporter,
    ) -> list[QueueDepth]:
        """Wait for stable emptiness while reporting every complete sample."""
        started_at = self._monotonic_clock()
        zero_since: float | None = None
        while True:
            depths = self._aws.queue_depths(include_dead_letters=False)
            now = self._monotonic_clock()
            if depths and all(depth.total == 0 for depth in depths):
                if zero_since is None:
                    zero_since = now
            else:
                zero_since = None
            stable_for = 0.0 if zero_since is None else now - zero_since
            progress(
                QueueDrainProgress(
                    stage=stage,
                    queues=depths,
                    elapsed_seconds=now - started_at,
                    stable_for_seconds=stable_for,
                    required_stable_seconds=self._drain_stable_seconds,
                    sampled_at=self._aware_now(),
                ),
            )
            if zero_since is not None and stable_for >= self._drain_stable_seconds:
                return depths
            if now - started_at >= self._drain_timeout_seconds:
                totals = ", ".join(
                    f"{depth.name}={depth.total}" for depth in depths
                )
                raise TimeoutError(f"Queues did not drain before timeout: {totals}")
            self._sleeper(float(self._drain_poll_seconds))

    async def pause_schedules(
        self,
        *,
        reason: str,
        collector_was_running: bool | None,
    ) -> MaintenanceWindow:
        """Open maintenance and disable every relay-owned schedule."""
        normalized_reason = AdministrationReason(reason=reason).reason
        existing = await self._database.get_open_maintenance()
        if existing is None:
            identity = self._aws.identity()
            schedules = self._aws.schedule_snapshots()
            queues_before = self._aws.queue_depths(include_dead_letters=False)
            window = await self._database.open_maintenance(
                reason=normalized_reason,
                operator_arn=identity.arn,
                schedules=schedules,
                queues_before=queues_before,
                collector_was_running=collector_was_running,
            )
        else:
            if existing.phase is not MaintenancePhase.ENTERING:
                raise RuntimeError(
                    f"Maintenance window {existing.id} is {existing.phase.value}.",
                )
            if existing.reason != normalized_reason:
                raise RuntimeError(
                    "Open maintenance reason does not match this retry.",
                )
            window = existing
        for schedule in window.schedules:
            self._aws.set_schedule_state(
                schedule=schedule,
                state=ScheduleState.DISABLED,
                schedule_expression=schedule.schedule_expression,
            )
        return window

    async def activate_maintenance(
        self,
        *,
        collector_was_running: bool,
    ) -> MaintenanceWindow:
        """Mark the open entering window active after stable quiescence."""
        window = await self._require_open_phase(MaintenancePhase.ENTERING)
        depths = self._aws.queue_depths(include_dead_letters=False)
        if any(depth.total != 0 for depth in depths):
            raise RuntimeError("Cannot activate maintenance while queues are nonempty.")
        recorded_state = window.collector_was_running
        if recorded_state is not None and recorded_state != collector_was_running:
            raise RuntimeError("Collector state differs from the recorded begin state.")
        return await self._database.activate_maintenance(
            maintenance_id=window.id,
            collector_was_running=collector_was_running,
            queues_after=depths,
        )

    async def begin_production_maintenance(
        self,
        *,
        reason: str,
    ) -> MaintenanceWindow:
        """Quiesce AWS writers, drain work, and stop the NAS collector."""
        return await self.begin_production_maintenance_with_progress(
            reason=reason,
            progress=discard_administration_progress,
        )

    async def begin_production_maintenance_with_progress(
        self,
        *,
        reason: str,
        progress: AdministrationProgressReporter,
    ) -> MaintenanceWindow:
        """Begin maintenance and report each recoverable workflow step."""
        workflow = AdministrationWorkflow.BEGIN_MAINTENANCE
        self._run_progress_step(
            workflow=workflow,
            step=AdministrationWorkflowStep.CHECK_DEAD_LETTERS,
            progress=progress,
            operation=self._ensure_dead_letters_empty,
        )
        collector = self._run_progress_step(
            workflow=workflow,
            step=AdministrationWorkflowStep.READ_COLLECTOR,
            progress=progress,
            operation=self._nas.status,
        )
        window = await self._run_async_progress_step(
            workflow=workflow,
            step=AdministrationWorkflowStep.PAUSE_SCHEDULES,
            progress=progress,
            operation=lambda: self.pause_schedules(
                reason=reason,
                collector_was_running=collector.running,
            ),
        )
        self._run_progress_step(
            workflow=workflow,
            step=AdministrationWorkflowStep.DRAIN_BEFORE_COLLECTOR_STOP,
            progress=progress,
            operation=lambda: self.drain_queues_with_progress(
                stage=QueueDrainStage.BEFORE_COLLECTOR_STOP,
                progress=progress,
            ),
        )
        if collector.running:
            self._run_progress_step(
                workflow=workflow,
                step=AdministrationWorkflowStep.STOP_COLLECTOR,
                progress=progress,
                operation=self._stop_collector,
            )
        else:
            self._skip_progress_step(
                workflow=workflow,
                step=AdministrationWorkflowStep.STOP_COLLECTOR,
                detail="collector was already stopped",
                progress=progress,
            )
        self._run_progress_step(
            workflow=workflow,
            step=AdministrationWorkflowStep.DRAIN_AFTER_COLLECTOR_STOP,
            progress=progress,
            operation=lambda: self.drain_queues_with_progress(
                stage=QueueDrainStage.AFTER_COLLECTOR_STOP,
                progress=progress,
            ),
        )
        collector_was_running = (
            collector.running
            if window.collector_was_running is None
            else window.collector_was_running
        )
        return await self._run_async_progress_step(
            workflow=workflow,
            step=AdministrationWorkflowStep.ACTIVATE_MAINTENANCE,
            progress=progress,
            operation=lambda: self.activate_maintenance(
                collector_was_running=collector_was_running,
            ),
        )

    async def end_production_maintenance(self) -> MaintenanceWindow:
        """Restore the collector and schedules, then close maintenance."""
        return await self.end_production_maintenance_with_progress(
            progress=discard_administration_progress,
        )

    async def end_production_maintenance_with_progress(
        self,
        *,
        progress: AdministrationProgressReporter,
    ) -> MaintenanceWindow:
        """End maintenance and report each recoverable workflow step."""
        workflow = AdministrationWorkflow.END_MAINTENANCE
        window = await self._run_async_progress_step(
            workflow=workflow,
            step=AdministrationWorkflowStep.CHECK_MAINTENANCE,
            progress=progress,
            operation=lambda: self._require_open_phase(
                MaintenancePhase.ACTIVE,
                MaintenancePhase.EXITING,
            ),
        )
        self._run_progress_step(
            workflow=workflow,
            step=AdministrationWorkflowStep.CHECK_DEAD_LETTERS,
            progress=progress,
            operation=self._ensure_dead_letters_empty,
        )
        self._run_progress_step(
            workflow=workflow,
            step=AdministrationWorkflowStep.CHECK_QUEUES,
            progress=progress,
            operation=self._require_working_queues_empty_for_end,
        )
        if window.phase is MaintenancePhase.ACTIVE:
            window = await self._run_async_progress_step(
                workflow=workflow,
                step=AdministrationWorkflowStep.BEGIN_EXIT,
                progress=progress,
                operation=lambda: self._database.begin_maintenance_exit(
                    maintenance_id=window.id,
                ),
            )
        else:
            self._skip_progress_step(
                workflow=workflow,
                step=AdministrationWorkflowStep.BEGIN_EXIT,
                detail="maintenance was already exiting",
                progress=progress,
            )
        if window.collector_was_running is None:
            raise RuntimeError("Maintenance has no recorded collector state.")
        if window.collector_was_running:
            self._run_progress_step(
                workflow=workflow,
                step=AdministrationWorkflowStep.START_COLLECTOR,
                progress=progress,
                operation=self._start_collector,
            )
        else:
            self._skip_progress_step(
                workflow=workflow,
                step=AdministrationWorkflowStep.START_COLLECTOR,
                detail="collector was stopped before maintenance",
                progress=progress,
            )
        closed = await self._run_async_progress_step(
            workflow=workflow,
            step=AdministrationWorkflowStep.RESTORE_SCHEDULES,
            progress=progress,
            operation=lambda: self._restore_window(window=window),
        )
        if closed.collector_was_running and any(
            schedule.name.endswith("-reference-quarter-hour")
            and schedule.state is ScheduleState.ENABLED
            for schedule in closed.schedules
        ):
            await self._run_async_progress_step(
                workflow=workflow,
                step=AdministrationWorkflowStep.SEND_REFERENCE,
                progress=progress,
                operation=lambda: self.send_reference(allow_maintenance=False),
            )
        else:
            self._skip_progress_step(
                workflow=workflow,
                step=AdministrationWorkflowStep.SEND_REFERENCE,
                detail="restored state does not require a reference job",
                progress=progress,
            )
        return closed

    async def restore_schedules(self) -> MaintenanceWindow:
        """Restore only AWS schedules and close the maintenance record."""
        window = await self._require_open_phase(
            MaintenancePhase.ENTERING,
            MaintenancePhase.ACTIVE,
            MaintenancePhase.EXITING,
        )
        depths = self._aws.queue_depths(include_dead_letters=False)
        if any(depth.total != 0 for depth in depths):
            raise RuntimeError("Cannot restore schedules while queues are nonempty.")
        if window.phase is not MaintenancePhase.EXITING:
            window = await self._database.begin_maintenance_exit(
                maintenance_id=window.id,
            )
        return await self._restore_window(window=window)

    async def rebaseline_current(
        self,
        *,
        reason: str,
        refresh_normalized_data: bool,
    ) -> ChangeFeedRebaselineResult:
        """Optionally refresh, then replace the current change-feed baseline."""
        return await self.rebaseline_current_with_progress(
            reason=reason,
            refresh_normalized_data=refresh_normalized_data,
            progress=discard_administration_progress,
        )

    async def rebaseline_current_with_progress(
        self,
        *,
        reason: str,
        refresh_normalized_data: bool,
        progress: AdministrationProgressReporter,
    ) -> ChangeFeedRebaselineResult:
        """Rebaseline normalized state while reporting every workflow step."""
        normalized_reason = AdministrationReason(reason=reason).reason
        workflow = AdministrationWorkflow.REBASELINE
        await self._run_async_progress_step(
            workflow=workflow,
            step=AdministrationWorkflowStep.CHECK_MAINTENANCE,
            progress=progress,
            operation=lambda: self._require_open_phase(MaintenancePhase.ACTIVE),
        )
        self._run_progress_step(
            workflow=workflow,
            step=AdministrationWorkflowStep.CHECK_DEAD_LETTERS,
            progress=progress,
            operation=self._ensure_dead_letters_empty,
        )
        if refresh_normalized_data:
            self._run_progress_step(
                workflow=workflow,
                step=AdministrationWorkflowStep.START_COLLECTOR,
                progress=progress,
                operation=self._start_collector_for_rebaseline,
            )
            try:
                await self._run_async_progress_step(
                    workflow=workflow,
                    step=AdministrationWorkflowStep.SEND_REFERENCE,
                    progress=progress,
                    operation=lambda: self.send_reference(allow_maintenance=True),
                )
                self._run_progress_step(
                    workflow=workflow,
                    step=AdministrationWorkflowStep.DRAIN_AFTER_REFERENCE,
                    progress=progress,
                    operation=lambda: self.drain_queues_with_progress(
                        stage=QueueDrainStage.AFTER_REFERENCE,
                        progress=progress,
                    ),
                )
                await self._run_async_progress_step(
                    workflow=workflow,
                    step=AdministrationWorkflowStep.SEND_LIVE,
                    progress=progress,
                    operation=lambda: self.send_current_live(
                        allow_maintenance=True,
                    ),
                )
                self._run_progress_step(
                    workflow=workflow,
                    step=AdministrationWorkflowStep.DRAIN_AFTER_LIVE,
                    progress=progress,
                    operation=lambda: self.drain_queues_with_progress(
                        stage=QueueDrainStage.AFTER_LIVE,
                        progress=progress,
                    ),
                )
            finally:
                self._run_progress_step(
                    workflow=workflow,
                    step=AdministrationWorkflowStep.STOP_COLLECTOR,
                    progress=progress,
                    operation=self._stop_collector_after_rebaseline,
                )
        else:
            self._skip_progress_step(
                workflow=workflow,
                step=AdministrationWorkflowStep.START_COLLECTOR,
                detail="normalized data refresh was not requested",
                progress=progress,
            )
        self._run_progress_step(
            workflow=workflow,
            step=AdministrationWorkflowStep.DRAIN_BEFORE_REBASELINE,
            progress=progress,
            operation=lambda: self.drain_queues_with_progress(
                stage=QueueDrainStage.BEFORE_REBASELINE,
                progress=progress,
            ),
        )
        return await self._run_async_progress_step(
            workflow=workflow,
            step=AdministrationWorkflowStep.REBUILD_BASELINE,
            progress=progress,
            operation=lambda: self._database.rebaseline_current_change_feed(
                reason=normalized_reason,
            ),
        )

    def _stop_collector(self) -> NasCollectorStatus:
        stopped = self._nas.stop()
        if stopped.running:
            raise RuntimeError("NAS collector remained running after stop.")
        return stopped

    def _start_collector(self) -> NasCollectorStatus:
        collector = self._nas.start()
        if not collector.running:
            raise RuntimeError("NAS collector did not start.")
        return collector

    def _start_collector_for_rebaseline(self) -> NasCollectorStatus:
        collector = self._nas.start()
        if not collector.running:
            raise RuntimeError("NAS collector did not start for baseline refresh.")
        return collector

    def _stop_collector_after_rebaseline(self) -> NasCollectorStatus:
        stopped = self._nas.stop()
        if stopped.running:
            raise RuntimeError("NAS collector remained running after refresh.")
        return stopped

    def _require_working_queues_empty_for_end(self) -> list[QueueDepth]:
        depths = self._aws.queue_depths(include_dead_letters=False)
        if any(depth.total != 0 for depth in depths):
            raise RuntimeError("Cannot end maintenance while queues are nonempty.")
        return depths

    def _run_progress_step[Result](
        self,
        *,
        workflow: AdministrationWorkflow,
        step: AdministrationWorkflowStep,
        progress: AdministrationProgressReporter,
        operation: Callable[[], Result],
    ) -> Result:
        self._emit_progress_step(
            workflow=workflow,
            step=step,
            state=AdministrationWorkflowStepState.STARTED,
            detail=None,
            progress=progress,
        )
        try:
            result = operation()
        except Exception as error:
            self._emit_progress_step(
                workflow=workflow,
                step=step,
                state=AdministrationWorkflowStepState.FAILED,
                detail=str(error),
                progress=progress,
            )
            raise
        self._emit_progress_step(
            workflow=workflow,
            step=step,
            state=AdministrationWorkflowStepState.COMPLETED,
            detail=None,
            progress=progress,
        )
        return result

    async def _run_async_progress_step[Result](
        self,
        *,
        workflow: AdministrationWorkflow,
        step: AdministrationWorkflowStep,
        progress: AdministrationProgressReporter,
        operation: Callable[[], Awaitable[Result]],
    ) -> Result:
        self._emit_progress_step(
            workflow=workflow,
            step=step,
            state=AdministrationWorkflowStepState.STARTED,
            detail=None,
            progress=progress,
        )
        try:
            result = await operation()
        except Exception as error:
            self._emit_progress_step(
                workflow=workflow,
                step=step,
                state=AdministrationWorkflowStepState.FAILED,
                detail=str(error),
                progress=progress,
            )
            raise
        self._emit_progress_step(
            workflow=workflow,
            step=step,
            state=AdministrationWorkflowStepState.COMPLETED,
            detail=None,
            progress=progress,
        )
        return result

    def _skip_progress_step(
        self,
        *,
        workflow: AdministrationWorkflow,
        step: AdministrationWorkflowStep,
        detail: str,
        progress: AdministrationProgressReporter,
    ) -> None:
        self._emit_progress_step(
            workflow=workflow,
            step=step,
            state=AdministrationWorkflowStepState.SKIPPED,
            detail=detail,
            progress=progress,
        )

    def _emit_progress_step(
        self,
        *,
        workflow: AdministrationWorkflow,
        step: AdministrationWorkflowStep,
        state: AdministrationWorkflowStepState,
        detail: str | None,
        progress: AdministrationProgressReporter,
    ) -> None:
        progress(
            AdministrationWorkflowProgress(
                workflow=workflow,
                step=step,
                state=state,
                occurred_at=self._aware_now(),
                detail=detail,
            ),
        )

    async def _current_live_job(self) -> LiveJob:
        season = await self._database.get_current_season()
        if season is None:
            raise RuntimeError("No current normalized season exists.")
        event = await self._database.get_current_event(season_id=season.id)
        if event is None:
            raise RuntimeError("No current normalized event exists.")
        fixtures = []
        after_id = 0
        while True:
            page = await self._database.list_fixtures(
                season_id=season.id,
                event_id=event.id,
                after_id=after_id,
                limit=PAGE_SIZE,
            )
            fixtures.extend(page)
            if len(page) < PAGE_SIZE:
                break
            next_id = page[-1].id
            if next_id <= after_id:
                raise RuntimeError("Fixture pagination did not advance.")
            after_id = next_id
        kickoff_times = [fixture.kickoff_time for fixture in fixtures]
        if not kickoff_times or any(value is None for value in kickoff_times):
            raise RuntimeError("Current event fixtures lack complete kickoff times.")
        concrete_times = [value for value in kickoff_times if value is not None]
        return LiveJob(
            version=1,
            kind="live",
            season_id=season.id,
            event_id=event.id,
            window_start=min(concrete_times) - WINDOW_BEFORE_KICKOFF,
            window_end=max(concrete_times) + LIVE_WINDOW_AFTER_KICKOFF,
        )

    async def _restore_window(
        self,
        *,
        window: MaintenanceWindow,
    ) -> MaintenanceWindow:
        now = self._aware_now()
        for schedule in window.schedules:
            state, expression = restored_schedule(schedule=schedule, now=now)
            self._aws.set_schedule_state(
                schedule=schedule,
                state=state,
                schedule_expression=expression,
            )
        identity = self._aws.identity()
        return await self._database.close_maintenance(
            maintenance_id=window.id,
            operator_arn=identity.arn,
        )

    async def _require_send_allowed(self, *, allow_maintenance: bool) -> None:
        if await self._database.maintenance_active() and not allow_maintenance:
            raise RuntimeError("Queue sends are blocked during maintenance.")

    async def _require_open_phase(
        self,
        *phases: MaintenancePhase,
    ) -> MaintenanceWindow:
        window = await self._database.get_open_maintenance()
        if window is None:
            raise RuntimeError("No maintenance window is open.")
        if window.phase not in phases:
            allowed = ", ".join(phase.value for phase in phases)
            raise RuntimeError(
                f"Maintenance window {window.id} is {window.phase.value}; "
                f"expected {allowed}.",
            )
        return window

    def _ensure_dead_letters_empty(self) -> None:
        dead_letters = [
            depth
            for depth in self._aws.queue_depths(include_dead_letters=True)
            if "dead-letter" in depth.name and depth.total != 0
        ]
        if dead_letters:
            summary = ", ".join(
                f"{depth.name}={depth.total}" for depth in dead_letters
            )
            raise RuntimeError(f"Dead-letter queues require review: {summary}")

    def _aware_now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Administration clock must be timezone-aware.")
        return now.astimezone(UTC)


def restored_schedule(
    *,
    schedule: ScheduleSnapshot,
    now: datetime,
) -> tuple[ScheduleState, str]:
    """Restore recurring state and safely handle elapsed one-time schedules."""
    if not schedule.name.startswith(LIVE_SCHEDULE_PREFIX):
        return schedule.state, schedule.schedule_expression
    match = AT_EXPRESSION.fullmatch(schedule.schedule_expression)
    if match is None:
        raise ValueError(
            f"Live schedule {schedule.name} does not use an at expression.",
        )
    trigger = datetime.fromisoformat(match.group(1)).replace(tzinfo=UTC)
    if schedule.state is ScheduleState.DISABLED or trigger > now:
        return schedule.state, schedule.schedule_expression
    job = LiveJob.model_validate_json(schedule.target.input)
    if job.window_end <= now:
        return ScheduleState.DISABLED, schedule.schedule_expression
    catchup = (now + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")
    return ScheduleState.ENABLED, f"at({catchup})"
