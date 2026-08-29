"""Typer command surface for local production administration."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Protocol, cast

import typer

from fpl_data_relay.application.administration import AdministrationService
from fpl_data_relay.application.errors import AwsProfileError
from fpl_data_relay.application.ports.administration import (
    AwsAdministration,
    AwsIamPrincipalType,
    AwsProfileAdministration,
    AwsProfileBootstrapAdministration,
    AwsProfileBootstrapStatus,
    AwsProfileStatus,
    MaintenanceWindow,
    NasAdministration,
    ProductionAdministrationDatabase,
    QueueDepth,
    ScheduleSnapshot,
    SchemaStatus,
)
from fpl_data_relay.application.schedule_bootstrap import (
    pause_schedules_to_snapshot,
    restore_schedules_from_snapshot,
)
from fpl_data_relay.config import AdminSettings

PRODUCTION_CONFIRMATION = "production"


class AdminRuntime(Protocol):
    """Owned operations supplied by the administration composition root."""

    settings: AdminSettings
    service: AdministrationService
    profile: AwsProfileAdministration
    profile_bootstrap: AwsProfileBootstrapAdministration
    aws: AwsAdministration
    nas: NasAdministration
    database: ProductionAdministrationDatabase


type AdminRuntimeFactory = Callable[[Path], AdminRuntime]


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

    @aws_app.command("profile-bootstrap")
    def aws_profile_bootstrap(context: typer.Context) -> None:
        """Generate and attach the sign-in and relay administration policies."""
        runtime = admin_runtime(context=context)
        typer.echo(runtime.profile_bootstrap.instructions())
        typer.echo(
            f"Configured target account: {runtime.settings.aws_account_id} "
            f"({runtime.settings.aws_region})",
        )
        bootstrap_profile = typer.prompt(
            "Existing authenticated bootstrap AWS profile",
        ).strip()
        raw_principal_type = typer.prompt(
            "Target IAM principal type (user, group, or role)",
        ).strip()
        try:
            principal_type = AwsIamPrincipalType(raw_principal_type)
        except ValueError as error:
            raise typer.BadParameter(
                "IAM principal type must be user, group, or role.",
                param_hint="principal type",
            ) from error
        principal_name = typer.prompt("Target IAM principal name").strip()
        confirm = typer.prompt("Type 'production' to attach IAM policies").strip()
        require_production_confirmation(confirm=confirm)
        echo_aws_profile_bootstrap(
            status=run_profile_operation(
                operation=lambda: runtime.profile_bootstrap.bootstrap(
                    bootstrap_profile=bootstrap_profile,
                    principal_type=principal_type,
                    principal_name=principal_name,
                ),
            ),
        )

    @aws_app.command("profile-setup")
    def aws_profile_setup(context: typer.Context) -> None:
        """Configure, authenticate, and verify the console-login profile."""
        runtime = admin_runtime(context=context)
        echo_aws_profile(
            status=run_profile_operation(operation=runtime.profile.setup),
        )

    @aws_app.command("profile-login")
    def aws_profile_login(context: typer.Context) -> None:
        """Renew and verify the configured console-login profile."""
        runtime = admin_runtime(context=context)
        echo_aws_profile(
            status=run_profile_operation(operation=runtime.profile.login),
        )

    @aws_app.command("profile-status")
    def aws_profile_status(context: typer.Context) -> None:
        """Show the configured profile's verified authentication state."""
        runtime = admin_runtime(context=context)
        echo_aws_profile(
            status=run_profile_operation(operation=runtime.profile.status),
        )

    @aws_app.command("profile-logout")
    def aws_profile_logout(context: typer.Context) -> None:
        """Remove cached login credentials for only the configured profile."""
        runtime = admin_runtime(context=context)
        run_profile_operation(operation=runtime.profile.logout)
        typer.echo(
            f"profile={runtime.settings.aws_profile} authenticated=false",
        )

    @aws_app.command("doctor")
    def aws_doctor(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        runtime.service.aws_doctor()
        typer.echo("AWS administration checks passed")

    @aws_app.command("app-revision")
    def aws_app_revision(context: typer.Context) -> None:
        """Show the immutable revision exposed by the application stack."""
        runtime = admin_runtime(context=context)
        runtime.aws.identity()
        typer.echo(f"deployed_revision={runtime.aws.app_deployed_revision()}")

    @aws_app.command("status")
    def aws_status(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        identity = runtime.aws.identity()
        typer.echo(f"account={identity.account_id} operator={identity.arn}")
        runtime.aws.resources()
        echo_schema(status=asyncio.run(runtime.database.schema_status()))
        echo_maintenance(
            window=asyncio.run(runtime.service.latest_maintenance()),
        )
        echo_queues(
            depths=runtime.aws.queue_depths(include_dead_letters=True),
        )
        echo_schedules(schedules=runtime.aws.schedule_snapshots())

    @aws_app.command("db-status")
    def aws_db_status(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        runtime.aws.identity()
        echo_schema(status=asyncio.run(runtime.database.schema_status()))

    @aws_app.command("db-migrate")
    def aws_db_migrate(
        context: typer.Context,
        confirm: Annotated[str, typer.Option("--confirm")],
    ) -> None:
        require_production_confirmation(confirm=confirm)
        runtime = admin_runtime(context=context)
        echo_schema(status=asyncio.run(runtime.service.apply_schema()))

    @aws_app.command("queues-status")
    def aws_queues_status(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        runtime.aws.identity()
        echo_queues(
            depths=runtime.aws.queue_depths(include_dead_letters=False),
        )

    @aws_app.command("queues-drain")
    def aws_queues_drain(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        runtime.aws.identity()
        echo_queues(depths=runtime.service.drain_queues())
        typer.echo("working queues are stably empty")

    @aws_app.command("dlqs-status")
    def aws_dlqs_status(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        runtime.aws.identity()
        echo_queues(
            depths=[
                depth
                for depth in runtime.aws.queue_depths(include_dead_letters=True)
                if "dead-letter" in depth.name
            ],
        )

    @aws_app.command("dlq-peek")
    def aws_dlq_peek(
        context: typer.Context,
        queue: Annotated[str, typer.Option("--queue")],
        max_messages: Annotated[
            int,
            typer.Option("--max-messages", min=1, max=10),
        ],
    ) -> None:
        runtime = admin_runtime(context=context)
        runtime.aws.identity()
        messages = runtime.aws.peek_dead_letters(
            queue_name=queue,
            max_messages=max_messages,
        )
        if not messages:
            typer.echo("no messages")
        for index, body in enumerate(messages, start=1):
            typer.echo(f"message[{index}]={body}")

    @aws_app.command("send-reference")
    def aws_send_reference(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        runtime.aws.identity()
        message_id = asyncio.run(
            runtime.service.send_reference(allow_maintenance=False),
        )
        typer.echo(f"reference sent message_id={message_id}")

    @aws_app.command("send-live")
    def aws_send_live(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        runtime.aws.identity()
        message_id = asyncio.run(
            runtime.service.send_current_live(allow_maintenance=False),
        )
        typer.echo(f"live sent message_id={message_id}")

    @aws_app.command("send-community")
    def aws_send_community(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        runtime.aws.identity()
        message_id = asyncio.run(
            runtime.service.send_community(allow_maintenance=False),
        )
        typer.echo(f"community dispatch sent message_id={message_id}")

    @aws_app.command("schedules-status")
    def aws_schedules_status(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        runtime.aws.identity()
        echo_schedules(schedules=runtime.aws.schedule_snapshots())

    @aws_app.command("schedules-bootstrap-pause")
    def aws_schedules_bootstrap_pause(
        context: typer.Context,
        state_file: Annotated[Path, typer.Option("--state-file")],
        confirm: Annotated[str, typer.Option("--confirm")],
    ) -> None:
        """Snapshot and disable schedules before migration 0005 exists."""
        require_production_confirmation(confirm=confirm)
        runtime = admin_runtime(context=context)
        snapshot = pause_schedules_to_snapshot(
            aws=runtime.aws,
            snapshot_path=state_file,
            aws_region=runtime.settings.aws_region,
            app_stack_name=runtime.settings.app_stack_name,
            captured_at=datetime.now(tz=UTC),
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
        snapshot = restore_schedules_from_snapshot(
            aws=runtime.aws,
            snapshot_path=state_file,
            aws_region=runtime.settings.aws_region,
            app_stack_name=runtime.settings.app_stack_name,
            restored_at=datetime.now(tz=UTC),
        )
        typer.echo(
            f"schedule_snapshot={state_file} schedules={len(snapshot.schedules)} "
            "state=restored",
        )

    @aws_app.command("maintenance-status")
    def aws_maintenance_status(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        runtime.aws.identity()
        echo_maintenance(
            window=asyncio.run(runtime.service.latest_maintenance()),
        )

    @aws_app.command("schedules-pause")
    def aws_schedules_pause(
        context: typer.Context,
        reason: Annotated[str, typer.Option("--reason", min=1)],
        confirm: Annotated[str, typer.Option("--confirm")],
    ) -> None:
        require_production_confirmation(confirm=confirm)
        runtime = admin_runtime(context=context)
        window = asyncio.run(
            runtime.service.pause_schedules(
                reason=reason,
                collector_was_running=None,
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
        echo_maintenance(
            window=asyncio.run(runtime.service.restore_schedules()),
        )

    @aws_app.command("rebaseline-current")
    def aws_rebaseline_current(
        context: typer.Context,
        reason: Annotated[str, typer.Option("--reason", min=1)],
        confirm: Annotated[str, typer.Option("--confirm")],
    ) -> None:
        require_production_confirmation(confirm=confirm)
        runtime = admin_runtime(context=context)
        result = asyncio.run(
            runtime.service.rebaseline_current(
                reason=reason,
                refresh_normalized_data=False,
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
        runtime.service.nas_doctor()
        typer.echo("NAS administration checks passed")

    @nas_app.command("status")
    def nas_status(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        echo_collector(status=runtime.nas.status())

    @nas_app.command("start")
    def nas_start(
        context: typer.Context,
        confirm: Annotated[str, typer.Option("--confirm")],
    ) -> None:
        require_production_confirmation(confirm=confirm)
        runtime = admin_runtime(context=context)
        echo_collector(status=runtime.nas.start())

    @nas_app.command("stop")
    def nas_stop(
        context: typer.Context,
        confirm: Annotated[str, typer.Option("--confirm")],
    ) -> None:
        require_production_confirmation(confirm=confirm)
        runtime = admin_runtime(context=context)
        echo_collector(status=runtime.nas.stop())

    @nas_app.command("logs")
    def nas_logs(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        typer.echo(
            runtime.nas.logs(
                tail_lines=runtime.settings.nas_log_tail_lines,
            ),
        )

    @nas_app.command("update")
    def nas_update(
        context: typer.Context,
        sha: Annotated[str, typer.Option("--sha", min=40, max=40)],
        confirm: Annotated[str, typer.Option("--confirm")],
    ) -> None:
        require_production_confirmation(confirm=confirm)
        runtime = admin_runtime(context=context)
        echo_collector(status=runtime.nas.update(image_tag=f"sha-{sha}"))

    @nas_app.command("rollback")
    def nas_rollback(
        context: typer.Context,
        sha: Annotated[str, typer.Option("--sha", min=40, max=40)],
        confirm: Annotated[str, typer.Option("--confirm")],
    ) -> None:
        require_production_confirmation(confirm=confirm)
        runtime = admin_runtime(context=context)
        echo_collector(status=runtime.nas.update(image_tag=f"sha-{sha}"))

    @prod_app.command("doctor")
    def prod_doctor(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        runtime.service.aws_doctor()
        runtime.service.nas_doctor()
        asyncio.run(runtime.database.schema_status())
        typer.echo("production administration checks passed")

    @prod_app.command("status")
    def prod_status(context: typer.Context) -> None:
        runtime = admin_runtime(context=context)
        status = asyncio.run(runtime.service.production_status())
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
        echo_maintenance(
            window=asyncio.run(
                runtime.service.begin_production_maintenance(reason=reason),
            ),
        )

    @prod_app.command("maintenance-end")
    def prod_maintenance_end(
        context: typer.Context,
        confirm: Annotated[str, typer.Option("--confirm")],
    ) -> None:
        require_production_confirmation(confirm=confirm)
        runtime = admin_runtime(context=context)
        echo_maintenance(
            window=asyncio.run(runtime.service.end_production_maintenance()),
        )

    @prod_app.command("rebaseline-current")
    def prod_rebaseline_current(
        context: typer.Context,
        reason: Annotated[str, typer.Option("--reason", min=1)],
        confirm: Annotated[str, typer.Option("--confirm")],
    ) -> None:
        require_production_confirmation(confirm=confirm)
        runtime = admin_runtime(context=context)
        result = asyncio.run(
            runtime.service.rebaseline_current(
                reason=reason,
                refresh_normalized_data=True,
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


def run_profile_operation[Result](
    *,
    operation: Callable[[], Result],
) -> Result:
    """Render expected local profile failures without a Python traceback."""
    try:
        return operation()
    except AwsProfileError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error


def echo_aws_profile(*, status: AwsProfileStatus) -> None:
    """Print verified profile state without credential material."""
    typer.echo(
        f"profile={status.profile_name} region={status.region} "
        f"authentication={status.authentication} "
        f"authenticated={str(status.authenticated).lower()} "
        f"account={status.account_id} operator={status.arn}",
    )


def echo_aws_profile_bootstrap(*, status: AwsProfileBootstrapStatus) -> None:
    """Print policy attachment state without credential material."""
    typer.echo(
        f"bootstrap_profile={status.bootstrap_profile} "
        f"bootstrap_operator={status.bootstrap_arn}",
    )
    typer.echo(
        f"target={status.principal_type.value}/{status.principal_name} "
        f"sign_in_policy={status.sign_in_policy_arn}",
    )
    typer.echo(
        f"relay_policy={status.relay_policy_arn} "
        f"relay_policy_state={status.relay_policy_state.value}",
    )
    typer.echo(
        "If the bootstrap identity received temporary IAM-management access, "
        "ask the account administrator to remove that temporary grant now.",
    )


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
