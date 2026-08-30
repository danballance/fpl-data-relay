"""Typer entry point for the local Textual operations console."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from fpl_data_relay.adapters.inbound.tui.app import build_tui
from fpl_data_relay.adapters.inbound.tui.settings import TuiSettings
from fpl_data_relay.admin_bootstrap import build_admin_runtime
from fpl_data_relay.application.administration_facade import AdministrationFacade

app = typer.Typer(add_completion=False, no_args_is_help=True)


def build_administration_facade(
    config_path: Path,
) -> AdministrationFacade:
    """Build one lazy, operation-scoped production administration façade."""
    runtime = build_admin_runtime(config_path)
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


@app.command()
def launch(
    project_root: Annotated[
        Path,
        typer.Option(
            "--project-root",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ],
    admin_config: Annotated[
        Path,
        typer.Option(
            "--admin-config",
            exists=False,
            file_okay=True,
            dir_okay=False,
            resolve_path=True,
        ),
    ],
    log_path: Annotated[
        Path,
        typer.Option(
            "--log-path",
            exists=False,
            file_okay=True,
            dir_okay=False,
            resolve_path=True,
        ),
    ],
    log_max_bytes: Annotated[
        int,
        typer.Option("--log-max-bytes", min=1),
    ],
    log_file_count: Annotated[
        int,
        typer.Option("--log-file-count", min=1),
    ],
) -> None:
    """Launch the FPL Data Relay developer and production console."""
    settings = TuiSettings(
        project_root=project_root,
        admin_config=admin_config,
        log_path=log_path,
        log_max_bytes=log_max_bytes,
        log_file_count=log_file_count,
    )
    build_tui(
        settings=settings,
        facade_factory=build_administration_facade,
    ).run()
