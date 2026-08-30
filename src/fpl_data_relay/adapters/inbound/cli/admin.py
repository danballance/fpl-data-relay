"""Typer command surface for local production administration."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Protocol, assert_never, cast

import typer

from fpl_data_relay.application.administration import AdministrationService
from fpl_data_relay.application.administration_facade import AdministrationFacade
from fpl_data_relay.application.ports.administration import (
    AdministrationProgress,
    AdministrationReason,
    AdministrationWorkflowProgress,
    AwsAdministration,
    DeadLetterPeekRequest,
    DeadLetterQueueName,
    GitShaRequest,
    MaintenanceWindow,
    NasAdministration,
    NasLogsRequest,
    ProductionAdministrationDatabase,
    QueueDepth,
    QueueDrainProgress,
    ScheduleSnapshot,
    ScheduleStateFileRequest,
    SchemaStatus,
)
from fpl_data_relay.config import AdminSettings

PRODUCTION_CONFIRMATION = "production"


class AdminRuntime(Protocol):
    """Owned operations supplied by the administration composition root."""

    settings: AdminSettings
    service: AdministrationService
    aws: AwsAdministration
    nas: NasAdministration
    database: ProductionAdministrationDatabase


type AdminRuntimeFactory = Callable[[Path], AdminRuntime]


def build_administration_facade(*, runtime: AdminRuntime) -> AdministrationFacade:
    """Build a presentation-neutral façade around one injected CLI runtime."""
    return AdministrationFacade(
        service_factory=lambda: runtime.service,
        aws_factory=lambda: runtime.aws,
        nas_factory=lambda: runtime.nas,
        database_factory=lambda: runtime.database,
        aws_profile=runtime.settings.aws_profile,
        aws_region=runtime.settings.aws_region,
        app_stack_name=runtime.settings.app_stack_name,
        clock=lambda: datetime.now(tz=UTC),
    )


def create_admin_app(*, runtime_factory: AdminRuntimeFactory) -> typer.Typer:
    """Create the administration CLI around injected production operations."""
    app = typer.Typer(no_args_is_help=True)
    aws_app = typer.Typer(no_args_is_help=True)
    nas_app = typer.Typer(no_args_is_help=True)
    prod_app = typer.Typer(no_args_is_help=True)
    app.add_typer(aws_app, name="aws")
    app.add_typer(nas_app, name="nas")
    app.add_typer(prod_app, name="prod")

    @app.callback()
    def configure(
        context: typer.Context,
        config: Annotated[
            Path,
            typer.Option(
                "--config",
                help="Explicit non-secret administration environment file.",
            ),
        ],
    ) -> None:
        context.obj = runtime_factory(config)

    @aws_app.command("doctor")
    def aws_doctor(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        result = build_administration_facade(runtime=runtime).aws_doctor()
        connection = result.connection
        typer.echo(
            f"profile={connection.profile_name} region={connection.region} "
            f"account={connection.account_id} operator={connection.arn}",
        )
        typer.echo("AWS administration checks passed")

    @aws_app.command("app-revision")
    def aws_app_revision(context: typer.Context) -> None:
        """Show the immutable revision exposed by the application stack."""
        runtime = admin_runtime(context=context)
        revision = build_administration_facade(runtime=runtime).deployed_revision()
        typer.echo(f"deployed_revision={revision.revision}")

    @aws_app.command("status")
    def aws_status(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        snapshot = asyncio.run(
            build_administration_facade(runtime=runtime).aws_snapshot(),
        )
        typer.echo(
            f"profile={snapshot.connection.profile_name} "
            f"region={snapshot.connection.region} "
            f"account={snapshot.connection.account_id} "
            f"operator={snapshot.connection.arn}",
        )
        echo_schema(status=snapshot.schema_status)
        echo_maintenance(window=snapshot.maintenance)
        echo_queues(depths=snapshot.queues)
        echo_schedules(schedules=snapshot.schedules)

    @aws_app.command("db-status")
    def aws_db_status(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        facade = build_administration_facade(runtime=runtime)
        echo_schema(status=asyncio.run(facade.schema_status()))

    @aws_app.command("db-migrate")
    def aws_db_migrate(
        context: typer.Context,
        confirm: Annotated[str, typer.Option("--confirm")],
    ) -> None:
        require_production_confirmation(confirm=confirm)
        runtime = admin_runtime(context=context)
        facade = build_administration_facade(runtime=runtime)
        echo_schema(status=asyncio.run(facade.apply_schema()))

    @aws_app.command("queues-status")
    def aws_queues_status(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        facade = build_administration_facade(runtime=runtime)
        echo_queues(
            depths=facade.queue_depths(include_dead_letters=False),
        )

    @aws_app.command("queues-drain")
    def aws_queues_drain(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        facade = build_administration_facade(runtime=runtime)
        echo_queues(
            depths=facade.drain_queues(progress=echo_administration_progress),
        )
        typer.echo("working queues are stably empty")

    @aws_app.command("dlqs-status")
    def aws_dlqs_status(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        facade = build_administration_facade(runtime=runtime)
        echo_queues(depths=facade.dead_letter_depths())

    @aws_app.command("dlq-peek")
    def aws_dlq_peek(
        context: typer.Context,
        queue: Annotated[DeadLetterQueueName, typer.Option("--queue")],
        max_messages: Annotated[
            int,
            typer.Option("--max-messages", min=1, max=10),
        ],
    ) -> None:
        runtime = admin_runtime(context=context)
        facade = build_administration_facade(runtime=runtime)
        result = facade.peek_dead_letters(
            request=DeadLetterPeekRequest(
                queue=queue,
                max_messages=max_messages,
            ),
        )
        if not result.messages:
            typer.echo("no messages")
        for message in result.messages:
            typer.echo(f"message[{message.position}]={message.body}")

    @aws_app.command("send-reference")
    def aws_send_reference(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        result = asyncio.run(
            build_administration_facade(runtime=runtime).send_reference(),
        )
        typer.echo(f"reference sent message_id={result.message_id}")

    @aws_app.command("send-live")
    def aws_send_live(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        result = asyncio.run(
            build_administration_facade(runtime=runtime).send_current_live(),
        )
        typer.echo(f"live sent message_id={result.message_id}")

    @aws_app.command("send-community")
    def aws_send_community(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        result = asyncio.run(
            build_administration_facade(runtime=runtime).send_community(),
        )
        typer.echo(f"community dispatch sent message_id={result.message_id}")

    @aws_app.command("schedules-status")
    def aws_schedules_status(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        facade = build_administration_facade(runtime=runtime)
        echo_schedules(schedules=facade.schedule_snapshots())

    @aws_app.command("schedules-bootstrap-pause")
    def aws_schedules_bootstrap_pause(
        context: typer.Context,
        state_file: Annotated[Path, typer.Option("--state-file")],
        confirm: Annotated[str, typer.Option("--confirm")],
    ) -> None:
        """Snapshot and disable schedules before migration 0005 exists."""
        require_production_confirmation(confirm=confirm)
        runtime = admin_runtime(context=context)
        facade = build_administration_facade(runtime=runtime)
        snapshot = facade.pause_schedules_to_state_file(
            request=ScheduleStateFileRequest(path=state_file),
        )
        typer.echo(
            f"schedule_snapshot={state_file} schedules={len(snapshot.schedules)} "
            "state=disabled",
        )

    @aws_app.command("schedules-bootstrap-restore")
    def aws_schedules_bootstrap_restore(
        context: typer.Context,
        state_file: Annotated[Path, typer.Option("--state-file")],
        confirm: Annotated[str, typer.Option("--confirm")],
    ) -> None:
        """Restore schedules captured by the migration 0005 bootstrap."""
        require_production_confirmation(confirm=confirm)
        runtime = admin_runtime(context=context)
        facade = build_administration_facade(runtime=runtime)
        snapshot = facade.restore_schedules_from_state_file(
            request=ScheduleStateFileRequest(path=state_file),
        )
        typer.echo(
            f"schedule_snapshot={state_file} schedules={len(snapshot.schedules)} "
            "state=restored",
        )

    @aws_app.command("maintenance-status")
    def aws_maintenance_status(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        facade = build_administration_facade(runtime=runtime)
        echo_maintenance(
            window=asyncio.run(facade.latest_maintenance()),
        )

    @aws_app.command("schedules-pause")
    def aws_schedules_pause(
        context: typer.Context,
        reason: Annotated[str, typer.Option("--reason", min=1)],
        confirm: Annotated[str, typer.Option("--confirm")],
    ) -> None:
        require_production_confirmation(confirm=confirm)
        runtime = admin_runtime(context=context)
        facade = build_administration_facade(runtime=runtime)
        window = asyncio.run(
            facade.pause_schedules(
                reason=AdministrationReason(reason=reason),
            ),
        )
        echo_maintenance(window=window)

    @aws_app.command("schedules-restore")
    def aws_schedules_restore(
        context: typer.Context,
        confirm: Annotated[str, typer.Option("--confirm")],
    ) -> None:
        require_production_confirmation(confirm=confirm)
        runtime = admin_runtime(context=context)
        facade = build_administration_facade(runtime=runtime)
        echo_maintenance(
            window=asyncio.run(facade.restore_schedules()),
        )

    @aws_app.command("rebaseline-current")
    def aws_rebaseline_current(
        context: typer.Context,
        reason: Annotated[str, typer.Option("--reason", min=1)],
        confirm: Annotated[str, typer.Option("--confirm")],
    ) -> None:
        require_production_confirmation(confirm=confirm)
        runtime = admin_runtime(context=context)
        facade = build_administration_facade(runtime=runtime)
        result = asyncio.run(
            facade.rebaseline_current(
                reason=AdministrationReason(reason=reason),
                refresh_normalized_data=False,
                progress=echo_administration_progress,
            ),
        )
        typer.echo(
            f"rebaseline_id={result.id} season={result.season_id} "
            f"change_events_deleted={result.change_events_deleted} "
            f"entity_changes_deleted={result.entity_changes_deleted} "
            f"snapshots_rebuilt={result.snapshots_rebuilt}",
        )

    @nas_app.command("doctor")
    def nas_doctor(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        build_administration_facade(runtime=runtime).nas_doctor()
        typer.echo("NAS administration checks passed")

    @nas_app.command("status")
    def nas_status(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        facade = build_administration_facade(runtime=runtime)
        echo_collector(status=facade.collector_status())

    @nas_app.command("start")
    def nas_start(
        context: typer.Context,
        confirm: Annotated[str, typer.Option("--confirm")],
    ) -> None:
        require_production_confirmation(confirm=confirm)
        runtime = admin_runtime(context=context)
        facade = build_administration_facade(runtime=runtime)
        echo_collector(status=facade.collector_start())

    @nas_app.command("stop")
    def nas_stop(
        context: typer.Context,
        confirm: Annotated[str, typer.Option("--confirm")],
    ) -> None:
        require_production_confirmation(confirm=confirm)
        runtime = admin_runtime(context=context)
        facade = build_administration_facade(runtime=runtime)
        echo_collector(status=facade.collector_stop())

    @nas_app.command("logs")
    def nas_logs(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        facade = build_administration_facade(runtime=runtime)
        result = facade.nas_logs(
            request=NasLogsRequest(
                tail_lines=runtime.settings.nas_log_tail_lines,
            ),
        )
        typer.echo(result.output)

    @nas_app.command("update")
    def nas_update(
        context: typer.Context,
        sha: Annotated[str, typer.Option("--sha", min=40, max=40)],
        confirm: Annotated[str, typer.Option("--confirm")],
    ) -> None:
        require_production_confirmation(confirm=confirm)
        runtime = admin_runtime(context=context)
        facade = build_administration_facade(runtime=runtime)
        echo_collector(
            status=facade.collector_update(request=GitShaRequest(sha=sha)),
        )

    @nas_app.command("rollback")
    def nas_rollback(
        context: typer.Context,
        sha: Annotated[str, typer.Option("--sha", min=40, max=40)],
        confirm: Annotated[str, typer.Option("--confirm")],
    ) -> None:
        require_production_confirmation(confirm=confirm)
        runtime = admin_runtime(context=context)
        facade = build_administration_facade(runtime=runtime)
        echo_collector(
            status=facade.collector_update(request=GitShaRequest(sha=sha)),
        )

    @prod_app.command("doctor")
    def prod_doctor(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        facade = build_administration_facade(runtime=runtime)
        asyncio.run(facade.production_doctor())
        typer.echo("production administration checks passed")

    @prod_app.command("status")
    def prod_status(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        facade = build_administration_facade(runtime=runtime)
        status = asyncio.run(facade.production_status())
        echo_schema(status=status.schema_status)
        echo_maintenance(window=status.maintenance)
        echo_queues(depths=status.queues)
        echo_schedules(schedules=status.schedules)
        echo_collector(status=status.collector)

    @prod_app.command("maintenance-begin")
    def prod_maintenance_begin(
        context: typer.Context,
        reason: Annotated[str, typer.Option("--reason", min=1)],
        confirm: Annotated[str, typer.Option("--confirm")],
    ) -> None:
        require_production_confirmation(confirm=confirm)
        runtime = admin_runtime(context=context)
        facade = build_administration_facade(runtime=runtime)
        echo_maintenance(
            window=asyncio.run(
                facade.begin_production_maintenance(
                    reason=AdministrationReason(reason=reason),
                    progress=echo_administration_progress,
                ),
            ),
        )

    @prod_app.command("maintenance-end")
    def prod_maintenance_end(
        context: typer.Context,
        confirm: Annotated[str, typer.Option("--confirm")],
    ) -> None:
        require_production_confirmation(confirm=confirm)
        runtime = admin_runtime(context=context)
        facade = build_administration_facade(runtime=runtime)
        echo_maintenance(
            window=asyncio.run(
                facade.end_production_maintenance(
                    progress=echo_administration_progress,
                ),
            ),
        )

    @prod_app.command("rebaseline-current")
    def prod_rebaseline_current(
        context: typer.Context,
        reason: Annotated[str, typer.Option("--reason", min=1)],
        confirm: Annotated[str, typer.Option("--confirm")],
    ) -> None:
        require_production_confirmation(confirm=confirm)
        runtime = admin_runtime(context=context)
        facade = build_administration_facade(runtime=runtime)
        result = asyncio.run(
            facade.rebaseline_current(
                reason=AdministrationReason(reason=reason),
                refresh_normalized_data=True,
                progress=echo_administration_progress,
            ),
        )
        typer.echo(
            f"rebaseline_id={result.id} season={result.season_id} "
            f"change_events_deleted={result.change_events_deleted} "
            f"entity_changes_deleted={result.entity_changes_deleted} "
            f"snapshots_rebuilt={result.snapshots_rebuilt}",
        )

    return app


def admin_runtime(*, context: typer.Context) -> AdminRuntime:
    """Return the runtime installed by the root callback."""
    if context.obj is None:
        raise RuntimeError("Administration runtime was not configured.")
    return cast("AdminRuntime", context.obj)


def require_production_confirmation(*, confirm: str) -> None:
    """Require an exact production confirmation token."""
    if confirm != PRODUCTION_CONFIRMATION:
        raise typer.BadParameter(
            f"confirmation must be exactly {PRODUCTION_CONFIRMATION!r}",
            param_hint="--confirm",
        )


def echo_administration_progress(progress: AdministrationProgress) -> None:
    """Render typed workflow and queue-drain progress for CLI operators."""
    if isinstance(progress, AdministrationWorkflowProgress):
        detail = "" if progress.detail is None else f" detail={progress.detail!r}"
        typer.echo(
            f"workflow={progress.workflow.value} step={progress.step.value} "
            f"state={progress.state.value}{detail}",
        )
        return
    if isinstance(progress, QueueDrainProgress):
        queues = ",".join(
            f"{depth.name}={depth.total}" for depth in progress.queues
        )
        typer.echo(
            f"queue_drain_stage={progress.stage.value} "
            f"elapsed_seconds={progress.elapsed_seconds:g} "
            f"stable_for_seconds={progress.stable_for_seconds:g} "
            f"required_stable_seconds={progress.required_stable_seconds} "
            f"queues=[{queues}]",
        )
        return
    assert_never(progress)


def echo_schema(*, status: SchemaStatus) -> None:
    """Print schema state consistently."""
    applied = ",".join(str(value) for value in status.applied_versions)
    pending = ",".join(str(value) for value in status.pending_versions)
    typer.echo(f"schema applied=[{applied}] pending=[{pending}]")


def echo_queues(*, depths: list[QueueDepth]) -> None:
    """Print complete queue depth samples."""
    for depth in depths:
        typer.echo(
            f"queue={depth.name} visible={depth.visible} "
            f"in_flight={depth.in_flight} delayed={depth.delayed} "
            f"total={depth.total}",
        )


def echo_schedules(*, schedules: list[ScheduleSnapshot]) -> None:
    """Print schedule identity, state, and expression."""
    for schedule in schedules:
        typer.echo(
            f"schedule={schedule.group_name}/{schedule.name} "
            f"state={schedule.state.value} "
            f"expression={schedule.schedule_expression}",
        )


def echo_maintenance(*, window: MaintenanceWindow | None) -> None:
    """Print one maintenance audit summary."""
    if window is None:
        typer.echo("maintenance=none")
        return
    typer.echo(
        f"maintenance_id={window.id} phase={window.phase.value} "
        f"operator={window.operator_arn} reason={window.reason!r}",
    )


def echo_collector(*, status: object) -> None:
    """Print collector state without exposing its environment."""
    from fpl_data_relay.application.ports.administration import NasCollectorStatus

    collector = cast("NasCollectorStatus", status)
    typer.echo(
        f"collector_running={str(collector.running).lower()} "
        f"health={collector.health} image={collector.image}",
    )
