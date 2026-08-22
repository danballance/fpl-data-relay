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
