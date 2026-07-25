"""Database schema constants for the normalised Postgres FPL store."""

from fpl_data_relay.application.database import SCHEMA_VERSION

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
DROP TABLE IF EXISTS relay_change_events;
DROP TABLE IF EXISTS relay_ingestion_sources;
DROP TABLE IF EXISTS fpl_event_live_explain_stats;
DROP TABLE IF EXISTS fpl_event_live_elements;
DROP TABLE IF EXISTS fpl_event_status_days;
DROP TABLE IF EXISTS fpl_event_status;
DROP TABLE IF EXISTS fpl_fixture_stat_entries;
DROP TABLE IF EXISTS fpl_fixtures;
DROP TABLE IF EXISTS fpl_elements;
DROP TABLE IF EXISTS fpl_element_stat_definitions;
DROP TABLE IF EXISTS fpl_element_types;
DROP TABLE IF EXISTS fpl_teams;
DROP TABLE IF EXISTS fpl_phases;
DROP TABLE IF EXISTS fpl_events;
DROP TABLE IF EXISTS fpl_seasons;

CREATE TABLE IF NOT EXISTS fpl_seasons (
    id text PRIMARY KEY,
    start_year integer NOT NULL,
    end_year integer NOT NULL,
    first_deadline_time timestamptz NOT NULL,
    last_deadline_time timestamptz NOT NULL,
    is_current boolean NOT NULL,
    row_hash text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fpl_seasons_years_match_id CHECK (
        id = start_year::text || '-' || right(end_year::text, 2)
    ),
    CONSTRAINT fpl_seasons_end_year_follows_start CHECK (
        end_year = start_year + 1
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS fpl_seasons_single_current_idx
    ON fpl_seasons (is_current)
    WHERE is_current = true;

CREATE TABLE IF NOT EXISTS fpl_events (
    season_id text NOT NULL REFERENCES fpl_seasons(id),
    id integer NOT NULL,
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
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (season_id, id)
);

CREATE TABLE IF NOT EXISTS fpl_phases (
    season_id text NOT NULL REFERENCES fpl_seasons(id),
    id integer NOT NULL,
    name text NOT NULL,
    start_event integer NOT NULL,
    stop_event integer NOT NULL,
    row_hash text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (season_id, id),
    FOREIGN KEY (season_id, start_event) REFERENCES fpl_events(season_id, id),
    FOREIGN KEY (season_id, stop_event) REFERENCES fpl_events(season_id, id)
);

CREATE TABLE IF NOT EXISTS fpl_teams (
    season_id text NOT NULL REFERENCES fpl_seasons(id),
    id integer NOT NULL,
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
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (season_id, id)
);

CREATE TABLE IF NOT EXISTS fpl_element_types (
    season_id text NOT NULL REFERENCES fpl_seasons(id),
    id integer NOT NULL,
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
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (season_id, id)
);

CREATE TABLE IF NOT EXISTS fpl_element_stat_definitions (
    season_id text NOT NULL REFERENCES fpl_seasons(id),
    name text NOT NULL,
    label text NOT NULL,
    row_hash text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (season_id, name)
);

CREATE TABLE IF NOT EXISTS fpl_elements (
    season_id text NOT NULL REFERENCES fpl_seasons(id),
    id integer NOT NULL,
    code integer,
    first_name text NOT NULL,
    second_name text NOT NULL,
    web_name text NOT NULL,
    photo text,
    team integer NOT NULL,
    team_code integer,
    element_type integer NOT NULL,
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
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (season_id, id),
    FOREIGN KEY (season_id, team) REFERENCES fpl_teams(season_id, id),
    FOREIGN KEY (season_id, element_type)
        REFERENCES fpl_element_types(season_id, id)
);

CREATE TABLE IF NOT EXISTS fpl_fixtures (
    season_id text NOT NULL REFERENCES fpl_seasons(id),
    id integer NOT NULL,
    code integer,
    event integer,
    finished boolean NOT NULL,
    finished_provisional boolean,
    kickoff_time timestamptz,
    minutes integer,
    provisional_start_time boolean,
    started boolean NOT NULL,
    team_a integer NOT NULL,
    team_a_score integer,
    team_h integer NOT NULL,
    team_h_score integer,
    pulse_id integer,
    row_hash text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (season_id, id),
    FOREIGN KEY (season_id, event) REFERENCES fpl_events(season_id, id),
    FOREIGN KEY (season_id, team_a) REFERENCES fpl_teams(season_id, id),
    FOREIGN KEY (season_id, team_h) REFERENCES fpl_teams(season_id, id)
);

CREATE TABLE IF NOT EXISTS fpl_fixture_stat_entries (
    season_id text NOT NULL,
    fixture_id integer NOT NULL,
    identifier text NOT NULL,
    side text NOT NULL CHECK (side IN ('a', 'h')),
    ordinal integer NOT NULL,
    element integer,
    value_text text,
    value_type text NOT NULL,
    PRIMARY KEY (season_id, fixture_id, identifier, side, ordinal),
    FOREIGN KEY (season_id, fixture_id)
        REFERENCES fpl_fixtures(season_id, id)
        ON DELETE CASCADE,
    FOREIGN KEY (season_id, element) REFERENCES fpl_elements(season_id, id)
);

CREATE TABLE IF NOT EXISTS fpl_event_status (
    season_id text PRIMARY KEY REFERENCES fpl_seasons(id),
    leagues text,
    payload_hash text NOT NULL,
    fetched_at timestamptz NOT NULL,
    checked_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fpl_event_status_days (
    season_id text NOT NULL,
    event integer NOT NULL,
    date date NOT NULL,
    bonus_added boolean NOT NULL,
    leagues_updated boolean,
    row_hash text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (season_id, event, date),
    FOREIGN KEY (season_id, event) REFERENCES fpl_events(season_id, id)
);

CREATE TABLE IF NOT EXISTS fpl_event_live_elements (
    season_id text NOT NULL,
    event_id integer NOT NULL,
    element_id integer NOT NULL,
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
    PRIMARY KEY (season_id, event_id, element_id),
    FOREIGN KEY (season_id, event_id) REFERENCES fpl_events(season_id, id),
    FOREIGN KEY (season_id, element_id) REFERENCES fpl_elements(season_id, id)
);

CREATE TABLE IF NOT EXISTS fpl_event_live_explain_stats (
    season_id text NOT NULL,
    event_id integer NOT NULL,
    element_id integer NOT NULL,
    fixture_id integer NOT NULL,
    identifier text NOT NULL,
    ordinal integer NOT NULL,
    points integer NOT NULL,
    value_text text,
    value_type text NOT NULL,
    PRIMARY KEY (
        season_id, event_id, element_id, fixture_id, identifier, ordinal
    ),
    FOREIGN KEY (season_id, event_id, element_id)
        REFERENCES fpl_event_live_elements(season_id, event_id, element_id)
        ON DELETE CASCADE,
    FOREIGN KEY (season_id, fixture_id) REFERENCES fpl_fixtures(season_id, id)
);

CREATE TABLE IF NOT EXISTS relay_ingestion_sources (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    season_id text NOT NULL REFERENCES fpl_seasons(id),
    source_key text NOT NULL,
    event_id integer,
    payload_hash text NOT NULL,
    fetched_at timestamptz NOT NULL,
    checked_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT relay_ingestion_sources_event_id_positive CHECK (
        event_id IS NULL OR event_id > 0
    ),
    CONSTRAINT relay_ingestion_sources_payload_hash_sha256 CHECK (
        length(payload_hash) = 64
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS relay_ingestion_sources_identity_idx
    ON relay_ingestion_sources (season_id, source_key, COALESCE(event_id, 0));

CREATE TABLE IF NOT EXISTS relay_change_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    season_id text REFERENCES fpl_seasons(id),
    entity_family text NOT NULL DEFAULT 'events',
    event_name text NOT NULL,
    source_key text,
    resource_key text,
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
