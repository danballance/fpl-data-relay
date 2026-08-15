CREATE TABLE relay_community_extraction_cache (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    strategy_key text NOT NULL,
    strategy_version integer NOT NULL,
    source_key text NOT NULL,
    source_type text NOT NULL,
    document_id text NOT NULL,
    external_id text NOT NULL,
    content_revision text NOT NULL,
    extraction_contract_hash text NOT NULL,
    document jsonb NOT NULL,
    topics jsonb NOT NULL,
    published_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (
        strategy_key,
        strategy_version,
        source_key,
        document_id,
        content_revision,
        extraction_contract_hash
    ),
    CONSTRAINT relay_community_cache_strategy_version_positive CHECK (
        strategy_version > 0
    ),
    CONSTRAINT relay_community_cache_source_type CHECK (
        source_type IN ('x', 'youtube', 'blog')
    ),
    CONSTRAINT relay_community_cache_content_revision_sha256 CHECK (
        content_revision ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT relay_community_cache_contract_sha256 CHECK (
        extraction_contract_hash ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT relay_community_cache_document_object CHECK (
        jsonb_typeof(document) = 'object' AND NOT (document ? 'text')
    ),
    CONSTRAINT relay_community_cache_topics_object CHECK (
        jsonb_typeof(topics) = 'object'
        AND topics ? 'topics'
        AND jsonb_typeof(topics -> 'topics') = 'array'
    ),
    CONSTRAINT relay_community_cache_expiry_ordered CHECK (
        expires_at > published_at
    )
);

CREATE INDEX relay_community_cache_expiry_idx
    ON relay_community_extraction_cache (expires_at);

CREATE INDEX relay_community_cache_lookup_idx
    ON relay_community_extraction_cache (
        strategy_key,
        strategy_version,
        extraction_contract_hash,
        document_id
    );

CREATE FUNCTION relay_reject_community_cache_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'community extraction cache rows are insert-only';
END;
$$;

CREATE TRIGGER relay_community_extraction_cache_insert_only
BEFORE UPDATE ON relay_community_extraction_cache
FOR EACH ROW EXECUTE FUNCTION relay_reject_community_cache_update();
