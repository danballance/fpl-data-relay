#!/bin/sh
set -eu

action="$1"
stack_directory="$2"
compose_executable="$3"
docker_executable="$4"
health_attempts="$5"
health_interval_seconds="$6"

compose() {
    "$compose_executable" \
        -f "$stack_directory/compose.yaml" \
        --env-file "$stack_directory/.env" \
        "$@"
}

status() {
    compose config --quiet
    container_id="$(compose ps -q collector)"
    if [ -z "$container_id" ]; then
        printf 'running=false\nhealth=stopped\nimage=none\n'
        return
    fi
    state="$("$docker_executable" inspect --format '{{.State.Status}}' "$container_id")"
    health="$("$docker_executable" inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id")"
    image="$("$docker_executable" inspect --format '{{.Config.Image}}' "$container_id")"
    if [ "$state" = "running" ]; then
        running=true
    else
        running=false
    fi
    printf 'running=%s\nhealth=%s\nimage=%s\n' "$running" "$health" "$image"
}

wait_healthy() {
    attempt=1
    while [ "$attempt" -le "$health_attempts" ]; do
        container_id="$(compose ps -q collector)"
        if [ -n "$container_id" ]; then
            state="$("$docker_executable" inspect --format '{{.State.Status}}' "$container_id")"
            health="$("$docker_executable" inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id")"
            if [ "$state" = "running" ] && [ "$health" = "healthy" ]; then
                return
            fi
        fi
        sleep "$health_interval_seconds"
        attempt=$((attempt + 1))
    done
    printf 'collector did not become healthy\n' >&2
    exit 1
}

case "$action" in
    doctor)
        compose config --quiet
        "$docker_executable" version >/dev/null
        printf 'doctor=ok\n'
        ;;
    status)
        status
        ;;
    start)
        compose up -d collector
        wait_healthy
        status
        ;;
    stop)
        compose stop -t 30 collector
        status
        ;;
    logs)
        tail_lines="$7"
        compose logs --tail "$tail_lines" collector
        ;;
    update)
        image_tag="$7"
        environment_file="$stack_directory/.env"
        match_count="$(grep -c '^COLLECTOR_IMAGE_TAG=' "$environment_file" || true)"
        if [ "$match_count" -ne 1 ]; then
            printf 'expected exactly one COLLECTOR_IMAGE_TAG entry\n' >&2
            exit 1
        fi
        timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
        backup_file="$environment_file.backup.$timestamp"
        temporary_file="$environment_file.admin.$$"
        trap 'rm -f "$temporary_file"' EXIT HUP INT TERM
        cp "$environment_file" "$backup_file"
        awk -v image_tag="$image_tag" '
            /^COLLECTOR_IMAGE_TAG=/ { print "COLLECTOR_IMAGE_TAG=" image_tag; next }
            { print }
        ' "$environment_file" > "$temporary_file"
        mv "$temporary_file" "$environment_file"
        trap - EXIT HUP INT TERM
        compose pull collector
        compose up -d collector
        wait_healthy
        printf 'backup=%s\n' "$backup_file"
        status
        ;;
    *)
        printf 'unknown NAS administration action: %s\n' "$action" >&2
        exit 1
        ;;
esac
