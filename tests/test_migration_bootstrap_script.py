from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts/bootstrap-migration-0005.sh"
SHA = "a" * 40
REASON = "season start correction"

FAKE_MAKE = r"""#!/usr/bin/env bash
set -eu

if [ "${1:-}" = "--no-print-directory" ]; then
    shift
fi
target="$1"
shift
printf '%s %s\n' "$target" "$*" >> "$FAKE_MAKE_LOG"

if [ "${FAKE_FAIL_ONCE_TARGET:-}" = "$target" ]; then
    marker="$FAKE_RUNTIME_DIRECTORY/failed-$target"
    if [ ! -e "$marker" ]; then
        : > "$marker"
        printf 'simulated failure for %s\n' "$target" >&2
        exit 42
    fi
fi

case "$target" in
    aws-doctor)
        printf 'AWS administration checks passed\n'
        ;;
    nas-doctor)
        printf 'NAS administration checks passed\n'
        ;;
    aws-db-status)
        if [ "${FAKE_SCHEMA_STATE:-pending}" = "applied" ]; then
            printf 'schema applied=[1,2,3,4,5] pending=[]\n'
        else
            printf 'schema applied=[1,2,3,4] pending=[5]\n'
        fi
        ;;
    aws-dlqs-status)
        for name in fetch result schedule community; do
            total=0
            if [ "${FAKE_NONEMPTY_DLQ:-}" = "$name" ]; then
                total=1
            fi
            printf 'queue=%s-dead-letter visible=%s in_flight=0 delayed=0 total=%s\n' \
                "$name" "$total" "$total"
        done
        ;;
    nas-status)
        running="$(cat "$FAKE_COLLECTOR_STATE")"
        if [ "$running" = true ]; then
            health=healthy
        else
            health=stopped
        fi
        printf 'collector_running=%s health=%s image=collector:old\n' \
            "$running" "$health"
        ;;
    aws-schedules-bootstrap-pause)
        for argument in "$@"; do
            case "$argument" in
                STATE_FILE=*) state_file="${argument#STATE_FILE=}" ;;
            esac
        done
        test -n "${state_file:-}"
        if [ ! -e "$state_file" ]; then
            printf '{"snapshot":"original"}\n' > "$state_file"
        fi
        printf 'schedule_snapshot=%s schedules=3 state=disabled\n' "$state_file"
        ;;
    aws-schedules-bootstrap-restore)
        if [ "${FAKE_REJECT_SNAPSHOT:-false}" = true ]; then
            printf 'schedule snapshot is invalid\n' >&2
            exit 1
        fi
        printf 'schedule_snapshot=restored schedules=3 state=restored\n'
        ;;
    aws-schedules-status)
        printf 'schedule=app-reference/reference state=DISABLED expression=cron\n'
        printf 'schedule=app-community/community state=DISABLED expression=cron\n'
        printf 'schedule=app-live/fpl-live-test state=DISABLED expression=at\n'
        ;;
    aws-queues-drain)
        printf 'queue=fetch visible=0 in_flight=0 delayed=0 total=0\n'
        printf 'queue=result visible=0 in_flight=0 delayed=0 total=0\n'
        printf 'queue=community visible=0 in_flight=0 delayed=0 total=0\n'
        printf 'working queues are stably empty\n'
        ;;
    nas-stop)
        printf 'false\n' > "$FAKE_COLLECTOR_STATE"
        printf 'collector_running=false health=stopped image=collector:old\n'
        ;;
    aws-app-revision)
        printf 'deployed_revision=%s\n' "${FAKE_REVISION:-missing}"
        ;;
    aws-maintenance-status)
        if [ ! -f "$FAKE_MAINTENANCE_PHASE" ]; then
            printf 'maintenance=none\n'
        else
            phase="$(cat "$FAKE_MAINTENANCE_PHASE")"
            reason="$(cat "$FAKE_MAINTENANCE_REASON")"
            printf "maintenance_id=1 phase=%s operator=arn:operator reason='%s'\n" \
                "$phase" "$reason"
        fi
        ;;
    prod-maintenance-begin)
        for argument in "$@"; do
            case "$argument" in
                REASON=*) reason="${argument#REASON=}" ;;
            esac
        done
        printf '%s\n' "$reason" > "$FAKE_MAINTENANCE_REASON"
        printf 'active\n' > "$FAKE_MAINTENANCE_PHASE"
        printf "maintenance_id=1 phase=active operator=arn:operator reason='%s'\n" \
            "$reason"
        ;;
    nas-update)
        for argument in "$@"; do
            case "$argument" in
                SHA=*) sha="${argument#SHA=}" ;;
            esac
        done
        printf 'true\n' > "$FAKE_COLLECTOR_STATE"
        printf 'collector_running=true health=healthy image=collector:sha-%s\n' "$sha"
        ;;
    prod-rebaseline-current)
        printf 'false\n' > "$FAKE_COLLECTOR_STATE"
        printf '%s%s\n' \
            'rebaseline_id=8 season=2026-27 change_events_deleted=10 ' \
            'entity_changes_deleted=20 snapshots_rebuilt=30'
        ;;
    prod-maintenance-end)
        printf 'closed\n' > "$FAKE_MAINTENANCE_PHASE"
        printf "maintenance_id=1 phase=closed operator=arn:operator reason='%s'\n" \
            "$(cat "$FAKE_MAINTENANCE_REASON")"
        ;;
    nas-start)
        printf 'true\n' > "$FAKE_COLLECTOR_STATE"
        printf 'collector_running=true health=healthy image=collector:new\n'
        ;;
    aws-send-reference)
        printf 'reference sent message_id=reference-1\n'
        ;;
    prod-status)
        printf 'schema applied=[1,2,3,4,5] pending=[]\n'
        printf 'maintenance=none\n'
        ;;
    *)
        printf 'unexpected fake make target: %s\n' "$target" >&2
        exit 2
        ;;
esac
"""


def bootstrap_checkout(
    *,
    tmp_path: Path,
    collector_running: bool,
) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "checkout"
    scripts = root / "scripts"
    fake_bin = root / "fake-bin"
    runtime = root / "fake-runtime"
    scripts.mkdir(parents=True)
    fake_bin.mkdir()
    runtime.mkdir()
    copied_script = scripts / SCRIPT.name
    shutil.copy2(SCRIPT, copied_script)
    copied_script.chmod(0o755)
    (root / "Makefile").write_text("help:\n\t@true\n", encoding="utf-8")
    (root / ".admin.env").write_text("configured=true\n", encoding="utf-8")
    fake_make = fake_bin / "make"
    fake_make.write_text(FAKE_MAKE, encoding="utf-8")
    fake_make.chmod(0o755)
    collector_state = runtime / "collector"
    collector_state.write_text(
        "true\n" if collector_running else "false\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "FAKE_MAKE_LOG": str(runtime / "make.log"),
            "FAKE_RUNTIME_DIRECTORY": str(runtime),
            "FAKE_COLLECTOR_STATE": str(collector_state),
            "FAKE_MAINTENANCE_PHASE": str(runtime / "maintenance-phase"),
            "FAKE_MAINTENANCE_REASON": str(runtime / "maintenance-reason"),
            "FAKE_SCHEMA_STATE": "pending",
            "FAKE_REVISION": SHA,
        },
    )
    return copied_script, environment


def invoke(
    *,
    script: Path,
    environment: dict[str, str],
    arguments: list[str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(script), *arguments],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )


def prepare(
    *,
    script: Path,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return invoke(
        script=script,
        environment=environment,
        arguments=[
            "prepare",
            "--sha",
            SHA,
            "--reason",
            REASON,
            "--confirm",
            "production",
        ],
    )


def complete(
    *,
    script: Path,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return invoke(
        script=script,
        environment=environment,
        arguments=["complete", "--confirm", "production"],
    )


def state_directory(*, script: Path) -> Path:
    return script.parents[1] / ".admin-state/migration-0005"


def test_bootstrap_script_has_valid_bash_syntax_and_is_executable() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert SCRIPT.stat().st_mode & 0o111


def test_prepare_quiesces_in_order_and_preserves_immutable_state(
    tmp_path: Path,
) -> None:
    script, environment = bootstrap_checkout(
        tmp_path=tmp_path,
        collector_running=True,
    )
    result = prepare(script=script, environment=environment)
    assert result.returncode == 0, result.stderr
    assert "Production is quiesced" in result.stdout
    assert "Deploy production" in result.stdout
    state = state_directory(script=script)
    assert (state / "phase").read_text().strip() == "prepared"
    assert (state / "sha").read_text().strip() == SHA
    assert (state / "reason").read_text().strip() == REASON
    assert (state / "collector-running").read_text().strip() == "true"
    assert (state / "schedules.json").is_file()
    assert (state / "schedules.json").stat().st_mode & 0o777 == 0o600

    log = Path(environment["FAKE_MAKE_LOG"]).read_text().splitlines()
    pause_index = next(
        index
        for index, line in enumerate(log)
        if line.startswith("aws-schedules-bootstrap-pause ")
    )
    stop_index = next(
        index for index, line in enumerate(log) if line.startswith("nas-stop ")
    )
    drains = [
        index
        for index, line in enumerate(log)
        if line.startswith("aws-queues-drain ")
    ]
    assert pause_index < drains[0] < stop_index < drains[1]

    repeated = prepare(script=script, environment=environment)
    assert repeated.returncode == 0, repeated.stderr
    assert (state / "schedules.json").read_text() == '{"snapshot":"original"}\n'


def test_prepare_resumes_after_partial_pause_and_rejects_bad_inputs(
    tmp_path: Path,
) -> None:
    script, environment = bootstrap_checkout(
        tmp_path=tmp_path,
        collector_running=True,
    )
    environment["FAKE_FAIL_ONCE_TARGET"] = "aws-schedules-bootstrap-pause"
    failed = prepare(script=script, environment=environment)
    assert failed.returncode == 42
    assert "phase=pausing_schedules" in failed.stderr
    resumed = prepare(script=script, environment=environment)
    assert resumed.returncode == 0, resumed.stderr

    mismatch = invoke(
        script=script,
        environment=environment,
        arguments=[
            "prepare",
            "--sha",
            SHA,
            "--reason",
            "different reason",
            "--confirm",
            "production",
        ],
    )
    assert mismatch.returncode != 0
    assert "differ from the immutable" in mismatch.stderr

    bad_confirmation = invoke(
        script=script,
        environment=environment,
        arguments=["complete", "--confirm", "yes"],
    )
    assert bad_confirmation.returncode != 0
    assert "exactly 'production'" in bad_confirmation.stderr


def test_prepare_rejects_nonempty_dlq_without_creating_state(
    tmp_path: Path,
) -> None:
    script, environment = bootstrap_checkout(
        tmp_path=tmp_path,
        collector_running=True,
    )
    environment["FAKE_NONEMPTY_DLQ"] = "result"
    result = prepare(script=script, environment=environment)
    assert result.returncode != 0
    assert "result-dead-letter is missing or nonempty" in result.stderr
    assert not state_directory(script=script).exists()


@pytest.mark.parametrize("collector_running", [True, False])
def test_complete_refreshes_rebaselines_and_restores_original_state(
    tmp_path: Path,
    collector_running: bool,
) -> None:
    script, environment = bootstrap_checkout(
        tmp_path=tmp_path,
        collector_running=collector_running,
    )
    prepared = prepare(script=script, environment=environment)
    assert prepared.returncode == 0, prepared.stderr
    environment["FAKE_SCHEMA_STATE"] = "applied"

    result = complete(script=script, environment=environment)
    assert result.returncode == 0, result.stderr
    assert "Migration 0005 bootstrap is complete" in result.stdout
    assert "rebaseline_id=8" in result.stdout
    state = state_directory(script=script)
    assert (state / "phase").read_text().strip() == "completed"

    log = Path(environment["FAKE_MAKE_LOG"]).read_text().splitlines()
    positions = {
        target: next(
            index
            for index, line in enumerate(log)
            if line.startswith(f"{target} ")
        )
        for target in (
            "prod-maintenance-begin",
            "nas-update",
            "prod-rebaseline-current",
            "prod-maintenance-end",
            "aws-schedules-bootstrap-restore",
            "prod-status",
        )
    }
    assert list(positions.values()) == sorted(positions.values())
    final_running = Path(environment["FAKE_COLLECTOR_STATE"]).read_text().strip()
    assert final_running == str(collector_running).lower()
    targets = [line.split(maxsplit=1)[0] for line in log]
    assert ("nas-start" in targets) is collector_running
    assert ("aws-send-reference" in targets) is collector_running


def test_complete_rejects_revision_and_schema_mismatches_then_resumes(
    tmp_path: Path,
) -> None:
    script, environment = bootstrap_checkout(
        tmp_path=tmp_path,
        collector_running=True,
    )
    assert prepare(script=script, environment=environment).returncode == 0

    environment["FAKE_REVISION"] = "b" * 40
    revision_failure = complete(script=script, environment=environment)
    assert revision_failure.returncode != 0
    assert "does not expose expected revision" in revision_failure.stderr
    assert (
        state_directory(script=script) / "phase"
    ).read_text().strip() == "verifying_deployment"

    environment["FAKE_REVISION"] = SHA
    schema_failure = complete(script=script, environment=environment)
    assert schema_failure.returncode != 0
    assert "expected migration status" in schema_failure.stderr

    environment["FAKE_SCHEMA_STATE"] = "applied"
    resumed = complete(script=script, environment=environment)
    assert resumed.returncode == 0, resumed.stderr


def test_complete_retains_recovery_phase_when_snapshot_restore_fails(
    tmp_path: Path,
) -> None:
    script, environment = bootstrap_checkout(
        tmp_path=tmp_path,
        collector_running=False,
    )
    assert prepare(script=script, environment=environment).returncode == 0
    environment["FAKE_SCHEMA_STATE"] = "applied"
    environment["FAKE_REJECT_SNAPSHOT"] = "true"
    failed = complete(script=script, environment=environment)
    assert failed.returncode != 0
    assert "phase=restoring_schedules" in failed.stderr

    environment["FAKE_REJECT_SNAPSHOT"] = "false"
    resumed = complete(script=script, environment=environment)
    assert resumed.returncode == 0, resumed.stderr


def test_status_reports_not_started_and_completed_state(tmp_path: Path) -> None:
    script, environment = bootstrap_checkout(
        tmp_path=tmp_path,
        collector_running=False,
    )
    not_started = invoke(
        script=script,
        environment=environment,
        arguments=["status"],
    )
    assert not_started.returncode == 0
    assert "phase=not-started" in not_started.stdout

    assert prepare(script=script, environment=environment).returncode == 0
    status = invoke(
        script=script,
        environment=environment,
        arguments=["status"],
    )
    assert status.returncode == 0, status.stderr
    assert "phase=prepared" in status.stdout
    assert f"revision={SHA}" in status.stdout
