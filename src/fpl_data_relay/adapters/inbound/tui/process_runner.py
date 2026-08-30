"""Allowlisted PTY process execution for Make-backed TUI commands."""

import codecs
import errno
import os
import select
import signal
import subprocess
import threading
import traceback as traceback_module
from collections.abc import Callable, Collection
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from fpl_data_relay.adapters.inbound.tui.logging import (
    LogParameter,
    LogStream,
    OperationCompletedEvent,
    OperationEventSink,
    OperationFailedEvent,
    OperationOutputEvent,
    OperationSignal,
    OperationSignalEvent,
    OperationStartedEvent,
)

type ProcessOutputCallback = Callable[[str], None]

_PTY_READ_SIZE = 64 * 1024
_PTY_SELECT_SECONDS = 0.1


class MakeProcessError(RuntimeError):
    """Base error for managed Make process execution."""


class MakeTargetNotAllowedError(MakeProcessError):
    """Raised before execution when a target is not in the injected catalogue."""


class MakeProcessAlreadyRunningError(MakeProcessError):
    """Raised when a second process is requested from the same runner."""


class MakeProcessNotRunningError(MakeProcessError):
    """Raised when a signal is requested after a process has exited."""


class MakeProcessWaitTimeoutError(MakeProcessError):
    """Raised when an explicit bounded wait expires."""


class MakeProcessInfrastructureError(MakeProcessError):
    """Raised when output delivery or audit persistence fails."""


class MakeProcessResult(BaseModel):
    """Immutable completion state for one managed target."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target: str
    argv: tuple[str, ...]
    exit_code: int
    started_at: datetime
    finished_at: datetime
    signals: tuple[OperationSignal, ...]


class MakeProcessRunner:
    """Start one allowlisted Make target at a time in its own process group."""

    def __init__(
        self,
        *,
        project_root: Path,
        allowed_targets: Collection[str],
        event_sink: OperationEventSink,
        session_id: str,
    ) -> None:
        root = project_root.absolute()
        if not root.is_dir():
            raise ValueError(f"project root is not a directory: {root}")
        if not session_id:
            raise ValueError("process runner session_id must not be empty")
        targets = frozenset(allowed_targets)
        if not targets:
            raise ValueError("process runner requires at least one allowed target")
        if any(not target for target in targets):
            raise ValueError("allowed Make targets must not be empty")

        self._project_root = root
        self._allowed_targets = targets
        self._event_sink = event_sink
        self._session_id = session_id
        self._lock = threading.Lock()
        self._starting = False
        self._active: ManagedMakeProcess | None = None

    @property
    def active(self) -> ManagedMakeProcess | None:
        """Return the active handle until its monitor has fully completed."""
        with self._lock:
            return self._active

    def command_for(self, *, target: str) -> tuple[str, ...]:
        """Build the only command shape this runner can execute."""
        if target not in self._allowed_targets:
            raise MakeTargetNotAllowedError(
                f"Make target is not present in the TUI catalogue: {target!r}",
            )
        return ("uv", "run", "make", "--no-print-directory", target)

    def start(
        self,
        *,
        target: str,
        task_id: str,
        on_output: ProcessOutputCallback,
    ) -> ManagedMakeProcess:
        """Start an allowlisted target and return its independently stoppable handle."""
        if not task_id:
            raise ValueError("process task_id must not be empty")
        argv = self.command_for(target=target)
        with self._lock:
            if self._starting or self._active is not None:
                raise MakeProcessAlreadyRunningError(
                    "another Make target is already active",
                )
            self._starting = True

        started_at = datetime.now(tz=UTC)
        master_descriptor = -1
        slave_descriptor = -1
        try:
            self._event_sink.write(
                event=OperationStartedEvent(
                    event="operation_started",
                    timestamp=started_at,
                    session_id=self._session_id,
                    task_id=task_id,
                    target=target,
                    parameters=(
                        LogParameter(name="target", value=target),
                        LogParameter(name="argv", value=" ".join(argv)),
                    ),
                ),
            )
            master_descriptor, slave_descriptor = os.openpty()
            process = subprocess.Popen(
                argv,
                cwd=self._project_root,
                stdin=subprocess.DEVNULL,
                stdout=slave_descriptor,
                stderr=slave_descriptor,
                close_fds=True,
                start_new_session=True,
            )
        except Exception as error:
            if master_descriptor >= 0:
                os.close(master_descriptor)
            if slave_descriptor >= 0:
                os.close(slave_descriptor)
            with self._lock:
                self._starting = False
            try:
                self._write_start_failure(
                    target=target,
                    task_id=task_id,
                    error=error,
                )
            except Exception as audit_error:
                raise MakeProcessInfrastructureError(
                    f"Make target {target!r} could not start and its failure "
                    "could not be persisted",
                ) from audit_error
            raise
        else:
            os.close(slave_descriptor)

        handle = ManagedMakeProcess(
            target=target,
            task_id=task_id,
            argv=argv,
            process=process,
            master_descriptor=master_descriptor,
            event_sink=self._event_sink,
            session_id=self._session_id,
            started_at=started_at,
            on_output=on_output,
            on_complete=self._release,
        )
        with self._lock:
            self._active = handle
            self._starting = False
        handle.begin()
        return handle

    def _write_start_failure(
        self,
        *,
        target: str,
        task_id: str,
        error: Exception,
    ) -> None:
        self._event_sink.write(
            event=OperationFailedEvent(
                event="operation_failed",
                timestamp=datetime.now(tz=UTC),
                session_id=self._session_id,
                task_id=task_id,
                target=target,
                error=str(error),
                traceback="".join(
                    traceback_module.format_exception(
                        type(error),
                        error,
                        error.__traceback__,
                    ),
                ),
                exit_code=None,
            ),
        )

    def _release(self, process: ManagedMakeProcess) -> None:
        with self._lock:
            if self._active is process:
                self._active = None


class ManagedMakeProcess:
    """Live PTY-backed Make process with explicit, bounded signal operations."""

    def __init__(
        self,
        *,
        target: str,
        task_id: str,
        argv: tuple[str, ...],
        process: subprocess.Popen[bytes],
        master_descriptor: int,
        event_sink: OperationEventSink,
        session_id: str,
        started_at: datetime,
        on_output: ProcessOutputCallback,
        on_complete: Callable[[ManagedMakeProcess], None],
    ) -> None:
        self._target = target
        self._task_id = task_id
        self._argv = argv
        self._process = process
        self._master_descriptor = master_descriptor
        self._event_sink = event_sink
        self._session_id = session_id
        self._started_at = started_at
        self._on_output = on_output
        self._on_complete = on_complete
        self._state_lock = threading.Lock()
        self._signals: list[OperationSignal] = []
        self._infrastructure_error: Exception | None = None
        self._result: MakeProcessResult | None = None
        self._completed = threading.Event()
        self._reader = threading.Thread(
            target=self._read_pty,
            name=f"tui-pty-{task_id}",
            daemon=True,
        )
        self._monitor = threading.Thread(
            target=self._monitor_process,
            name=f"tui-process-{task_id}",
            daemon=True,
        )

    @property
    def target(self) -> str:
        """Return the exact allowlisted Make target."""
        return self._target

    @property
    def argv(self) -> tuple[str, ...]:
        """Return the immutable process argument vector."""
        return self._argv

    @property
    def pid(self) -> int:
        """Return the process-group leader PID."""
        return self._process.pid

    @property
    def is_running(self) -> bool:
        """Report whether the process-group leader is still running."""
        return self._process.poll() is None

    def begin(self) -> None:
        """Begin PTY delivery and completion monitoring."""
        self._reader.start()
        self._monitor.start()

    def wait(self, *, timeout_seconds: float | None) -> MakeProcessResult:
        """Wait for completion, with the caller choosing bounded or unbounded wait."""
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("process wait timeout_seconds must be greater than zero")
        if not self._completed.wait(timeout=timeout_seconds):
            raise MakeProcessWaitTimeoutError(
                f"Make target {self._target!r} did not exit within "
                f"{timeout_seconds} seconds",
            )
        if self._infrastructure_error is not None:
            raise MakeProcessInfrastructureError(
                f"Make target {self._target!r} completed but its output or audit "
                "event could not be delivered",
            ) from self._infrastructure_error
        if self._result is None:
            raise MakeProcessInfrastructureError(
                f"Make target {self._target!r} has no completion result",
            )
        return self._result

    def request_interrupt(self) -> None:
        """Send SIGINT to the entire managed process group."""
        self._send_signal(
            native_signal=signal.SIGINT,
            operation_signal=OperationSignal.INTERRUPT,
        )

    def request_termination(self) -> None:
        """Send SIGTERM only after the caller has obtained explicit confirmation."""
        self._send_signal(
            native_signal=signal.SIGTERM,
            operation_signal=OperationSignal.TERMINATE,
        )

    def _send_signal(
        self,
        *,
        native_signal: signal.Signals,
        operation_signal: OperationSignal,
    ) -> None:
        with self._state_lock:
            if self._process.poll() is not None:
                raise MakeProcessNotRunningError(
                    f"Make target {self._target!r} is no longer running",
                )
            os.killpg(self._process.pid, native_signal)
            self._signals.append(operation_signal)
        try:
            self._event_sink.write(
                event=OperationSignalEvent(
                    event="operation_signal",
                    timestamp=datetime.now(tz=UTC),
                    session_id=self._session_id,
                    task_id=self._task_id,
                    target=self._target,
                    signal=operation_signal,
                ),
            )
        except Exception as error:
            self._record_infrastructure_error(error=error)
            raise MakeProcessInfrastructureError(
                f"signal sent to {self._target!r}, but its audit event failed",
            ) from error

    def _read_pty(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while True:
                readable, _, _ = select.select(
                    [self._master_descriptor],
                    [],
                    [],
                    _PTY_SELECT_SECONDS,
                )
                if not readable:
                    if self._process.poll() is not None:
                        break
                    continue
                try:
                    value = os.read(self._master_descriptor, _PTY_READ_SIZE)
                except OSError as error:
                    if error.errno == errno.EIO:
                        break
                    raise
                if not value:
                    break
                self._deliver_output(value=decoder.decode(value, final=False))
            final_value = decoder.decode(b"", final=True)
            if final_value:
                self._deliver_output(value=final_value)
        except Exception as error:
            self._record_infrastructure_error(error=error)
        finally:
            os.close(self._master_descriptor)

    def _deliver_output(self, *, value: str) -> None:
        try:
            self._event_sink.write(
                event=OperationOutputEvent(
                    event="operation_output",
                    timestamp=datetime.now(tz=UTC),
                    session_id=self._session_id,
                    task_id=self._task_id,
                    target=self._target,
                    stream=LogStream.PTY,
                    raw_output=value,
                ),
            )
        except Exception as error:
            self._record_infrastructure_error(error=error)
        try:
            self._on_output(value)
        except Exception as error:
            self._record_infrastructure_error(error=error)

    def _monitor_process(self) -> None:
        exit_code = self._process.wait()
        self._reader.join()
        finished_at = datetime.now(tz=UTC)
        with self._state_lock:
            signals = tuple(self._signals)
        result = MakeProcessResult(
            target=self._target,
            argv=self._argv,
            exit_code=exit_code,
            started_at=self._started_at,
            finished_at=finished_at,
            signals=signals,
        )
        try:
            self._write_completion(result=result)
        except Exception as error:
            self._record_infrastructure_error(error=error)
        finally:
            self._result = result
            self._on_complete(self)
            self._completed.set()

    def _write_completion(self, *, result: MakeProcessResult) -> None:
        if result.exit_code == 0 and self._infrastructure_error is None:
            self._event_sink.write(
                event=OperationCompletedEvent(
                    event="operation_completed",
                    timestamp=result.finished_at,
                    session_id=self._session_id,
                    task_id=self._task_id,
                    target=self._target,
                    result="Make target completed successfully",
                    exit_code=result.exit_code,
                ),
            )
            return

        infrastructure_error = self._infrastructure_error
        if infrastructure_error is None:
            error_text = f"Make target exited with status {result.exit_code}"
            traceback_text = ""
        else:
            error_text = str(infrastructure_error)
            traceback_text = "".join(
                traceback_module.format_exception(
                    type(infrastructure_error),
                    infrastructure_error,
                    infrastructure_error.__traceback__,
                ),
            )
        self._event_sink.write(
            event=OperationFailedEvent(
                event="operation_failed",
                timestamp=result.finished_at,
                session_id=self._session_id,
                task_id=self._task_id,
                target=self._target,
                error=error_text,
                traceback=traceback_text,
                exit_code=result.exit_code,
            ),
        )

    def _record_infrastructure_error(self, *, error: Exception) -> None:
        with self._state_lock:
            if self._infrastructure_error is None:
                self._infrastructure_error = error
