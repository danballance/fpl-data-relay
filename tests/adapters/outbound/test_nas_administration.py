import subprocess
from typing import cast

import pytest

from fpl_data_relay.adapters.outbound import nas_administration
from fpl_data_relay.adapters.outbound.nas_administration import (
    NasSshAdministration,
    parse_status,
)
from fpl_data_relay.config import AdminSettings


def settings() -> AdminSettings:
    return AdminSettings.model_validate(
        {
            "aws_profile": "admin",
            "aws_region": "eu-west-2",
            "data_stack_name": "data",
            "app_stack_name": "app",
            "nas_ssh_target": "relay@nas",
            "nas_stack_directory": "/volume1/relay",
            "nas_compose_executable": "/usr/local/bin/docker-compose",
            "nas_docker_executable": "/usr/local/bin/docker",
            "nas_ssh_connect_timeout_seconds": 10,
            "drain_timeout_seconds": 20,
            "drain_poll_seconds": 2,
            "drain_stable_seconds": 4,
            "nas_health_attempts": 2,
            "nas_health_interval_seconds": 1,
            "nas_log_tail_lines": 10,
        },
    )


def test_nas_adapter_runs_versioned_helper_for_all_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], str]] = []

    def run(
        command: list[str],
        **parameters: object,
    ) -> subprocess.CompletedProcess[str]:
        script = cast("str", parameters["input"])
        calls.append((command, script))
        action = command[9]
        if action == "doctor":
            output = "doctor=ok\n"
        elif action == "logs":
            output = "collector log\n"
        else:
            output = (
                "backup=/tmp/old\n"
                "running=true\nhealth=healthy\n"
                "image=ghcr.io/collector:sha-" + "a" * 40 + "\n"
            )
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(nas_administration.subprocess, "run", run)
    adapter = NasSshAdministration(settings=settings())
    adapter.doctor()
    assert adapter.status().running is True
    assert adapter.start().health == "healthy"
    assert adapter.stop().image.endswith("a" * 40)
    assert adapter.logs(tail_lines=25) == "collector log"
    assert adapter.update(image_tag="sha-" + "a" * 40).running is True
    assert [command[9] for command, _ in calls] == [
        "doctor",
        "status",
        "start",
        "stop",
        "logs",
        "update",
    ]
    assert calls[0][0][:6] == [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "relay@nas",
    ]
    assert "expected exactly one COLLECTOR_IMAGE_TAG" in calls[0][1]
    assert calls[-1][0][-1] == "sha-" + "a" * 40


def test_nas_adapter_rejects_invalid_inputs_and_remote_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = NasSshAdministration(settings=settings())
    with pytest.raises(ValueError, match="positive"):
        adapter.logs(tail_lines=0)
    with pytest.raises(ValueError, match="40 lowercase"):
        adapter.update(image_tag="latest")

    def failed(
        command: list[str],
        **parameters: object,
    ) -> subprocess.CompletedProcess[str]:
        del parameters
        return subprocess.CompletedProcess(command, 4, "", "ssh failed")

    monkeypatch.setattr(nas_administration.subprocess, "run", failed)
    with pytest.raises(RuntimeError, match="exit code 4: ssh failed"):
        adapter.status()


def test_nas_status_parser_validates_complete_boolean_output() -> None:
    status = parse_status(
        output="noise\nrunning=false\nhealth=stopped\nimage=none\n",
    )
    assert status.running is False
    with pytest.raises(RuntimeError, match="omitted fields"):
        parse_status(output="running=true")
    with pytest.raises(RuntimeError, match="true or false"):
        parse_status(output="running=yes\nhealth=healthy\nimage=image")


def test_nas_doctor_requires_success_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(
        command: list[str],
        **parameters: object,
    ) -> subprocess.CompletedProcess[str]:
        del parameters
        return subprocess.CompletedProcess(command, 0, "unexpected\n", "warning\n")

    monkeypatch.setattr(nas_administration.subprocess, "run", run)
    adapter = NasSshAdministration(settings=settings())
    with pytest.raises(RuntimeError, match="did not report success"):
        adapter.doctor()
