from pathlib import Path
from typing import cast

from textual.command import CommandPalette
from textual.widgets import ContentSwitcher, Static

from fpl_data_relay.adapters.inbound.tui.app import (
    AdministrationFacadeFactory,
    build_tui,
)
from fpl_data_relay.adapters.inbound.tui.settings import TuiSettings


class NoRemoteFacade: ...


def project_settings(*, tmp_path: Path) -> TuiSettings:
    root = tmp_path / "project"
    root.mkdir()
    (root / "Makefile").write_text("help:\n")
    (root / "pyproject.toml").write_text("[project]\nname = 'test'\n")
    return TuiSettings(
        project_root=root,
        admin_config=root / ".admin.env",
        log_path=root / ".admin-state" / "tui" / "fpl-tui.jsonl",
        log_max_bytes=10_485_760,
        log_file_count=5,
    )


async def test_tui_startup_and_navigation_make_no_remote_calls(
    tmp_path: Path,
) -> None:
    launch_settings = project_settings(tmp_path=tmp_path)
    facade_requests: list[Path] = []

    def facade_factory(
        path: Path,
    ) -> NoRemoteFacade:
        facade_requests.append(path)
        return NoRemoteFacade()

    app = build_tui(
        settings=launch_settings,
        facade_factory=cast("AdministrationFacadeFactory", facade_factory),
    )

    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        assert facade_requests == []
        assert app.query_one("#size-warning", Static).display is False

        await pilot.click("#nav--workspace")
        await pilot.pause()

        assert app.query_one(ContentSwitcher).current == "workspace"
        assert facade_requests == []

    assert launch_settings.log_path.is_file()


async def test_tui_shows_minimum_size_warning(tmp_path: Path) -> None:
    launch_settings = project_settings(tmp_path=tmp_path)
    app = build_tui(
        settings=launch_settings,
        facade_factory=cast(
            "AdministrationFacadeFactory",
            lambda _path: NoRemoteFacade(),
        ),
    )

    async with app.run_test(size=(79, 23)) as pilot:
        await pilot.pause()

        warning = app.query_one("#size-warning", Static)
        assert warning.display is True
        assert "requires at least 80x24" in str(warning.content)


async def test_tui_uses_exact_responsive_boundaries(tmp_path: Path) -> None:
    launch_settings = project_settings(tmp_path=tmp_path)
    app = build_tui(
        settings=launch_settings,
        facade_factory=cast(
            "AdministrationFacadeFactory",
            lambda _path: NoRemoteFacade(),
        ),
    )

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert app.query_one("#size-warning", Static).display is False
        assert app.screen.has_class("stacked")
        assert app.screen.has_class("narrow")

        await pilot.resize_terminal(109, 24)
        await pilot.pause()
        assert app.screen.has_class("stacked")
        assert app.screen.has_class("narrow")

        await pilot.resize_terminal(110, 24)
        await pilot.pause()
        assert not app.screen.has_class("stacked")
        assert app.screen.has_class("narrow")

        await pilot.resize_terminal(139, 24)
        await pilot.pause()
        assert not app.screen.has_class("stacked")
        assert app.screen.has_class("narrow")
        assert app.query_one("#sidebar").display is False
        assert app.query_one("#narrow-nav").display is True

        await pilot.resize_terminal(140, 24)
        await pilot.pause()
        assert not app.screen.has_class("stacked")
        assert not app.screen.has_class("narrow")
        assert app.query_one("#sidebar").display is True
        assert app.query_one("#narrow-nav").display is False


async def test_tui_uses_side_by_side_workspace_at_split_boundary(
    tmp_path: Path,
) -> None:
    launch_settings = project_settings(tmp_path=tmp_path)
    app = build_tui(
        settings=launch_settings,
        facade_factory=cast(
            "AdministrationFacadeFactory",
            lambda _path: NoRemoteFacade(),
        ),
    )

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()

        workspace = app.query_one("#workspace-layout")
        control_pane = app.query_one("#control-pane")
        task_drawer = app.query_one("#task-drawer")

        assert control_pane.region.x == workspace.region.x
        assert control_pane.region.y == task_drawer.region.y == workspace.region.y
        assert control_pane.region.height == workspace.region.height
        assert task_drawer.region.height == workspace.region.height
        assert task_drawer.region.x == control_pane.region.right
        assert task_drawer.region.right == workspace.region.right
        assert control_pane.region.width == 66
        assert task_drawer.region.width == 44


async def test_tui_stacks_task_drawer_below_controls_at_109_columns(
    tmp_path: Path,
) -> None:
    launch_settings = project_settings(tmp_path=tmp_path)
    app = build_tui(
        settings=launch_settings,
        facade_factory=cast(
            "AdministrationFacadeFactory",
            lambda _path: NoRemoteFacade(),
        ),
    )

    async with app.run_test(size=(109, 30)) as pilot:
        await pilot.pause()

        workspace = app.query_one("#workspace-layout")
        control_pane = app.query_one("#control-pane")
        task_drawer = app.query_one("#task-drawer")

        assert app.screen.has_class("stacked")
        assert control_pane.region.x == task_drawer.region.x == workspace.region.x
        assert control_pane.region.width == workspace.region.width
        assert task_drawer.region.width == workspace.region.width
        assert control_pane.region.y == workspace.region.y
        assert task_drawer.region.y == control_pane.region.bottom
        assert task_drawer.region.bottom == workspace.region.bottom
        assert task_drawer.region.height == 13


async def test_tui_command_palette_opens_with_catalogue_provider(
    tmp_path: Path,
) -> None:
    launch_settings = project_settings(tmp_path=tmp_path)
    app = build_tui(
        settings=launch_settings,
        facade_factory=cast(
            "AdministrationFacadeFactory",
            lambda _path: NoRemoteFacade(),
        ),
    )

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("ctrl+p")
        await pilot.pause()

        assert isinstance(app.screen, CommandPalette)
