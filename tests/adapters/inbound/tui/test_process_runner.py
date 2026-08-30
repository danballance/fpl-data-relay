import os
import threading
from pathlib import Path

import pytest

import fpl_data_relay.adapters.inbound.tui.process_runner as process_runner_module
from fpl_data_relay.adapters.inbound.tui.logging import (
    OperationCompletedEvent,
    OperationFailedEvent,
    OperationOutputEvent,
    OperationSignal,
    OperationSignalEvent,
    OperationStartedEvent,
    SecureJsonlLogger,
)
from fpl_data_relay.adapters.inbound.tui.process_runner import (
    MakeProcessAlreadyRunningError,
    MakeProcessInfrastructureError,
    MakeProcessNotRunningError,
    MakeProcessRunner,
    MakeProcessWaitTimeoutError,
    MakeTargetNotAllowedError,
)


def install_fake_uv(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
) -> Path:
    executable_directory = tmp_path / "bin"
    executable_directory.mkdir()
    executable = executable_directory / "uv"
    executable.write_text(f"#!/bin/sh\n{body}\n")
    executable.chmod(0o700)
    monkeypatch.setenv(
        "PATH",
        f"{executable_directory}{os.pathsep}{os.environ['PATH']}",
    )
    return executable


def runner(
    *,
    tmp_path: Path,
    allowed_targets: set[str],
) -> tuple[MakeProcessRunner, SecureJsonlLogger]:
    project_root = tmp_path / "project"
    project_root.mkdir()
    logger = SecureJsonlLogger(
        path=tmp_path / "history" / "fpl-tui.jsonl",
        max_bytes=1_000_000,
        file_count=5,
    )
    return (
        MakeProcessRunner(
            project_root=project_root,
            allowed_targets=allowed_targets,
            event_sink=logger,
            session_id="session-1",
        ),
        logger,
    )


def test_make_process_runner_uses_static_argv_and_streams_pty_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_uv(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        body="printf 'arguments:%s\\n' \"$*\"\nprintf 'raw-output\\n'",
    )
    process_runner, logger = runner(tmp_path=tmp_path, allowed_targets={"safe"})
    output: list[str] = []

    process = process_runner.start(
        target="safe",
        task_id="task-1",
        on_output=output.append,
    )
    result = process.wait(timeout_seconds=5)

    assert process.argv == (
        "uv",
        "run",
        "make",
        "--no-print-directory",
        "safe",
    )
    assert process.target == "safe"
    assert process.pid > 0
    assert result.argv == process.argv
    assert result.exit_code == 0
    assert "arguments:run make --no-print-directory safe" in "".join(output)
    assert "raw-output" in "".join(output)
    assert process_runner.active is None
    events = logger.read_events()
    assert isinstance(events[0], OperationStartedEvent)
    assert any(
        isinstance(event, OperationOutputEvent) and "raw-output" in event.raw_output
        for event in events
    )
    assert isinstance(events[-1], OperationCompletedEvent)

    with pytest.raises(MakeProcessNotRunningError, match="no longer running"):
        process.request_interrupt()


@pytest.mark.parametrize(
    ("root_exists", "allowed_targets", "session_id", "message"),
    [
        (False, {"safe"}, "session", "project root"),
        (True, set(), "session", "at least one"),
        (True, {""}, "session", "must not be empty"),
        (True, {"safe"}, "", "session_id"),
    ],
)
def test_make_process_runner_rejects_invalid_configuration(
    tmp_path: Path,
    root_exists: bool,
    allowed_targets: set[str],
    session_id: str,
    message: str,
) -> None:
    project_root = tmp_path / "project"
    if root_exists:
        project_root.mkdir()
    logger = SecureJsonlLogger(
        path=tmp_path / "history" / "fpl-tui.jsonl",
        max_bytes=1_000_000,
        file_count=5,
    )

    with pytest.raises(ValueError, match=message):
        MakeProcessRunner(
            project_root=project_root,
            allowed_targets=allowed_targets,
            event_sink=logger,
            session_id=session_id,
        )


def test_make_process_runner_rejects_empty_task_id(tmp_path: Path) -> None:
    process_runner, _logger = runner(tmp_path=tmp_path, allowed_targets={"safe"})

    with pytest.raises(ValueError, match="task_id"):
        process_runner.start(
            target="safe",
            task_id="",
            on_output=lambda _value: None,
        )


def test_make_process_runner_logs_spawn_failure_and_releases_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_runner, logger = runner(tmp_path=tmp_path, allowed_targets={"safe"})

    def fail_to_spawn(*_args: object, **_kwargs: object) -> None:
        raise OSError("spawn failed")

    monkeypatch.setattr(process_runner_module.subprocess, "Popen", fail_to_spawn)

    with pytest.raises(OSError, match="spawn failed"):
        process_runner.start(
            target="safe",
            task_id="task-1",
            on_output=lambda _value: None,
        )

    assert process_runner.active is None
    events = logger.read_events()
    assert isinstance(events[0], OperationStartedEvent)
    assert isinstance(events[-1], OperationFailedEvent)
    assert events[-1].exit_code is None
    assert "spawn failed" in events[-1].traceback


def test_make_process_runner_reports_output_callback_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_uv(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        body="printf 'raw-output\\n'",
    )
    process_runner, logger = runner(tmp_path=tmp_path, allowed_targets={"safe"})

    def fail_to_display(_value: str) -> None:
        raise RuntimeError("display failed")

    process = process_runner.start(
        target="safe",
        task_id="task-1",
        on_output=fail_to_display,
    )

    with pytest.raises(MakeProcessInfrastructureError, match="could not be delivered"):
        process.wait(timeout_seconds=5)

    events = logger.read_events()
    assert isinstance(events[-1], OperationFailedEvent)
    assert events[-1].error == "display failed"
    assert "RuntimeError: display failed" in events[-1].traceback


def test_make_process_wait_rejects_non_positive_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_uv(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        body="printf 'complete\\n'",
    )
    process_runner, _logger = runner(tmp_path=tmp_path, allowed_targets={"safe"})
    process = process_runner.start(
        target="safe",
        task_id="task-1",
        on_output=lambda _value: None,
    )

    with pytest.raises(ValueError, match="greater than zero"):
        process.wait(timeout_seconds=0)
    process.wait(timeout_seconds=None)


def test_make_process_runner_rejects_target_outside_allowlist_before_execution(
    tmp_path: Path,
) -> None:
    process_runner, logger = runner(tmp_path=tmp_path, allowed_targets={"safe"})

    with pytest.raises(MakeTargetNotAllowedError, match="not present"):
        process_runner.start(
            target="safe; arbitrary-command",
            task_id="task-1",
            on_output=lambda _value: None,
        )

    assert logger.read_events() == ()


def test_make_process_runner_allows_only_one_active_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_uv(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        body="trap '' INT\nprintf 'ready\\n'\nwhile :; do sleep 1; done",
    )
    process_runner, _logger = runner(
        tmp_path=tmp_path,
        allowed_targets={"first", "second"},
    )
    ready = threading.Event()
    process = process_runner.start(
        target="first",
        task_id="task-1",
        on_output=lambda value: ready.set() if "ready" in value else None,
    )
    try:
        assert ready.wait(timeout=5)
        with pytest.raises(MakeProcessAlreadyRunningError, match="already active"):
            process_runner.start(
                target="second",
                task_id="task-2",
                on_output=lambda _value: None,
            )
    finally:
        if process.is_running:
            process.request_termination()
        process.wait(timeout_seconds=5)


def test_make_process_stop_is_interrupt_then_explicit_termination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_uv(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        body="trap '' INT\nprintf 'ready\\n'\nwhile :; do sleep 1; done",
    )
    process_runner, logger = runner(tmp_path=tmp_path, allowed_targets={"long"})
    ready = threading.Event()
    process = process_runner.start(
        target="long",
        task_id="task-1",
        on_output=lambda value: ready.set() if "ready" in value else None,
    )
    try:
        assert ready.wait(timeout=5)
        process.request_interrupt()
        with pytest.raises(MakeProcessWaitTimeoutError):
            process.wait(timeout_seconds=0.2)
        assert process.is_running

        process.request_termination()
        result = process.wait(timeout_seconds=5)
    finally:
        if process.is_running:
            process.request_termination()
            process.wait(timeout_seconds=5)

    assert result.signals == (
        OperationSignal.INTERRUPT,
        OperationSignal.TERMINATE,
    )
    events = logger.read_events()
    signals = [
        event.signal
        for event in events
        if isinstance(event, OperationSignalEvent)
    ]
    assert signals == [
        OperationSignal.INTERRUPT,
        OperationSignal.TERMINATE,
    ]
    assert isinstance(events[-1], OperationFailedEvent)
    assert events[-1].exit_code != 0
