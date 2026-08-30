"""Secure structured operation logging for the terminal interface."""

import errno
import os
import stat
import threading
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

LOG_DIRECTORY_MODE = 0o700
LOG_FILE_MODE = 0o600


class UnsafeLogPathError(ValueError):
    """Raised when an operation log path is not private and regular."""


class LogStream(StrEnum):
    """Source of raw operation output."""

    PTY = "pty"
    STDOUT = "stdout"
    STDERR = "stderr"


class OperationSignal(StrEnum):
    """Signals the TUI is permitted to send to a managed process."""

    INTERRUPT = "SIGINT"
    TERMINATE = "SIGTERM"


class LogParameter(BaseModel):
    """One deliberately selected, non-secret operation parameter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    value: str


class _OperationEvent(BaseModel):
    """Fields shared by every persisted operation event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timestamp: datetime
    session_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    target: str = Field(min_length=1)

    @field_validator("timestamp")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        """Reject local timestamps whose ordering would be ambiguous."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("operation log timestamps must be timezone-aware")
        return value


class OperationStartedEvent(_OperationEvent):
    """An operation was accepted and is about to execute."""

    event: Literal["operation_started"]
    parameters: tuple[LogParameter, ...]


class OperationProgressEvent(_OperationEvent):
    """A named operation step made observable progress."""

    event: Literal["operation_progress"]
    step: str = Field(min_length=1)
    progress: str


class OperationOutputEvent(_OperationEvent):
    """Raw output displayed by the TUI."""

    event: Literal["operation_output"]
    stream: LogStream
    raw_output: str


class OperationCompletedEvent(_OperationEvent):
    """An operation completed successfully."""

    event: Literal["operation_completed"]
    result: str
    exit_code: int | None


class OperationFailedEvent(_OperationEvent):
    """An operation failed, including its diagnostic traceback when available."""

    event: Literal["operation_failed"]
    error: str
    traceback: str
    exit_code: int | None


class OperationSignalEvent(_OperationEvent):
    """A permitted signal was sent to a managed process group."""

    event: Literal["operation_signal"]
    signal: OperationSignal


type OperationLogEvent = Annotated[
    OperationStartedEvent
    | OperationProgressEvent
    | OperationOutputEvent
    | OperationCompletedEvent
    | OperationFailedEvent
    | OperationSignalEvent,
    Field(discriminator="event"),
]

OPERATION_LOG_EVENT_ADAPTER = TypeAdapter(OperationLogEvent)


class OperationEventSink(Protocol):
    """Persistence boundary used by process and administration operations."""

    def write(self, *, event: OperationLogEvent) -> None:
        """Persist one complete event."""
        ...


class SecureJsonlLogger:
    """Append and rotate private JSONL operation history."""

    def __init__(
        self,
        *,
        path: Path,
        max_bytes: int,
        file_count: int,
    ) -> None:
        if max_bytes < 1:
            raise ValueError("log max_bytes must be at least 1")
        if file_count < 1:
            raise ValueError("log file_count must be at least 1")
        if not path.name:
            raise ValueError("log path must name a file")

        self._path = path.absolute()
        self._max_bytes = max_bytes
        self._file_count = file_count
        self._lock = threading.Lock()
        self._prepare_storage()

    @property
    def path(self) -> Path:
        """Return the current JSONL file path."""
        return self._path

    def write(self, *, event: OperationLogEvent) -> None:
        """Append one event, rotating first when the size limit is reached."""
        encoded = f"{event.model_dump_json()}\n".encode()
        if len(encoded) > self._max_bytes:
            raise ValueError(
                "One operation log event exceeds the configured maximum file size: "
                f"{len(encoded)} > {self._max_bytes} bytes.",
            )
        with self._lock:
            current_size = self._validated_file_size(path=self._path)
            if current_size > 0 and current_size + len(encoded) > self._max_bytes:
                self._rotate()
            descriptor = self._open_existing_file(path=self._path)
            try:
                self._write_all(descriptor=descriptor, value=encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def read_events(self) -> tuple[OperationLogEvent, ...]:
        """Read retained events from oldest archive through the current file."""
        with self._lock:
            events: list[OperationLogEvent] = []
            for path in self._history_paths_oldest_first():
                self._validate_regular_file(path=path)
                with path.open("rb") as source:
                    for line in source:
                        events.append(OPERATION_LOG_EVENT_ADAPTER.validate_json(line))
            return tuple(events)

    def _prepare_storage(self) -> None:
        self._reject_symlink_components(path=self._path.parent)
        directory_created = False
        try:
            self._path.parent.mkdir(parents=True, mode=LOG_DIRECTORY_MODE)
            directory_created = True
        except FileExistsError:
            pass
        if directory_created:
            os.chmod(self._path.parent, LOG_DIRECTORY_MODE)
        self._validate_directory(path=self._path.parent)

        for path in self._all_history_paths():
            if path.exists() or path.is_symlink():
                self._validate_regular_file(path=path)
        if not self._path.exists():
            self._create_file(path=self._path)
        self._validate_regular_file(path=self._path)

    def _rotate(self) -> None:
        for path in self._all_history_paths():
            if path.exists() or path.is_symlink():
                self._validate_regular_file(path=path)

        archive_count = self._file_count - 1
        if archive_count == 0:
            self._path.unlink()
            self._create_file(path=self._path)
            return

        oldest = self._archive_path(index=archive_count)
        if oldest.exists():
            oldest.unlink()
        for index in range(archive_count - 1, 0, -1):
            source = self._archive_path(index=index)
            if source.exists():
                os.replace(source, self._archive_path(index=index + 1))
        os.replace(self._path, self._archive_path(index=1))
        self._create_file(path=self._path)

    def _all_history_paths(self) -> tuple[Path, ...]:
        return (
            self._path,
            *(
                self._archive_path(index=index)
                for index in range(1, self._file_count)
            ),
        )

    def _history_paths_oldest_first(self) -> tuple[Path, ...]:
        archives = tuple(
            path
            for path in (
                self._archive_path(index=index)
                for index in range(self._file_count - 1, 0, -1)
            )
            if path.exists() or path.is_symlink()
        )
        return (*archives, self._path)

    def _archive_path(self, *, index: int) -> Path:
        return self._path.with_name(f"{self._path.name}.{index}")

    @staticmethod
    def _reject_symlink_components(*, path: Path) -> None:
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode):
                raise UnsafeLogPathError(
                    f"operation log path contains a symlink: {current}",
                )

    @staticmethod
    def _validate_directory(*, path: Path) -> None:
        try:
            metadata = path.lstat()
        except FileNotFoundError as error:
            raise UnsafeLogPathError(
                f"operation log directory does not exist: {path}",
            ) from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise UnsafeLogPathError(
                f"operation log directory must be a regular directory: {path}",
            )
        mode = stat.S_IMODE(metadata.st_mode)
        if mode != LOG_DIRECTORY_MODE:
            raise UnsafeLogPathError(
                f"operation log directory must have mode 0700, found {mode:04o}: "
                f"{path}",
            )

    @staticmethod
    def _validate_regular_file(*, path: Path) -> None:
        try:
            metadata = path.lstat()
        except FileNotFoundError as error:
            raise UnsafeLogPathError(
                f"operation log file does not exist: {path}",
            ) from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise UnsafeLogPathError(
                f"operation log path must be a regular file: {path}",
            )
        mode = stat.S_IMODE(metadata.st_mode)
        if mode != LOG_FILE_MODE:
            raise UnsafeLogPathError(
                f"operation log file must have mode 0600, found {mode:04o}: "
                f"{path}",
            )

    def _validated_file_size(self, *, path: Path) -> int:
        self._validate_regular_file(path=path)
        return path.stat().st_size

    @staticmethod
    def _create_file(*, path: Path) -> None:
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, LOG_FILE_MODE)
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.EMLINK}:
                raise UnsafeLogPathError(
                    f"operation log path must not be a symlink: {path}",
                ) from error
            raise
        try:
            os.fchmod(descriptor, LOG_FILE_MODE)
        finally:
            os.close(descriptor)

    @classmethod
    def _open_existing_file(cls, *, path: Path) -> int:
        flags = os.O_WRONLY | os.O_APPEND
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.EMLINK}:
                raise UnsafeLogPathError(
                    f"operation log path must not be a symlink: {path}",
                ) from error
            raise
        try:
            metadata = os.fstat(descriptor)
            mode = stat.S_IMODE(metadata.st_mode)
            if not stat.S_ISREG(metadata.st_mode) or mode != LOG_FILE_MODE:
                raise UnsafeLogPathError(
                    "operation log descriptor is not a regular 0600 file: "
                    f"{path}",
                )
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    @staticmethod
    def _write_all(*, descriptor: int, value: bytes) -> None:
        view = memoryview(value)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
