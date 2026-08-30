import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from fpl_data_relay.adapters.inbound.tui.logging import (
    LOG_DIRECTORY_MODE,
    LOG_FILE_MODE,
    LogParameter,
    LogStream,
    OperationCompletedEvent,
    OperationFailedEvent,
    OperationOutputEvent,
    OperationProgressEvent,
    OperationSignal,
    OperationSignalEvent,
    OperationStartedEvent,
    SecureJsonlLogger,
    UnsafeLogPathError,
)

NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)


def output_event(*, value: str) -> OperationOutputEvent:
    return OperationOutputEvent(
        event="operation_output",
        timestamp=NOW,
        session_id="session-1",
        task_id="task-1",
        target="test",
        stream=LogStream.PTY,
        raw_output=value,
    )


def test_secure_jsonl_logger_creates_private_storage_and_reads_raw_output(
    tmp_path: Path,
) -> None:
    path = tmp_path / "history" / "fpl-tui.jsonl"
    logger = SecureJsonlLogger(path=path, max_bytes=10_000, file_count=5)

    logger.write(event=output_event(value="raw output\nwith details"))

    assert stat.S_IMODE(path.parent.stat().st_mode) == LOG_DIRECTORY_MODE
    assert stat.S_IMODE(path.stat().st_mode) == LOG_FILE_MODE
    assert logger.path == path
    assert logger.read_events() == (
        output_event(value="raw output\nwith details"),
    )


def test_secure_jsonl_logger_round_trips_every_operation_event(tmp_path: Path) -> None:
    path = tmp_path / "history" / "fpl-tui.jsonl"
    logger = SecureJsonlLogger(path=path, max_bytes=100_000, file_count=5)
    events = (
        OperationStartedEvent(
            event="operation_started",
            timestamp=NOW,
            session_id="session-1",
            task_id="task-1",
            target="test",
            parameters=(LogParameter(name="reason", value="maintenance"),),
        ),
        OperationProgressEvent(
            event="operation_progress",
            timestamp=NOW,
            session_id="session-1",
            task_id="task-1",
            target="test",
            step="queues",
            progress="3 remaining",
        ),
        OperationSignalEvent(
            event="operation_signal",
            timestamp=NOW,
            session_id="session-1",
            task_id="task-1",
            target="test",
            signal=OperationSignal.INTERRUPT,
        ),
        OperationCompletedEvent(
            event="operation_completed",
            timestamp=NOW,
            session_id="session-1",
            task_id="task-1",
            target="test",
            result="complete",
            exit_code=None,
        ),
        OperationFailedEvent(
            event="operation_failed",
            timestamp=NOW,
            session_id="session-1",
            task_id="task-1",
            target="test",
            error="failure",
            traceback="traceback",
            exit_code=1,
        ),
    )

    for event in events:
        logger.write(event=event)

    assert logger.read_events() == events


def test_operation_log_events_require_timezone_aware_timestamps() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        OperationOutputEvent(
            event="operation_output",
            timestamp=datetime(2026, 8, 30, 12),
            session_id="session-1",
            task_id="task-1",
            target="test",
            stream=LogStream.PTY,
            raw_output="value",
        )


def test_secure_jsonl_logger_rotates_and_retains_requested_total_file_count(
    tmp_path: Path,
) -> None:
    path = tmp_path / "history" / "fpl-tui.jsonl"
    event_size = len(f"{output_event(value='output-0').model_dump_json()}\n".encode())
    logger = SecureJsonlLogger(path=path, max_bytes=event_size, file_count=3)

    for index in range(5):
        logger.write(event=output_event(value=f"output-{index}"))

    assert sorted(item.name for item in path.parent.iterdir()) == [
        "fpl-tui.jsonl",
        "fpl-tui.jsonl.1",
        "fpl-tui.jsonl.2",
    ]
    events = logger.read_events()
    raw_output = [
        event.raw_output
        for event in events
        if isinstance(event, OperationOutputEvent)
    ]
    assert raw_output == [
        "output-2",
        "output-3",
        "output-4",
    ]


def test_secure_jsonl_logger_with_one_file_discards_rotated_history(
    tmp_path: Path,
) -> None:
    path = tmp_path / "history" / "fpl-tui.jsonl"
    event_size = len(f"{output_event(value='old').model_dump_json()}\n".encode())
    logger = SecureJsonlLogger(path=path, max_bytes=event_size, file_count=1)

    logger.write(event=output_event(value="old"))
    logger.write(event=output_event(value="new"))

    events = logger.read_events()
    raw_output = [
        event.raw_output
        for event in events
        if isinstance(event, OperationOutputEvent)
    ]
    assert raw_output == ["new"]


def test_secure_jsonl_logger_rejects_event_larger_than_file_limit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "history" / "fpl-tui.jsonl"
    logger = SecureJsonlLogger(path=path, max_bytes=10, file_count=2)

    with pytest.raises(ValueError, match="exceeds the configured maximum"):
        logger.write(event=output_event(value="too large"))

    assert path.stat().st_size == 0


@pytest.mark.parametrize(
    ("max_bytes", "file_count", "message"),
    [
        (0, 1, "max_bytes"),
        (1, 0, "file_count"),
    ],
)
def test_secure_jsonl_logger_rejects_invalid_rotation_configuration(
    tmp_path: Path,
    max_bytes: int,
    file_count: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        SecureJsonlLogger(
            path=tmp_path / "history" / "fpl-tui.jsonl",
            max_bytes=max_bytes,
            file_count=file_count,
        )


def test_secure_jsonl_logger_rejects_unsafe_existing_directory(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "history"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)

    with pytest.raises(UnsafeLogPathError, match="mode 0700"):
        SecureJsonlLogger(
            path=directory / "fpl-tui.jsonl",
            max_bytes=10_000,
            file_count=5,
        )


def test_secure_jsonl_logger_rejects_unsafe_existing_file(tmp_path: Path) -> None:
    directory = tmp_path / "history"
    directory.mkdir(mode=0o700)
    path = directory / "fpl-tui.jsonl"
    path.write_text("public")
    path.chmod(0o644)

    with pytest.raises(UnsafeLogPathError, match="mode 0600"):
        SecureJsonlLogger(path=path, max_bytes=10_000, file_count=5)


def test_secure_jsonl_logger_rejects_symlink_file(tmp_path: Path) -> None:
    directory = tmp_path / "history"
    directory.mkdir(mode=0o700)
    destination = tmp_path / "destination.jsonl"
    destination.touch(mode=0o600)
    path = directory / "fpl-tui.jsonl"
    path.symlink_to(destination)

    with pytest.raises(UnsafeLogPathError, match="regular file"):
        SecureJsonlLogger(path=path, max_bytes=10_000, file_count=5)


def test_secure_jsonl_logger_rejects_symlink_directory(tmp_path: Path) -> None:
    destination = tmp_path / "destination"
    destination.mkdir(mode=0o700)
    directory = tmp_path / "history"
    directory.symlink_to(destination, target_is_directory=True)

    with pytest.raises(UnsafeLogPathError, match="contains a symlink"):
        SecureJsonlLogger(
            path=directory / "fpl-tui.jsonl",
            max_bytes=10_000,
            file_count=5,
        )


def test_secure_jsonl_logger_revalidates_path_before_each_write(
    tmp_path: Path,
) -> None:
    path = tmp_path / "history" / "fpl-tui.jsonl"
    logger = SecureJsonlLogger(path=path, max_bytes=10_000, file_count=5)
    path.chmod(0o644)

    with pytest.raises(UnsafeLogPathError, match="mode 0600"):
        logger.write(event=output_event(value="must not be written"))
