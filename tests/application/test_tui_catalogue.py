import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from fpl_data_relay.application.tui_catalogue import (
    COMMAND_CATALOGUE,
    COMMANDS_BY_TARGET,
    PARAMETER_MODELS,
    CommandLifetime,
    CommandPrerequisite,
    CommandRisk,
    DeadLetterQueue,
    DlqSelectionParameters,
    ParameterKind,
    ReasonParameters,
    ShaParameters,
    StateFileParameters,
    command_for_target,
)

PROJECT_ROOT = Path(__file__).parents[2]
PUBLIC_TARGET_PATTERN = re.compile(
    r"^([a-zA-Z0-9_-]+):.*## (.+)$",
    flags=re.MULTILINE,
)


def documented_make_targets() -> tuple[tuple[str, str], ...]:
    makefile = (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")
    return tuple(PUBLIC_TARGET_PATTERN.findall(makefile))


def test_catalogue_matches_documented_make_help_targets() -> None:
    documented = documented_make_targets()
    launcher_exemptions = {"tui"}
    catalogued = tuple(
        (command.target, command.description) for command in COMMAND_CATALOGUE
    )

    assert tuple(
        row for row in documented if row[0] not in launcher_exemptions
    ) == catalogued
    assert {target for target, _description in documented}.difference(
        COMMANDS_BY_TARGET,
    ).issubset(launcher_exemptions)


def test_catalogue_contains_49_unique_commands() -> None:
    assert len(COMMAND_CATALOGUE) == 49
    assert len(COMMANDS_BY_TARGET) == len(COMMAND_CATALOGUE)


def test_each_parameter_kind_resolves_to_its_explicit_model() -> None:
    assert set(PARAMETER_MODELS) == set(ParameterKind)
    for command in COMMAND_CATALOGUE:
        assert command.parameter_model is PARAMETER_MODELS[command.parameter_kind]


def test_production_changes_are_classified_and_use_admin_config() -> None:
    production_targets = {
        command.target
        for command in COMMAND_CATALOGUE
        if command.risk is CommandRisk.PRODUCTION_CHANGE
    }

    assert production_targets == {
        "aws-db-migrate",
        "aws-send-reference",
        "aws-send-live",
        "aws-send-community",
        "aws-schedules-bootstrap-pause",
        "aws-schedules-bootstrap-restore",
        "aws-schedules-pause",
        "aws-schedules-restore",
        "aws-rebaseline-current",
        "nas-start",
        "nas-stop",
        "nas-update",
        "nas-rollback",
        "prod-maintenance-begin",
        "prod-maintenance-end",
        "prod-rebaseline-current",
    }
    for target in production_targets:
        command = COMMANDS_BY_TARGET[target]
        assert CommandPrerequisite.ADMIN_CONFIG in command.prerequisites


def test_only_expected_targets_are_long_running() -> None:
    assert {
        command.target
        for command in COMMAND_CATALOGUE
        if command.lifetime is CommandLifetime.LONG_RUNNING
    } == {"local-dev", "local-client", "local-logs"}


def test_parameter_models_validate_and_normalize_explicit_input() -> None:
    assert ReasonParameters(
        reason="  controlled migration  ",
    ).reason == "controlled migration"
    assert ShaParameters(
        sha="a" * 40,
    ).sha == "a" * 40
    assert DlqSelectionParameters(
        queue=DeadLetterQueue.COMMUNITY,
    ).queue is DeadLetterQueue.COMMUNITY
    assert StateFileParameters(
        state_file=Path(".admin-state/schedules.json"),
    ).state_file == Path(".admin-state/schedules.json")


@pytest.mark.parametrize(
    ("model", "values"),
    [
        (
            ReasonParameters,
            {"reason": "   "},
        ),
        (
            ShaParameters,
            {"sha": "ABC"},
        ),
        (
            DlqSelectionParameters,
            {"queue": "unknown"},
        ),
        (
            StateFileParameters,
            {"state_file": Path(".")},
        ),
    ],
)
def test_parameter_models_reject_invalid_input(
    model: type[ReasonParameters]
    | type[ShaParameters]
    | type[DlqSelectionParameters]
    | type[StateFileParameters],
    values: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(values)


def test_catalogue_models_are_frozen() -> None:
    command = COMMAND_CATALOGUE[0]

    with pytest.raises(ValidationError, match="frozen"):
        command.target = "changed"


def test_lookup_rejects_arbitrary_make_targets() -> None:
    assert command_for_target(target="aws-status") is COMMANDS_BY_TARGET["aws-status"]

    with pytest.raises(ValueError, match="Unknown public Make target"):
        command_for_target(target="require-admin-env")
