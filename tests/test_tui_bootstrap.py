from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

import fpl_data_relay.tui_bootstrap as tui_bootstrap
from fpl_data_relay.adapters.inbound.tui.app import (
    AdministrationFacadeFactory,
    FplDataRelayTui,
)
from fpl_data_relay.adapters.inbound.tui.settings import TuiSettings
from fpl_data_relay.application.administration_facade import AdministrationFacade


class FakeTui:
    def __init__(self) -> None:
        self.run_calls = 0

    def run(self) -> None:
        self.run_calls += 1


def test_tui_help_documents_every_explicit_launch_option() -> None:
    result = CliRunner().invoke(tui_bootstrap.app, ["--help"])

    assert result.exit_code == 0
    assert "--project-root" in result.stdout
    assert "--admin-config" in result.stdout
    assert "--log-path" in result.stdout
    assert "--log-max-bytes" in result.stdout
    assert "--log-file-count" in result.stdout


def test_tui_launch_requires_every_explicit_option() -> None:
    result = CliRunner().invoke(tui_bootstrap.app, [])

    assert result.exit_code == 2
    assert "Missing option '--project-root'" in result.stderr


def test_tui_launch_builds_dependencies_and_runs_application(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "Makefile").write_text("help:\n")
    (project_root / "pyproject.toml").write_text("[project]\nname = 'test'\n")
    fake_tui = FakeTui()
    captured_settings: list[TuiSettings] = []
    captured_factories: list[AdministrationFacadeFactory] = []

    def fake_build_tui(
        *,
        settings: TuiSettings,
        facade_factory: AdministrationFacadeFactory,
    ) -> FplDataRelayTui:
        captured_settings.append(settings)
        captured_factories.append(facade_factory)
        return cast("FplDataRelayTui", fake_tui)

    monkeypatch.setattr(tui_bootstrap, "build_tui", fake_build_tui)
    admin_config = project_root / ".admin.env"
    log_path = project_root / ".admin-state" / "tui" / "fpl-tui.jsonl"

    result = CliRunner().invoke(
        tui_bootstrap.app,
        [
            "--project-root",
            str(project_root),
            "--admin-config",
            str(admin_config),
            "--log-path",
            str(log_path),
            "--log-max-bytes",
            "10485760",
            "--log-file-count",
            "5",
        ],
    )

    assert result.exit_code == 0
    assert fake_tui.run_calls == 1
    assert captured_factories == [tui_bootstrap.build_administration_facade]
    assert captured_settings == [
        TuiSettings(
            project_root=project_root,
            admin_config=admin_config,
            log_path=log_path,
            log_max_bytes=10_485_760,
            log_file_count=5,
        ),
    ]


def test_administration_facade_construction_does_not_resolve_remote_adapters(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / ".admin.env"
    example = Path(".admin.env.example").resolve()
    config_path.write_bytes(example.read_bytes())

    facade = tui_bootstrap.build_administration_facade(config_path)

    assert isinstance(facade, AdministrationFacade)
