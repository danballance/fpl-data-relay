"""Presentation-neutral facade for production administration clients."""

from collections.abc import Callable
from datetime import UTC, datetime

from fpl_data_relay.application.administration import (
    AdministrationService,
    ProductionStatus,
)
from fpl_data_relay.application.ports.administration import (
    AdministrationDoctorCheck,
    AdministrationDoctorResult,
    AdministrationDoctorScope,
    AdministrationJobDispatchResult,
    AdministrationJobKind,
    AdministrationProgressReporter,
    AdministrationReason,
    AdministrationSnapshot,
    AwsAdministration,
    AwsAdministrationSnapshot,
    AwsConnectionStatus,
    AwsDoctorResult,
    AwsIdentity,
    AwsResources,
    ChangeFeedRebaselineResult,
    DeadLetterMessage,
    DeadLetterPeekRequest,
    DeadLetterPeekResult,
    DeployedRevisionResult,
    GitShaRequest,
    MaintenanceWindow,
    NasAdministration,
    NasCollectorStatus,
    NasLogsRequest,
    NasLogsResult,
    ProductionAdministrationDatabase,
    QueueDepth,
    QueueDrainStage,
    ScheduleBootstrapSnapshot,
    ScheduleSnapshot,
    ScheduleStateFileRequest,
    SchemaStatus,
    require_aware_datetime,
)
from fpl_data_relay.application.schedule_bootstrap import (
    pause_schedules_to_snapshot,
    restore_schedules_from_snapshot,
)

AWS_DOCTOR_CHECKS = [
    AdministrationDoctorCheck.AWS_IDENTITY,
    AdministrationDoctorCheck.AWS_RESOURCES,
    AdministrationDoctorCheck.AWS_QUEUES,
    AdministrationDoctorCheck.AWS_SCHEDULES,
]
NAS_DOCTOR_CHECKS = [AdministrationDoctorCheck.NAS_CONTROL_PLANE]
PRODUCTION_DOCTOR_CHECKS = [
    *AWS_DOCTOR_CHECKS,
    *NAS_DOCTOR_CHECKS,
    AdministrationDoctorCheck.DATABASE_SCHEMA,
]

type AdministrationServiceFactory = Callable[[], AdministrationService]
type AwsAdministrationFactory = Callable[[], AwsAdministration]
type NasAdministrationFactory = Callable[[], NasAdministration]
type ProductionAdministrationDatabaseFactory = Callable[
    [],
    ProductionAdministrationDatabase,
]


class AdministrationFacade:
    """Expose typed administration operations without terminal presentation."""

    def __init__(
        self,
        *,
        service_factory: AdministrationServiceFactory,
        aws_factory: AwsAdministrationFactory,
        nas_factory: NasAdministrationFactory,
        database_factory: ProductionAdministrationDatabaseFactory,
        aws_profile: str,
        aws_region: str,
        app_stack_name: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._service_factory = service_factory
        self._aws_factory = aws_factory
        self._nas_factory = nas_factory
        self._database_factory = database_factory
        self._aws_profile = aws_profile
        self._aws_region = aws_region
        self._app_stack_name = app_stack_name
        self._clock = clock

    def connection_status(self) -> AwsConnectionStatus:
        """Verify and describe the configured profile-backed AWS connection."""
        return self._connection(identity=self._aws_factory().identity())

    def aws_doctor(self) -> AwsDoctorResult:
        """Validate every configured AWS control-plane dependency."""
        identity = self._service_factory().aws_doctor()
        return AwsDoctorResult(
            connection=self._connection(identity=identity),
            checks=AWS_DOCTOR_CHECKS,
            checked_at=self._now(),
        )

    def nas_doctor(self) -> AdministrationDoctorResult:
        """Validate every configured NAS control-plane dependency."""
        self._service_factory().nas_doctor()
        return AdministrationDoctorResult(
            scope=AdministrationDoctorScope.NAS,
            checks=NAS_DOCTOR_CHECKS,
            checked_at=self._now(),
        )

    async def production_doctor(self) -> AdministrationDoctorResult:
        """Validate AWS, NAS, and production database dependencies."""
        service = self._service_factory()
        service.aws_doctor()
        service.nas_doctor()
        await self._database_factory().schema_status()
        return AdministrationDoctorResult(
            scope=AdministrationDoctorScope.PRODUCTION,
            checks=PRODUCTION_DOCTOR_CHECKS,
            checked_at=self._now(),
        )

    def identity(self) -> AwsIdentity:
        """Return the verified AWS identity used by the facade."""
        return self._aws_factory().identity()

    def resources(self) -> AwsResources:
        """Return the resolved relay production resources."""
        aws = self._aws_factory()
        aws.identity()
        return aws.resources()

    def deployed_revision(self) -> DeployedRevisionResult:
        """Return the immutable application revision deployed in AWS."""
        aws = self._aws_factory()
        aws.identity()
        return DeployedRevisionResult(
            revision=aws.app_deployed_revision(),
            captured_at=self._now(),
        )

    async def snapshot(self) -> AdministrationSnapshot:
        """Capture one manually requested cross-system administration overview."""
        aws = self._aws_factory()
        identity = aws.identity()
        resources = aws.resources()
        revision = aws.app_deployed_revision()
        schema = await self._database_factory().schema_status()
        maintenance = await self._service_factory().latest_maintenance()
        queues = aws.queue_depths(include_dead_letters=True)
        schedules = aws.schedule_snapshots()
        collector = self._nas_factory().status()
        return AdministrationSnapshot(
            connection=self._connection(identity=identity),
            resources=resources,
            deployed_revision=revision,
            schema_status=schema,
            maintenance=maintenance,
            queues=queues,
            schedules=schedules,
            collector=collector,
            captured_at=self._now(),
        )

    async def aws_snapshot(self) -> AwsAdministrationSnapshot:
        """Capture one manually requested AWS-only administration overview."""
        aws = self._aws_factory()
        identity = aws.identity()
        resources = aws.resources()
        revision = aws.app_deployed_revision()
        schema = await self._database_factory().schema_status()
        maintenance = await self._service_factory().latest_maintenance()
        queues = aws.queue_depths(include_dead_letters=True)
        schedules = aws.schedule_snapshots()
        return AwsAdministrationSnapshot(
            connection=self._connection(identity=identity),
            resources=resources,
            deployed_revision=revision,
            schema_status=schema,
            maintenance=maintenance,
            queues=queues,
            schedules=schedules,
            captured_at=self._now(),
        )

    async def production_status(self) -> ProductionStatus:
        """Return the existing production status contract for CLI clients."""
        return await self._service_factory().production_status()

    async def schema_status(self) -> SchemaStatus:
        """Return the validated migration state for production."""
        self._aws_factory().identity()
        return await self._database_factory().schema_status()

    async def latest_maintenance(self) -> MaintenanceWindow | None:
        """Return the open or most recent maintenance window."""
        self._aws_factory().identity()
        return await self._service_factory().latest_maintenance()

    def queue_depths(self, *, include_dead_letters: bool) -> list[QueueDepth]:
        """Return complete working queue depths and optional DLQ depths."""
        aws = self._aws_factory()
        aws.identity()
        return aws.queue_depths(include_dead_letters=include_dead_letters)

    def dead_letter_depths(self) -> list[QueueDepth]:
        """Return only relay dead-letter queue depth samples."""
        return [
            depth
            for depth in self.queue_depths(include_dead_letters=True)
            if "dead-letter" in depth.name
        ]

    def schedule_snapshots(self) -> list[ScheduleSnapshot]:
        """Return every relay-owned schedule definition and state."""
        aws = self._aws_factory()
        aws.identity()
        return aws.schedule_snapshots()

    def peek_dead_letters(
        self,
        *,
        request: DeadLetterPeekRequest,
    ) -> DeadLetterPeekResult:
        """Return one bounded, non-destructive DLQ message view."""
        aws = self._aws_factory()
        aws.identity()
        bodies = aws.peek_dead_letters(
            queue_name=request.queue.value,
            max_messages=request.max_messages,
        )
        return DeadLetterPeekResult(
            queue=request.queue,
            requested_messages=request.max_messages,
            messages=[
                DeadLetterMessage(position=position, body=body)
                for position, body in enumerate(bodies, start=1)
            ],
            captured_at=self._now(),
        )

    async def apply_schema(self) -> SchemaStatus:
        """Apply and verify every pending production migration."""
        return await self._service_factory().apply_schema()

    async def send_reference(self) -> AdministrationJobDispatchResult:
        """Dispatch one explicit reference job outside maintenance."""
        self._aws_factory().identity()
        message_id = await self._service_factory().send_reference(
            allow_maintenance=False,
        )
        return self._dispatch_result(
            kind=AdministrationJobKind.REFERENCE,
            message_id=message_id,
        )

    async def send_current_live(self) -> AdministrationJobDispatchResult:
        """Dispatch one explicit current-event live job outside maintenance."""
        self._aws_factory().identity()
        message_id = await self._service_factory().send_current_live(
            allow_maintenance=False,
        )
        return self._dispatch_result(
            kind=AdministrationJobKind.LIVE,
            message_id=message_id,
        )

    async def send_community(self) -> AdministrationJobDispatchResult:
        """Dispatch one explicit community job outside maintenance."""
        self._aws_factory().identity()
        message_id = await self._service_factory().send_community(
            allow_maintenance=False,
        )
        return self._dispatch_result(
            kind=AdministrationJobKind.COMMUNITY,
            message_id=message_id,
        )

    def drain_queues(
        self,
        *,
        progress: AdministrationProgressReporter,
    ) -> list[QueueDepth]:
        """Wait for stable working-queue emptiness with typed samples."""
        self._aws_factory().identity()
        return self._service_factory().drain_queues_with_progress(
            stage=QueueDrainStage.STANDALONE,
            progress=progress,
        )

    async def pause_schedules(
        self,
        *,
        reason: AdministrationReason,
    ) -> MaintenanceWindow:
        """Open a maintenance record and pause schedules only."""
        self._aws_factory().identity()
        return await self._service_factory().pause_schedules(
            reason=reason.reason,
            collector_was_running=None,
        )

    async def restore_schedules(self) -> MaintenanceWindow:
        """Restore schedules and close the open maintenance record."""
        self._aws_factory().identity()
        return await self._service_factory().restore_schedules()

    def pause_schedules_to_state_file(
        self,
        *,
        request: ScheduleStateFileRequest,
    ) -> ScheduleBootstrapSnapshot:
        """Snapshot and disable schedules before schema bootstrap."""
        return pause_schedules_to_snapshot(
            aws=self._aws_factory(),
            snapshot_path=request.path,
            aws_region=self._aws_region,
            app_stack_name=self._app_stack_name,
            captured_at=self._now(),
        )

    def restore_schedules_from_state_file(
        self,
        *,
        request: ScheduleStateFileRequest,
    ) -> ScheduleBootstrapSnapshot:
        """Restore schedules captured before schema bootstrap."""
        return restore_schedules_from_snapshot(
            aws=self._aws_factory(),
            snapshot_path=request.path,
            aws_region=self._aws_region,
            app_stack_name=self._app_stack_name,
            restored_at=self._now(),
        )

    def collector_status(self) -> NasCollectorStatus:
        """Return the current NAS collector state."""
        return self._nas_factory().status()

    def collector_start(self) -> NasCollectorStatus:
        """Start the NAS collector and return its verified state."""
        return self._nas_factory().start()

    def collector_stop(self) -> NasCollectorStatus:
        """Stop the NAS collector and return its verified state."""
        return self._nas_factory().stop()

    def collector_update(self, *, request: GitShaRequest) -> NasCollectorStatus:
        """Activate one immutable collector image revision."""
        return self._nas_factory().update(image_tag=request.image_tag)

    def nas_logs(self, *, request: NasLogsRequest) -> NasLogsResult:
        """Return a bounded raw collector log tail."""
        return NasLogsResult(
            tail_lines=request.tail_lines,
            output=self._nas_factory().logs(tail_lines=request.tail_lines),
            captured_at=self._now(),
        )

    async def begin_production_maintenance(
        self,
        *,
        reason: AdministrationReason,
        progress: AdministrationProgressReporter,
    ) -> MaintenanceWindow:
        """Quiesce production with typed workflow progress."""
        self._aws_factory().identity()
        return await self._service_factory().begin_production_maintenance_with_progress(
            reason=reason.reason,
            progress=progress,
        )

    async def end_production_maintenance(
        self,
        *,
        progress: AdministrationProgressReporter,
    ) -> MaintenanceWindow:
        """Restore production with typed workflow progress."""
        self._aws_factory().identity()
        return await self._service_factory().end_production_maintenance_with_progress(
            progress=progress,
        )

    async def rebaseline_current(
        self,
        *,
        reason: AdministrationReason,
        refresh_normalized_data: bool,
        progress: AdministrationProgressReporter,
    ) -> ChangeFeedRebaselineResult:
        """Rebuild the current feed baseline with optional normalized refresh."""
        self._aws_factory().identity()
        return await self._service_factory().rebaseline_current_with_progress(
            reason=reason.reason,
            refresh_normalized_data=refresh_normalized_data,
            progress=progress,
        )

    def _dispatch_result(
        self,
        *,
        kind: AdministrationJobKind,
        message_id: str,
    ) -> AdministrationJobDispatchResult:
        return AdministrationJobDispatchResult(
            kind=kind,
            message_id=message_id,
            dispatched_at=self._now(),
        )

    def _connection(self, *, identity: AwsIdentity) -> AwsConnectionStatus:
        return AwsConnectionStatus(
            profile_name=self._aws_profile,
            region=self._aws_region,
            account_id=identity.account_id,
            arn=identity.arn,
        )

    def _now(self) -> datetime:
        return require_aware_datetime(self._clock()).astimezone(UTC)
