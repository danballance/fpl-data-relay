CREATE TABLE relay_community_reports (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    strategy_key text NOT NULL,
    strategy_version integer NOT NULL,
    report_date date NOT NULL,
    season_id text NOT NULL REFERENCES fpl_seasons(id),
    as_of_event_id integer,
    window_start timestamptz NOT NULL,
    window_end timestamptz NOT NULL,
    generated_at timestamptz NOT NULL,
    content jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (strategy_key, report_date),
    FOREIGN KEY (season_id, as_of_event_id)
        REFERENCES fpl_events(season_id, id),
    CONSTRAINT relay_community_reports_strategy_version_positive CHECK (
        strategy_version > 0
    ),
    CONSTRAINT relay_community_reports_window_ordered CHECK (
        window_end > window_start
    ),
    CONSTRAINT relay_community_reports_content_object CHECK (
        jsonb_typeof(content) = 'object'
    ),
    CONSTRAINT relay_community_reports_stories_array CHECK (
        content ? 'stories'
        AND jsonb_typeof(content -> 'stories') = 'array'
    ),
    CONSTRAINT relay_community_reports_story_count CHECK (
        jsonb_array_length(content -> 'stories') BETWEEN 1 AND 10
    )
);

CREATE INDEX relay_community_reports_strategy_id_idx
    ON relay_community_reports (strategy_key, id DESC);

CREATE FUNCTION relay_reject_community_report_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'community reports are immutable';
END;
$$;

CREATE TRIGGER relay_community_reports_immutable
BEFORE UPDATE OR DELETE ON relay_community_reports
FOR EACH ROW EXECUTE FUNCTION relay_reject_community_report_mutation();
