from pathlib import Path
from typing import cast

from fpl_data_relay.adapters.inbound.tui.app import (
    AdministrationFacadeFactory,
    FplDataRelayTui,
    build_tui,
)
from fpl_data_relay.adapters.inbound.tui.settings import TuiSettings


class GuardFacade:
    def __init__(self) -> None:
        self.collector_start_requests = 0

    def collector_start(self) -> str:
        self.collector_start_requests += 1
        return "collector started"


def safety_app(
    *,
    tmp_path: Path,
    facade: GuardFacade,
) -> tuple[FplDataRelayTui, TuiSettings]:
    root = tmp_path / "project"
    root.mkdir()
    (root / "Makefile").write_text("help:\n")
    (root / "pyproject.toml").write_text("[project]\nname = 'test'\n")
    config = root / ".admin.env"
    config.write_text(Path(".admin.env.example").read_text())
    settings = TuiSettings(
        project_root=root,
        admin_config=config,
        log_path=root / ".admin-state" / "tui" / "fpl-tui.jsonl",
        log_max_bytes=10_485_760,
        log_file_count=5,
    )
    app = build_tui(
        settings=settings,
        facade_factory=cast(
            "AdministrationFacadeFactory",
            lambda _path: facade,
        ),
    )
    return app, settings


async def test_mutation_runs_directly_without_unlock_or_confirmation(
    tmp_path: Path,
) -> None:
    facade = GuardFacade()
    app, _settings = safety_app(tmp_path=tmp_path, facade=facade)

    async with app.run_test(size=(110, 30)):
        app.request_target("nas-start")
        await app.workers.wait_for_complete()

        assert facade.collector_start_requests == 1


async def test_inherited_quit_action_uses_the_operation_guard(tmp_path: Path) -> None:
    facade = GuardFacade()
    app, _settings = safety_app(tmp_path=tmp_path, facade=facade)

    async with app.run_test(size=(110, 30)) as pilot:
        app._exclusive_reservation = "nas-start"
        await pilot.press("ctrl+q")
        await pilot.pause()

        assert app.is_running
        assert app._exclusive_reservation == "nas-start"
        app._exclusive_reservation = None
