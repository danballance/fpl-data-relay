ALTER TABLE fpl_event_status_days
    ADD COLUMN points text;

UPDATE fpl_event_status_days
SET points = '';

ALTER TABLE fpl_event_status_days
    ALTER COLUMN points SET NOT NULL,
    ADD CONSTRAINT fpl_event_status_days_points_state CHECK (
        points IN ('', 'l', 'p', 'r')
    ),
    DROP COLUMN leagues_updated;

CREATE TABLE relay_change_feed_rebaselines (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    season_id text NOT NULL REFERENCES fpl_seasons(id),
    reason text NOT NULL,
    change_events_deleted integer NOT NULL,
    entity_changes_deleted integer NOT NULL,
    snapshots_rebuilt integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT relay_change_feed_rebaselines_reason_nonempty CHECK (
        length(btrim(reason)) > 0
    ),
    CONSTRAINT relay_change_feed_rebaselines_counts_nonnegative CHECK (
        change_events_deleted >= 0
        AND entity_changes_deleted >= 0
        AND snapshots_rebuilt >= 0
    )
);

CREATE INDEX relay_change_feed_rebaselines_season_id_idx
    ON relay_change_feed_rebaselines (season_id, id);

CREATE TABLE relay_maintenance_windows (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reason text NOT NULL,
    operator_arn text NOT NULL,
    phase text NOT NULL,
    schedules jsonb NOT NULL,
    collector_was_running boolean,
    queues_before jsonb NOT NULL,
    queues_after jsonb NOT NULL DEFAULT '[]'::jsonb,
    started_at timestamptz NOT NULL DEFAULT now(),
    activated_at timestamptz,
    closed_at timestamptz,
    closed_by text,
    CONSTRAINT relay_maintenance_windows_reason_nonempty CHECK (
        length(btrim(reason)) > 0
    ),
    CONSTRAINT relay_maintenance_windows_operator_nonempty CHECK (
        length(btrim(operator_arn)) > 0
    ),
    CONSTRAINT relay_maintenance_windows_phase CHECK (
        phase IN ('entering', 'active', 'exiting', 'closed')
    ),
    CONSTRAINT relay_maintenance_windows_closed_state CHECK (
        (phase = 'closed' AND closed_at IS NOT NULL AND closed_by IS NOT NULL)
        OR (phase <> 'closed' AND closed_at IS NULL AND closed_by IS NULL)
    )
);

CREATE UNIQUE INDEX relay_maintenance_windows_one_open_idx
    ON relay_maintenance_windows ((true))
    WHERE phase <> 'closed';

CREATE INDEX relay_maintenance_windows_started_at_idx
    ON relay_maintenance_windows (started_at DESC, id DESC);
