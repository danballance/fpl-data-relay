from pathlib import Path

import pytest
from pydantic import ValidationError

from fpl_data_relay.adapters.inbound.tui.settings import TuiSettings


def project_root(*, tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "Makefile").write_text("help:\n")
    (root / "pyproject.toml").write_text("[project]\nname = 'test'\n")
    return root


def settings(*, root: Path) -> TuiSettings:
    return TuiSettings(
        project_root=root,
        admin_config=root / ".admin.env",
        log_path=root / ".admin-state" / "tui" / "fpl-tui.jsonl",
        log_max_bytes=10_485_760,
        log_file_count=5,
    )


def test_tui_settings_accept_explicit_paths_without_existing_admin_config(
    tmp_path: Path,
) -> None:
    root = project_root(tmp_path=tmp_path)

    result = settings(root=root)

    assert result.project_root == root
    assert result.admin_config == root / ".admin.env"
    assert result.log_path == (
        root / ".admin-state" / "tui" / "fpl-tui.jsonl"
    )
    assert result.log_max_bytes == 10_485_760
    assert result.log_file_count == 5


@pytest.mark.parametrize(
    "field",
    ["project_root", "admin_config", "log_path"],
)
def test_tui_settings_reject_relative_paths(tmp_path: Path, field: str) -> None:
    root = project_root(tmp_path=tmp_path)
    values: dict[str, Path | int] = {
        "project_root": root,
        "admin_config": root / ".admin.env",
        "log_path": root / ".admin-state" / "tui" / "fpl-tui.jsonl",
        "log_max_bytes": 10_485_760,
        "log_file_count": 5,
    }
    values[field] = Path("relative")

    with pytest.raises(ValidationError, match="must be absolute"):
        TuiSettings.model_validate(values)


@pytest.mark.parametrize("missing_name", ["Makefile", "pyproject.toml"])
def test_tui_settings_reject_checkout_missing_required_file(
    tmp_path: Path,
    missing_name: str,
) -> None:
    root = project_root(tmp_path=tmp_path)
    (root / missing_name).unlink()

    with pytest.raises(ValidationError, match=f"missing {missing_name}"):
        settings(root=root)


def test_tui_settings_reject_admin_config_outside_project_root(
    tmp_path: Path,
) -> None:
    root = project_root(tmp_path=tmp_path)

    with pytest.raises(ValidationError, match="directly inside"):
        TuiSettings(
            project_root=root,
            admin_config=root / "config" / ".admin.env",
            log_path=root / ".admin-state" / "tui" / "fpl-tui.jsonl",
            log_max_bytes=10_485_760,
            log_file_count=5,
        )


def test_tui_settings_reject_log_outside_admin_state_directory(
    tmp_path: Path,
) -> None:
    root = project_root(tmp_path=tmp_path)

    with pytest.raises(ValidationError, match=r"under \.admin-state"):
        TuiSettings(
            project_root=root,
            admin_config=root / ".admin.env",
            log_path=root / "logs" / "fpl-tui.jsonl",
            log_max_bytes=10_485_760,
            log_file_count=5,
        )


@pytest.mark.parametrize(
    ("max_bytes", "file_count"),
    [(0, 5), (10_485_760, 0)],
)
def test_tui_settings_reject_non_positive_rotation_limits(
    tmp_path: Path,
    max_bytes: int,
    file_count: int,
) -> None:
    root = project_root(tmp_path=tmp_path)

    with pytest.raises(ValidationError):
        TuiSettings(
            project_root=root,
            admin_config=root / ".admin.env",
            log_path=root / ".admin-state" / "tui" / "fpl-tui.jsonl",
            log_max_bytes=max_bytes,
            log_file_count=file_count,
        )
