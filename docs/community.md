# Scheduled community intelligence

Community intelligence is a strategy-driven, read-only reporting pipeline. A
daily EventBridge Scheduler delivery fans out one SQS job for every active
packaged strategy. The generic community Lambda collects the configured public
sources, analyzes the preceding seven days, links the discussion to canonical
FPL records, ranks the valid stories, and inserts one immutable PostgreSQL row.

The first packaged strategy is `weekly-community-momentum-v1`. It is deliberately
`active = false` and the source list is empty. Production must remain disabled
until a reviewed source catalog and the provider credentials described below are
in place.

## Runtime and data flow

1. EventBridge Scheduler sends a strict `community_dispatch` message at 06:00
   `Europe/London`. Its timestamp comes from
   `<aws.scheduler.scheduled-time>`.
2. The dispatcher creates one versioned `community_strategy` message per active
   strategy. Its `report_date` is the London local date and its half-open window
   is exactly `[scheduled_at - 7 days, scheduled_at)`.
3. The worker checks `(strategy_key, report_date)` before any provider or cache
   work, prunes expired extraction rows, loads the current season and gameweek,
   and validates the secret.
4. Lightweight discovery runs concurrently for every configured source and
   returns document identity, fresh evidence and engagement metadata, and a
   deterministic revision. Documents are deduplicated by provider-native ID and
   canonical URL.
5. Exact cache hits reuse strict per-document topic output. Only misses are
   materialized: X text already returned by the timeline is used transiently,
   while YouTube transcripts and blog article bodies are fetched only when
   needed. Oversized content is split without dropping characters. Asynchronous
   OpenAI Responses calls extract one result per miss, including valid empty
   topic batches.
6. A fresh synthesis call consumes the complete cached-plus-new seven-day topic
   corpus and writes semantically clustered candidates. Source bodies are
   explicitly untrusted, the model receives opaque document IDs and no writable
   tools, and responses use Pydantic Structured Outputs with `store=false`.
7. Every cited document and entity ID is checked against the in-memory corpus
   and current database. Only high-confidence entity links survive. Stories
   without a canonical entity are dropped.
8. The deterministic ranking policy selects one to ten stories and application
   code adds typed player, team, event, and fixture snapshots. The repository
   inserts one JSONB aggregate row; it never stores full posts, articles, or
   transcripts.

Reserved Lambda concurrency is one and the SQS event batch size is one. Database
uniqueness remains the final idempotency boundary, so a duplicate delivery
returns the existing report without repeating external work.

## Strategy and source manifest

Strategies live in
`src/fpl_data_relay/community_strategies.toml`. Every operational value is
explicit and strict; unknown fields fail validation. A new strategy version is a
new packaged definition and may select another `CommunityRankingPolicy` when a
policy is implemented and registered. Prompt revisions are explicit
`extraction_prompt_version` and `synthesis_prompt_version` values; changing prompt
behavior requires a corresponding version update.

`extraction_cache_retention_days` is also explicit. It is eight for the first
strategy: one day longer than its seven-day lookback, providing an operational
buffer without retaining derivatives indefinitely.

Populate the first strategy only with identifiers reviewed for production use.
This example shows all v1 source fields:

```toml
[[strategies.sources]]
type = "x"
key = "reviewed-x-source"
label = "Public FPL account"
user_id = "123456789"
username = "public_handle"
include_replies = false
include_reposts = false
max_documents = 100
timeout_seconds = 15.0

[[strategies.sources]]
type = "youtube"
key = "reviewed-youtube-source"
label = "Public FPL channel"
channel_id = "UC_REVIEWED_CHANNEL_ID"
max_videos = 10
timeout_seconds = 20.0
transcript_language = "en"
transcript_mode = "native"
transcript_poll_seconds = 2.0
transcript_timeout_seconds = 60.0

[[strategies.sources]]
type = "blog"
key = "reviewed-blog-source"
label = "Public FPL blog"
feed_url = "https://example.test/feed.xml"
allowed_article_hosts = ["example.test", "www.example.test"]
max_articles = 20
timeout_seconds = 15.0
max_response_bytes = 2000000
```

X sources use immutable user IDs and the app-only user-post timeline with the
explicit UTC window, engagement fields, pagination, and manifest-controlled
reply/repost exclusions. YouTube uses the official Data API for discovery and
statistics, then Supadata with English `mode=native`. A video with no native
captions is intentionally excluded. There is no generated-transcription or
YouTube caption-download fallback. Blogs must have RSS/Atom discovery; every
article URL and redirect is checked against the exact host allow-list before
main-text extraction.

Provider contracts: [X user-post timeline](https://docs.x.com/x-api/posts/timelines/integrate),
[YouTube search](https://developers.google.com/youtube/v3/docs/search/list),
[YouTube caption authorization](https://developers.google.com/youtube/v3/docs/captions/download),
and [Supadata native transcripts](https://docs.supadata.ai/api-reference/endpoint/transcript/transcript).

Run offline validation after every manifest edit:

```fish
uv run fpl-relay community validate-config
```

The strategy `model` and `reasoning_effort` are explicit OpenAI provider options
rather than hard-coded allow-lists. Either may be changed in the manifest without
a code release, provided it is a non-empty value containing no whitespace.
Configuration validation does not contact OpenAI or confirm that the selected
model supports the selected effort; an invalid or inaccessible combination fails
the run on its first model request.

## Persistent extraction cache

Migration 4 creates `relay_community_extraction_cache`. Rows are scoped by
strategy key and version and uniquely identify the source, document, content
revision, and extraction contract. The extraction-contract hash is SHA-256 over
canonical JSON containing the exact extraction prompt, model, reasoning effort,
and chunk size. A prompt or model configuration change therefore cannot silently
reuse an incompatible result. Changing the model or reasoning effort causes
extraction cache misses for the current seven-day corpus; daily synthesis always
uses the newly configured provider options.

Revision hashes are metadata-driven:

- X hashes the current post text;
- YouTube hashes video ID, title, transcript language, and native mode;
- blogs hash entry ID, canonical URL, title, publication time, and feed
  `updated` time when present.

Discovery still runs every day so seven-day membership, current engagement, and
source failures remain accurate. On a hit, YouTube skips Supadata and blogs skip
article download and extraction; X avoids only the OpenAI extraction because its
timeline already includes text. Missing native captions and other intentional
materialization exclusions are not cached and are retried on the next run.

Cache rows contain normalized evidence metadata and strict topic output,
including empty topic batches. They never contain source body text. Rows are
insert-only during their lifetime but may be deleted after expiry; the worker
prunes expired rows before external calls. Reports record eligible-document,
hit, miss, write, and expiry-prune counts. Model usage includes only extraction
calls actually made in the current run plus the required daily synthesis call.
Immutable historical reports and their evidence are independent from cache
expiry.

## Credentials

The production application stack requires a pre-existing Secrets Manager ARN.
The secret JSON must contain exactly these four non-empty keys and no others:

```json
{
  "openai_api_key": "...",
  "x_bearer_token": "...",
  "youtube_api_key": "...",
  "supadata_api_key": "..."
}
```

Local manual execution requires the corresponding explicit environment
variables `OPENAI_API_KEY`, `X_BEARER_TOKEN`, `YOUTUBE_API_KEY`, and
`SUPADATA_API_KEY`, in addition to the existing database variables. Run an
idempotent report with an offset-aware timestamp:

```fish
uv run fpl-relay community run \
  --strategy-key weekly-community-momentum-v1 \
  --scheduled-at 2026-08-13T06:00:00+01:00
```

The analyzer follows the official [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
and [current model guidance](https://developers.openai.com/api/docs/guides/latest-model.md).

## Ranking

`community_momentum_v1` calculates a deterministic 0–100 score after model
analysis:

- 35 points for distinct configured sources, capped at five;
- 20 points for distinct supporting documents, capped at ten;
- 20 points for average within-platform engagement percentile;
- 15 points for average linear recency inside the seven-day window;
- 10 points for actionability (`low=0.25`, `medium=0.60`, `high=1.00`).

X engagement is `likes + replies + 2 × (reposts + quotes)`. YouTube engagement
is `views + 10 × likes + 20 × comments`. Percentiles compare only documents on
the same platform in the current run; blogs contribute no engagement value.
Ties resolve by source breadth, evidence count, newest evidence, and normalized
headline. Stored component values explain every final score.

## Report storage and API

Migration 3 creates `relay_community_reports`. Each row records the strategy and
version, London report date, season and optional current event, UTC window and
generation timestamps, and one validated JSONB content object. The JSON contains
strategy metadata, coverage, extraction-cache metrics, model identifiers and
token usage, evidence metadata, ranked stories, and canonical snapshots. The
database enforces an object-shaped JSON value and one-to-ten stories, and
uniquely constrains `(strategy_key, report_date)`. Reports are insert-only and
retained indefinitely.

Public endpoints are documented in `docs/api.md`. Report history returns bounded
summaries; complete content is returned only by latest and by-ID reads. Community
reports are not part of the FPL ingestion change feed.

## Failure policy and recovery

Fetch, parse, extraction, response-size, disallowed-host, and unavailable-content
failures are stable source-level outcomes. The run continues when other sources
succeed, and published coverage names failed sources and counts exclusions.
Unavailable native captions count as exclusions rather than failures.

Invalid manifest configuration, missing or malformed credentials,
authentication failures, rate limiting, database errors, corrupt cache data,
OpenAI failures, refusals or incomplete output, schema violations, invented
citations, invalid entity IDs, and internal business-invariant failures abort
the SQS delivery. A zero-story result is not published. SQS retries and then
moves a repeatedly failing job to the dedicated DLQ. Recovery is to fix the
systemic cause and redrive the exact job; idempotency makes this safe.

Structured logs retain report ID, strategy/date, story and failed-source counts,
cache eligibility/hit/miss/write/prune counts, and aggregate input/output tokens.
They do not include source bodies or secret values. Alarms cover worker errors,
queue age, worker DLQ depth, Scheduler delivery errors and drops, and the absence
of a daily dispatch attempt for 26 hours.

## Production review and rollout

Provider terms, permitted retention, attribution, and privacy must be reviewed
before activation, especially X content and sending third-party YouTube material
to Supadata and OpenAI. The source catalog must contain only deliberately reviewed
public publishers. Evidence URLs remain visible to users; report prose must be a
paraphrase of community discussion, not copied source text or verified advice.

Roll out in this order:

1. Deploy migrations 3 and 4 and the application resources with
   `CommunityScheduleState=DISABLED`.
2. Create the exact four-key credential secret and supply its ARN to the stack.
3. Add the reviewed catalog, leave the schedule disabled, and validate it
   offline.
4. Mark the reviewed strategy active and perform one explicit manual production
   run.
5. Review evidence, entity links, story quality, coverage, and token usage.
6. Change the stack parameter to `ENABLED` only after approval.
7. Verify the next scheduled report, API and Explorer rendering, logs, alarms,
   duplicate delivery behavior, and history pagination.

Never enable the schedule merely because the infrastructure deployed
successfully.
