SCHEMA_VERSION = 1
NOTIFY_CHANNEL = "relay_change_events"
ADVISORY_LOCK_ID = 9_722_024_001

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS relay_schema_version (
    id boolean PRIMARY KEY DEFAULT true,
    version integer NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT relay_schema_version_single_row CHECK (id)
);

CREATE TABLE IF NOT EXISTS relay_resources (
    resource_key text PRIMARY KEY,
    event_id integer,
    payload jsonb NOT NULL,
    payload_hash text NOT NULL,
    fetched_at timestamptz NOT NULL,
    checked_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT relay_resources_payload_hash_sha256 CHECK (
        length(payload_hash) = 64
    )
);

CREATE TABLE IF NOT EXISTS relay_change_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    resource_key text NOT NULL,
    event_name text NOT NULL,
    event_id integer,
    payload_hash text NOT NULL,
    fetched_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT relay_change_events_payload_hash_sha256 CHECK (
        length(payload_hash) = 64
    )
);

CREATE INDEX IF NOT EXISTS relay_change_events_resource_key_id_idx
    ON relay_change_events (resource_key, id);

INSERT INTO relay_schema_version (id, version)
VALUES (true, {SCHEMA_VERSION})
ON CONFLICT (id)
DO UPDATE SET version = EXCLUDED.version, applied_at = now();
"""

