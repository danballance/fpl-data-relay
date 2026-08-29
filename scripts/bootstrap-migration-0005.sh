#!/usr/bin/env bash
set -Eeuo pipefail
shopt -s inherit_errexit
umask 077

SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIRECTORY}/.." && pwd -P)"
STATE_DIRECTORY="${PROJECT_ROOT}/.admin-state/migration-0005"
PHASE_FILE="${STATE_DIRECTORY}/phase"
SHA_FILE="${STATE_DIRECTORY}/sha"
REASON_FILE="${STATE_DIRECTORY}/reason"
COLLECTOR_RUNNING_FILE="${STATE_DIRECTORY}/collector-running"
COLLECTOR_STATUS_FILE="${STATE_DIRECTORY}/collector-status.txt"
SCHEDULE_STATE_FILE="${STATE_DIRECTORY}/schedules.json"
REBASELINE_RESULT_FILE="${STATE_DIRECTORY}/rebaseline-result.txt"
CURRENT_ACTION="initializing migration 0005 bootstrap"

usage() {
    printf '%s\n' \
        'Usage:' \
        '  scripts/bootstrap-migration-0005.sh prepare --sha FULL_SHA --reason TEXT --confirm production' \
        '  scripts/bootstrap-migration-0005.sh complete --confirm production' \
        '  scripts/bootstrap-migration-0005.sh status'
}

fail() {
    printf 'error: %s\n' "$1" >&2
    return 1
}

read_required_file() {
    local path="$1"
    local label="$2"
    if [[ ! -f "${path}" ]]; then
        fail "bootstrap state is missing ${label}: ${path}"
    fi
    local value
    value="$(<"${path}")"
    if [[ -z "${value}" ]]; then
        fail "bootstrap state has an empty ${label}: ${path}"
    fi
    printf '%s' "${value}"
}

read_phase() {
    read_required_file "${PHASE_FILE}" "phase"
}

write_phase() {
    local phase="$1"
    local temporary_file="${PHASE_FILE}.tmp.$$"
    printf '%s\n' "${phase}" > "${temporary_file}"
    chmod 600 "${temporary_file}"
    mv -f -- "${temporary_file}" "${PHASE_FILE}"
}

recovery_command() {
    if [[ ! -f "${PHASE_FILE}" ]]; then
        return
    fi
    local phase
    phase="$(<"${PHASE_FILE}")"
    printf 'saved_state=%s phase=%s\n' "${STATE_DIRECTORY}" "${phase}" >&2
    if [[ "${phase}" == "completed" ]]; then
        printf 'Inspect with: scripts/bootstrap-migration-0005.sh status\n' >&2
    elif [[ "${phase}" == "initialized" \
        || "${phase}" == "pausing_schedules" \
        || "${phase}" == "schedules_paused" \
        || "${phase}" == "draining_before_collector_stop" \
        || "${phase}" == "queues_drained_before_collector_stop" \
        || "${phase}" == "stopping_collector" \
        || "${phase}" == "collector_stopped" \
        || "${phase}" == "final_prepare_drain" ]]; then
        local saved_sha saved_reason
        saved_sha="$(<"${SHA_FILE}")"
        saved_reason="$(<"${REASON_FILE}")"
        printf 'Resume with: %q prepare --sha %q --reason %q --confirm production\n' \
            "${BASH_SOURCE[0]}" "${saved_sha}" "${saved_reason}" >&2
    else
        printf 'Resume with: %q complete --confirm production\n' \
            "${BASH_SOURCE[0]}" >&2
    fi
}

handle_error() {
    local exit_code=$?
    trap - ERR
    printf 'Migration 0005 bootstrap failed while %s.\n' "${CURRENT_ACTION}" >&2
    recovery_command
    exit "${exit_code}"
}

trap handle_error ERR

run_make() {
    make --no-print-directory "$@"
}

capture_make() {
    local output
    output="$(run_make "$@")"
    printf '%s\n' "${output}" >&2
    printf '%s' "${output}"
}

assert_pending_migration_0005() {
    local output
    output="$(capture_make aws-db-status)"
    if [[ "${output}" != *'schema applied=[1,2,3,4] pending=[5]'* ]]; then
        fail "expected migration status applied=[1,2,3,4] pending=[5]"
    fi
}

assert_migration_0005_applied() {
    local output
    output="$(capture_make aws-db-status)"
    if [[ "${output}" != *'schema applied=[1,2,3,4,5] pending=[]'* ]]; then
        fail "expected migration status applied=[1,2,3,4,5] pending=[]"
    fi
}

assert_dlqs_empty() {
    local output
    output="$(capture_make aws-dlqs-status)"
    local queue_name
    for queue_name in \
        fetch-dead-letter \
        result-dead-letter \
        schedule-dead-letter \
        community-dead-letter; do
        if ! grep -Eq \
            "^queue=${queue_name} .* total=0$" \
            <<< "${output}"; then
            fail "dead-letter queue ${queue_name} is missing or nonempty"
        fi
    done
    local queue_count
    queue_count="$(grep -Ec '^queue=.*-dead-letter ' <<< "${output}")"
    if [[ "${queue_count}" != "4" ]]; then
        fail "expected exactly four dead-letter queue status rows"
    fi
}

assert_schedules_disabled() {
    local output
    output="$(capture_make aws-schedules-status)"
    local schedule_count
    schedule_count="$(grep -Ec '^schedule=' <<< "${output}")"
    if [[ "${schedule_count}" == "0" ]]; then
        fail "schedule status returned no relay schedules"
    fi
    if grep -E '^schedule=' <<< "${output}" | grep -Evq ' state=DISABLED '; then
        fail "all relay schedules must remain disabled"
    fi
}

drain_working_queues() {
    local output
    output="$(capture_make aws-queues-drain)"
    local queue_name
    for queue_name in fetch result community; do
        if ! grep -Eq "^queue=${queue_name} .* total=0$" <<< "${output}"; then
            fail "working queue ${queue_name} is missing or nonempty after drain"
        fi
    done
    local queue_count
    queue_count="$(grep -Ec '^queue=(fetch|result|community) ' <<< "${output}")"
    if [[ "${queue_count}" != "3" ]]; then
        fail "expected exactly three working queue status rows after drain"
    fi
    if [[ "${output}" != *'working queues are stably empty'* ]]; then
        fail "queue drain did not report stable emptiness"
    fi
}

collector_running_from_status() {
    local output="$1"
    if grep -Eq '^collector_running=true ' <<< "${output}"; then
        printf 'true'
    elif grep -Eq '^collector_running=false ' <<< "${output}"; then
        printf 'false'
    else
        fail "NAS status did not contain one collector_running value"
    fi
}

assert_collector_running() {
    local expected="$1"
    local output actual
    output="$(capture_make nas-status)"
    actual="$(collector_running_from_status "${output}")"
    if [[ "${actual}" != "${expected}" ]]; then
        fail "expected collector_running=${expected}, found ${actual}"
    fi
}

assert_deployed_revision() {
    local expected_sha="$1"
    local output
    output="$(capture_make aws-app-revision)"
    if [[ "${output}" != *"deployed_revision=${expected_sha}"* ]]; then
        fail "application stack does not expose expected revision ${expected_sha}"
    fi
}

assert_reason_matches_maintenance() {
    local output="$1"
    local reason="$2"
    if ! grep -Fq "reason='${reason}'" <<< "${output}"; then
        fail "open maintenance reason does not match the bootstrap reason"
    fi
}

ensure_maintenance_active() {
    local reason="$1"
    local output
    output="$(capture_make aws-maintenance-status)"
    if [[ "${output}" == *' phase=active '* ]]; then
        assert_reason_matches_maintenance "${output}" "${reason}"
        return
    fi
    if [[ "${output}" == *' phase=exiting '* ]]; then
        fail "maintenance is already exiting"
    fi
    if [[ "${output}" == *' phase=entering '* ]]; then
        assert_reason_matches_maintenance "${output}" "${reason}"
    fi
    run_make prod-maintenance-begin \
        "REASON=${reason}" \
        CONFIRM=production
    output="$(capture_make aws-maintenance-status)"
    if [[ "${output}" != *' phase=active '* ]]; then
        fail "production maintenance did not become active"
    fi
    assert_reason_matches_maintenance "${output}" "${reason}"
}

ensure_maintenance_closed() {
    local reason="$1"
    local output
    output="$(capture_make aws-maintenance-status)"
    if [[ "${output}" == *' phase=closed '* ]]; then
        assert_reason_matches_maintenance "${output}" "${reason}"
        return
    fi
    if [[ "${output}" != *' phase=active '* \
        && "${output}" != *' phase=exiting '* ]]; then
        fail "expected active, exiting, or closed bootstrap maintenance"
    fi
    assert_reason_matches_maintenance "${output}" "${reason}"
    run_make prod-maintenance-end CONFIRM=production
    output="$(capture_make aws-maintenance-status)"
    if [[ "${output}" != *' phase=closed '* ]]; then
        fail "production maintenance did not close"
    fi
    assert_reason_matches_maintenance "${output}" "${reason}"
}

validate_confirmation() {
    local confirmation="$1"
    if [[ "${confirmation}" != "production" ]]; then
        fail "--confirm must be exactly 'production'"
    fi
}

require_admin_config() {
    if [[ ! -f .admin.env ]]; then
        fail ".admin.env is required for prepare and complete"
    fi
}

validate_sha() {
    local sha="$1"
    if [[ ! "${sha}" =~ ^[0-9a-f]{40}$ ]]; then
        fail "--sha must be a full 40-character lowercase Git revision"
    fi
}

validate_reason() {
    local reason="$1"
    if [[ ! "${reason}" =~ ^[A-Za-z0-9][A-Za-z0-9\ .,:_/-]*$ ]]; then
        fail "--reason must be one nonempty line using letters, numbers, spaces, or .,:_/-"
    fi
    if [[ "${reason}" == *" " ]]; then
        fail "--reason must not end with whitespace"
    fi
}

initialize_state() {
    local sha="$1"
    local reason="$2"
    local collector_running="$3"
    local collector_status="$4"
    if [[ -e "${STATE_DIRECTORY}" ]]; then
        fail "bootstrap state already exists: ${STATE_DIRECTORY}"
    fi
    mkdir -p -- "${PROJECT_ROOT}/.admin-state"
    chmod 700 "${PROJECT_ROOT}/.admin-state"
    mkdir -- "${STATE_DIRECTORY}"
    chmod 700 "${STATE_DIRECTORY}"
    printf '%s\n' "${sha}" > "${SHA_FILE}"
    printf '%s\n' "${reason}" > "${REASON_FILE}"
    printf '%s\n' "${collector_running}" > "${COLLECTOR_RUNNING_FILE}"
    printf '%s\n' "${collector_status}" > "${COLLECTOR_STATUS_FILE}"
    chmod 600 \
        "${SHA_FILE}" \
        "${REASON_FILE}" \
        "${COLLECTOR_RUNNING_FILE}" \
        "${COLLECTOR_STATUS_FILE}"
    write_phase initialized
}

validate_saved_prepare_arguments() {
    local sha="$1"
    local reason="$2"
    local saved_sha saved_reason
    saved_sha="$(read_required_file "${SHA_FILE}" "revision")"
    saved_reason="$(read_required_file "${REASON_FILE}" "reason")"
    if [[ "${sha}" != "${saved_sha}" || "${reason}" != "${saved_reason}" ]]; then
        fail "prepare arguments differ from the immutable saved bootstrap state"
    fi
}

print_deployment_handoff() {
    local sha="$1"
    printf '\nProduction is quiesced for migration 0005.\n'
    printf '1. Push revision %s to main.\n' "${sha}"
    printf '2. Run the Deploy production workflow for that revision in GitHub Actions.\n'
    printf '3. Wait for the workflow to complete successfully.\n'
    printf '4. Continue with:\n'
    printf '   scripts/bootstrap-migration-0005.sh complete --confirm production\n'
    printf 'Saved recovery state: %s\n' "${STATE_DIRECTORY}"
}

prepare_workflow() {
    local sha="$1"
    local reason="$2"
    if [[ ! -e "${STATE_DIRECTORY}" ]]; then
        CURRENT_ACTION="running prepare preflight checks"
        run_make aws-doctor
        run_make nas-doctor
        assert_pending_migration_0005
        assert_dlqs_empty
        local collector_output collector_running
        collector_output="$(capture_make nas-status)"
        collector_running="$(collector_running_from_status "${collector_output}")"
        initialize_state \
            "${sha}" \
            "${reason}" \
            "${collector_running}" \
            "${collector_output}"
    else
        validate_saved_prepare_arguments "${sha}" "${reason}"
        run_make aws-doctor
        run_make nas-doctor
    fi

    while true; do
        local phase collector_running
        phase="$(read_phase)"
        collector_running="$(read_required_file \
            "${COLLECTOR_RUNNING_FILE}" \
            "collector state")"
        case "${phase}" in
            initialized|pausing_schedules)
                CURRENT_ACTION="capturing and pausing schedules"
                write_phase pausing_schedules
                run_make aws-schedules-bootstrap-pause \
                    "STATE_FILE=${SCHEDULE_STATE_FILE}" \
                    CONFIRM=production
                write_phase schedules_paused
                ;;
            schedules_paused|draining_before_collector_stop)
                CURRENT_ACTION="draining queues before stopping the collector"
                write_phase draining_before_collector_stop
                drain_working_queues
                write_phase queues_drained_before_collector_stop
                ;;
            queues_drained_before_collector_stop|stopping_collector)
                CURRENT_ACTION="stopping the collector"
                write_phase stopping_collector
                if [[ "${collector_running}" == "true" ]]; then
                    run_make nas-stop CONFIRM=production
                elif [[ "${collector_running}" != "false" ]]; then
                    fail "saved collector state must be true or false"
                fi
                assert_collector_running false
                write_phase collector_stopped
                ;;
            collector_stopped|final_prepare_drain)
                CURRENT_ACTION="confirming final stable queue emptiness"
                write_phase final_prepare_drain
                drain_working_queues
                assert_dlqs_empty
                assert_schedules_disabled
                write_phase prepared
                ;;
            prepared)
                print_deployment_handoff "${sha}"
                return
                ;;
            *)
                fail "phase ${phase} cannot be resumed with prepare"
                ;;
        esac
    done
}

verify_deployment() {
    local sha="$1"
    CURRENT_ACTION="verifying the GitHub deployment"
    run_make aws-doctor
    run_make nas-doctor
    assert_deployed_revision "${sha}"
    assert_migration_0005_applied
    run_make aws-schedules-bootstrap-pause \
        "STATE_FILE=${SCHEDULE_STATE_FILE}" \
        CONFIRM=production
    assert_schedules_disabled
    assert_collector_running false
    drain_working_queues
    assert_dlqs_empty
}

complete_workflow() {
    local sha reason collector_was_running
    sha="$(read_required_file "${SHA_FILE}" "revision")"
    reason="$(read_required_file "${REASON_FILE}" "reason")"
    collector_was_running="$(read_required_file \
        "${COLLECTOR_RUNNING_FILE}" \
        "collector state")"
    validate_sha "${sha}"
    validate_reason "${reason}"
    if [[ "${collector_was_running}" != "true" \
        && "${collector_was_running}" != "false" ]]; then
        fail "saved collector state must be true or false"
    fi

    while true; do
        local phase
        phase="$(read_phase)"
        case "${phase}" in
            prepared|verifying_deployment)
                write_phase verifying_deployment
                verify_deployment "${sha}"
                write_phase deployment_verified
                ;;
            deployment_verified|beginning_maintenance)
                CURRENT_ACTION="establishing audited production maintenance"
                write_phase beginning_maintenance
                ensure_maintenance_active "${reason}"
                write_phase maintenance_active
                ;;
            maintenance_active|updating_collector)
                CURRENT_ACTION="updating the NAS collector"
                write_phase updating_collector
                local update_output
                update_output="$(capture_make nas-update \
                    "SHA=${sha}" \
                    CONFIRM=production)"
                if [[ "${update_output}" != *'collector_running=true '* \
                    || "${update_output}" != *"sha-${sha}"* ]]; then
                    fail "NAS update did not start the expected collector image"
                fi
                write_phase collector_updated
                ;;
            collector_updated|rebaselining)
                CURRENT_ACTION="refreshing normalized data and rebaselining"
                write_phase rebaselining
                local rebaseline_output temporary_result
                rebaseline_output="$(capture_make prod-rebaseline-current \
                    "REASON=${reason}" \
                    CONFIRM=production)"
                if [[ "${rebaseline_output}" != *'rebaseline_id='* ]]; then
                    fail "rebaseline command did not report an audit identifier"
                fi
                temporary_result="${REBASELINE_RESULT_FILE}.tmp.$$"
                printf '%s\n' "${rebaseline_output}" > "${temporary_result}"
                chmod 600 "${temporary_result}"
                mv -f -- "${temporary_result}" "${REBASELINE_RESULT_FILE}"
                assert_collector_running false
                write_phase rebaselined
                ;;
            rebaselined|ending_maintenance)
                CURRENT_ACTION="closing audited production maintenance"
                write_phase ending_maintenance
                ensure_maintenance_closed "${reason}"
                assert_collector_running false
                write_phase maintenance_closed
                ;;
            maintenance_closed|restoring_collector)
                CURRENT_ACTION="restoring the original collector state"
                write_phase restoring_collector
                if [[ "${collector_was_running}" == "true" ]]; then
                    run_make nas-start CONFIRM=production
                fi
                assert_collector_running "${collector_was_running}"
                write_phase collector_restored
                ;;
            collector_restored|restoring_schedules)
                CURRENT_ACTION="restoring the original schedule states"
                write_phase restoring_schedules
                run_make aws-schedules-bootstrap-restore \
                    "STATE_FILE=${SCHEDULE_STATE_FILE}" \
                    CONFIRM=production
                write_phase schedules_restored
                ;;
            schedules_restored|sending_reference)
                if [[ "${collector_was_running}" == "true" ]]; then
                    CURRENT_ACTION="sending the final reference reconciliation"
                    write_phase sending_reference
                    run_make aws-send-reference
                fi
                write_phase reference_sent
                ;;
            reference_sent|draining_final_reference)
                if [[ "${collector_was_running}" == "true" ]]; then
                    CURRENT_ACTION="draining the final reference reconciliation"
                    write_phase draining_final_reference
                    drain_working_queues
                fi
                write_phase final_checks
                ;;
            final_checks)
                CURRENT_ACTION="running final production checks"
                run_make prod-status
                assert_dlqs_empty
                write_phase completed
                ;;
            completed)
                printf 'Migration 0005 bootstrap is complete.\n'
                printf 'revision=%s state=%s\n' "${sha}" "${STATE_DIRECTORY}"
                if [[ -f "${REBASELINE_RESULT_FILE}" ]]; then
                    cat "${REBASELINE_RESULT_FILE}"
                fi
                return
                ;;
            *)
                fail "phase ${phase} cannot be resumed with complete"
                ;;
        esac
    done
}

show_status() {
    if [[ ! -e "${STATE_DIRECTORY}" ]]; then
        printf 'phase=not-started state=%s\n' "${STATE_DIRECTORY}"
        return
    fi
    local phase sha reason collector_running
    phase="$(read_phase)"
    sha="$(read_required_file "${SHA_FILE}" "revision")"
    reason="$(read_required_file "${REASON_FILE}" "reason")"
    collector_running="$(read_required_file \
        "${COLLECTOR_RUNNING_FILE}" \
        "collector state")"
    printf 'phase=%s\n' "${phase}"
    printf 'revision=%s\n' "${sha}"
    printf 'reason=%s\n' "${reason}"
    printf 'collector_was_running=%s\n' "${collector_running}"
    printf 'collector_status=%s\n' "${COLLECTOR_STATUS_FILE}"
    printf 'schedule_snapshot=%s exists=%s\n' \
        "${SCHEDULE_STATE_FILE}" \
        "$([[ -f "${SCHEDULE_STATE_FILE}" ]] && printf true || printf false)"
    printf 'state=%s\n' "${STATE_DIRECTORY}"
    if [[ -f "${REBASELINE_RESULT_FILE}" ]]; then
        cat "${REBASELINE_RESULT_FILE}"
    fi
}

parse_prepare() {
    local sha=""
    local reason=""
    local confirmation=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --sha)
                [[ $# -ge 2 ]] || fail "--sha requires a value"
                sha="$2"
                shift 2
                ;;
            --reason)
                [[ $# -ge 2 ]] || fail "--reason requires a value"
                reason="$2"
                shift 2
                ;;
            --confirm)
                [[ $# -ge 2 ]] || fail "--confirm requires a value"
                confirmation="$2"
                shift 2
                ;;
            *)
                fail "unknown prepare argument: $1"
                ;;
        esac
    done
    validate_sha "${sha}"
    validate_reason "${reason}"
    validate_confirmation "${confirmation}"
    require_admin_config
    prepare_workflow "${sha}" "${reason}"
}

parse_complete() {
    local confirmation=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --confirm)
                [[ $# -ge 2 ]] || fail "--confirm requires a value"
                confirmation="$2"
                shift 2
                ;;
            *)
                fail "unknown complete argument: $1"
                ;;
        esac
    done
    validate_confirmation "${confirmation}"
    require_admin_config
    if [[ ! -d "${STATE_DIRECTORY}" ]]; then
        fail "prepare has not created bootstrap state"
    fi
    complete_workflow
}

cd -- "${PROJECT_ROOT}"
if [[ ! -f Makefile ]]; then
    fail "run from a project checkout containing Makefile"
fi

if [[ $# -lt 1 ]]; then
    usage >&2
    exit 2
fi

COMMAND="$1"
shift
case "${COMMAND}" in
    prepare)
        parse_prepare "$@"
        ;;
    complete)
        parse_complete "$@"
        ;;
    status)
        if [[ $# -ne 0 ]]; then
            fail "status does not accept arguments"
        fi
        show_status
        ;;
    *)
        usage >&2
        fail "unknown command: ${COMMAND}"
        ;;
esac
