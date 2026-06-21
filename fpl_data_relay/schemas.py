"""Database schema constants for the normalised Postgres FPL store."""

SCHEMA_VERSION = 2
NOTIFY_CHANNEL = "relay_change_events"
ADVISORY_LOCK_ID = 9_722_024_001

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS relay_schema_version (
    id boolean PRIMARY KEY DEFAULT true,
    version integer NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT relay_schema_version_single_row CHECK (id)
);

DROP TABLE IF EXISTS relay_resources;

CREATE TABLE IF NOT EXISTS fpl_events (
    id integer PRIMARY KEY,
    name text NOT NULL,
    deadline_time timestamptz,
    average_entry_score integer,
    finished boolean,
    data_checked boolean,
    highest_scoring_entry integer,
    deadline_time_epoch integer,
    deadline_time_game_offset integer,
    highest_score integer,
    is_previous boolean NOT NULL,
    is_current boolean NOT NULL,
    is_next boolean NOT NULL,
    row_hash text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fpl_phases (
    id integer PRIMARY KEY,
    name text NOT NULL,
    start_event integer NOT NULL REFERENCES fpl_events(id),
    stop_event integer NOT NULL REFERENCES fpl_events(id),
    row_hash text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fpl_teams (
    id integer PRIMARY KEY,
    name text NOT NULL,
    short_name text NOT NULL,
    code integer,
    strength integer,
    strength_overall_home integer,
    strength_overall_away integer,
    strength_attack_home integer,
    strength_attack_away integer,
    strength_defence_home integer,
    strength_defence_away integer,
    pulse_id integer,
    row_hash text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fpl_element_types (
    id integer PRIMARY KEY,
    singular_name text NOT NULL,
    singular_name_short text,
    plural_name text,
    plural_name_short text,
    squad_select integer,
    squad_min_play integer,
    squad_max_play integer,
    ui_shirt_specific boolean,
    sub_positions_locked integer[] NOT NULL DEFAULT '{{}}',
    element_count integer,
    row_hash text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fpl_element_stat_definitions (
    name text PRIMARY KEY,
    label text NOT NULL,
    row_hash text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fpl_elements (
    id integer PRIMARY KEY,
    code integer,
    first_name text NOT NULL,
    second_name text NOT NULL,
    web_name text NOT NULL,
    team integer NOT NULL REFERENCES fpl_teams(id),
    team_code integer,
    element_type integer NOT NULL REFERENCES fpl_element_types(id),
    status text,
    news text,
    news_added timestamptz,
    now_cost integer,
    selected_by_percent text,
    total_points integer,
    chance_of_playing_next_round integer,
    chance_of_playing_this_round integer,
    form text,
    minutes integer,
    goals_scored integer,
    assists integer,
    clean_sheets integer,
    goals_conceded integer,
    own_goals integer,
    penalties_saved integer,
    penalties_missed integer,
    yellow_cards integer,
    red_cards integer,
    saves integer,
    bonus integer,
    bps integer,
    influence text,
    creativity text,
    threat text,
    ict_index text,
    expected_goals text,
    expected_assists text,
    expected_goal_involvements text,
    expected_goals_conceded text,
    row_hash text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fpl_fixtures (
    id integer PRIMARY KEY,
    code integer,
    event integer REFERENCES fpl_events(id),
    finished boolean NOT NULL,
    finished_provisional boolean,
    kickoff_time timestamptz,
    minutes integer,
    provisional_start_time boolean,
    started boolean NOT NULL,
    team_a integer NOT NULL REFERENCES fpl_teams(id),
    team_a_score integer,
    team_h integer NOT NULL REFERENCES fpl_teams(id),
    team_h_score integer,
    pulse_id integer,
    row_hash text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fpl_fixture_stat_entries (
    fixture_id integer NOT NULL REFERENCES fpl_fixtures(id) ON DELETE CASCADE,
    identifier text NOT NULL,
    side text NOT NULL CHECK (side IN ('a', 'h')),
    ordinal integer NOT NULL,
    element integer REFERENCES fpl_elements(id),
    value_text text,
    value_type text NOT NULL,
    PRIMARY KEY (fixture_id, identifier, side, ordinal)
);

CREATE TABLE IF NOT EXISTS fpl_event_status (
    id boolean PRIMARY KEY DEFAULT true,
    leagues text,
    payload_hash text NOT NULL,
    fetched_at timestamptz NOT NULL,
    checked_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fpl_event_status_single_row CHECK (id)
);

CREATE TABLE IF NOT EXISTS fpl_event_status_days (
    event integer NOT NULL REFERENCES fpl_events(id),
    date date NOT NULL,
    bonus_added boolean NOT NULL,
    leagues_updated boolean,
    row_hash text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (event, date)
);

CREATE TABLE IF NOT EXISTS fpl_event_live_elements (
    event_id integer NOT NULL REFERENCES fpl_events(id),
    element_id integer NOT NULL REFERENCES fpl_elements(id),
    minutes integer,
    goals_scored integer,
    assists integer,
    clean_sheets integer,
    goals_conceded integer,
    own_goals integer,
    penalties_saved integer,
    penalties_missed integer,
    yellow_cards integer,
    red_cards integer,
    saves integer,
    bonus integer,
    bps integer,
    influence text,
    creativity text,
    threat text,
    ict_index text,
    starts integer,
    expected_goals text,
    expected_assists text,
    expected_goal_involvements text,
    expected_goals_conceded text,
    defensive_contribution integer,
    total_points integer,
    in_dreamteam boolean,
    row_hash text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, element_id)
);

CREATE TABLE IF NOT EXISTS fpl_event_live_explain_stats (
    event_id integer NOT NULL,
    element_id integer NOT NULL,
    fixture_id integer NOT NULL REFERENCES fpl_fixtures(id),
    identifier text NOT NULL,
    ordinal integer NOT NULL,
    points integer NOT NULL,
    value_text text,
    value_type text NOT NULL,
    PRIMARY KEY (event_id, element_id, fixture_id, identifier, ordinal),
    FOREIGN KEY (event_id, element_id)
        REFERENCES fpl_event_live_elements(event_id, element_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS relay_ingestion_sources (
    source_key text PRIMARY KEY,
    event_id integer,
    payload_hash text NOT NULL,
    fetched_at timestamptz NOT NULL,
    checked_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT relay_ingestion_sources_payload_hash_sha256 CHECK (
        length(payload_hash) = 64
    )
);

CREATE TABLE IF NOT EXISTS relay_change_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_family text NOT NULL,
    event_name text NOT NULL,
    source_key text,
    event_id integer,
    payload_hash text NOT NULL,
    fetched_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT relay_change_events_payload_hash_sha256 CHECK (
        length(payload_hash) = 64
    )
);

CREATE INDEX IF NOT EXISTS relay_change_events_family_id_idx
    ON relay_change_events (entity_family, id);

INSERT INTO relay_schema_version (id, version)
VALUES (true, {SCHEMA_VERSION})
ON CONFLICT (id)
DO UPDATE SET version = EXCLUDED.version, applied_at = now();
"""
