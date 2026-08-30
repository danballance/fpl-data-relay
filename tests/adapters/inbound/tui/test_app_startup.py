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

    async with app.run_test(size=(110, 30)) as pilot:
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
        assert app.screen.has_class("narrow")

        await pilot.resize_terminal(109, 24)
        await pilot.pause()
        assert app.screen.has_class("narrow")

        await pilot.resize_terminal(110, 24)
        await pilot.pause()
        assert not app.screen.has_class("narrow")


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
