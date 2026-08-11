DROP TABLE relay_change_events;

TRUNCATE TABLE relay_ingestion_sources;

ALTER TABLE relay_ingestion_sources
    ADD COLUMN last_changed_at timestamptz;

CREATE TABLE relay_entity_snapshots (
    season_id text NOT NULL REFERENCES fpl_seasons(id),
    entity_family text NOT NULL,
    source_event_id integer,
    entity_key text NOT NULL,
    entity_label text NOT NULL,
    snapshot jsonb NOT NULL,
    row_hash text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (season_id, entity_family, entity_key),
    CONSTRAINT relay_entity_snapshots_row_hash_sha256 CHECK (
        length(row_hash) = 64
    )
);

CREATE INDEX relay_entity_snapshots_scope_idx
    ON relay_entity_snapshots (season_id, entity_family, source_event_id);

CREATE TABLE relay_change_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    season_id text NOT NULL REFERENCES fpl_seasons(id),
    entity_family text NOT NULL,
    event_name text NOT NULL,
    source_key text NOT NULL,
    source_event_id integer,
    payload_hash text NOT NULL,
    created_count integer NOT NULL,
    updated_count integer NOT NULL,
    deleted_count integer NOT NULL,
    fetched_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT relay_change_events_payload_hash_sha256 CHECK (
        length(payload_hash) = 64
    ),
    CONSTRAINT relay_change_events_counts_nonnegative CHECK (
        created_count >= 0 AND updated_count >= 0 AND deleted_count >= 0
    ),
    CONSTRAINT relay_change_events_nonempty CHECK (
        created_count + updated_count + deleted_count > 0
    )
);

CREATE INDEX relay_change_events_family_id_idx
    ON relay_change_events (entity_family, id);

CREATE TABLE relay_entity_changes (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    change_event_id bigint NOT NULL REFERENCES relay_change_events(id)
        ON DELETE CASCADE,
    entity_key text NOT NULL,
    entity_label text NOT NULL,
    change_kind text NOT NULL CHECK (
        change_kind IN ('created', 'updated', 'deleted')
    ),
    field_changes jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT relay_entity_changes_fields_array CHECK (
        jsonb_typeof(field_changes) = 'array'
    )
);

CREATE INDEX relay_entity_changes_event_id_idx
    ON relay_entity_changes (change_event_id, id);
