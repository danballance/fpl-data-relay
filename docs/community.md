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
3. The worker checks `(strategy_key, report_date)` before any provider work,
   loads the current season and gameweek, validates the secret, then collects
   all configured sources concurrently.
4. Documents are normalized and deduplicated by provider-native ID and canonical
   URL. Oversized content is split without dropping characters. X posts are
   grouped by account; videos and articles are analyzed independently.
5. Asynchronous OpenAI Responses calls first extract topic mentions and then
   synthesize semantically clustered candidates. Source bodies are explicitly
   untrusted, the model receives opaque document IDs and no writable tools, and
   responses use Pydantic Structured Outputs with `store=false`.
6. Every cited document and entity ID is checked against the in-memory corpus
   and current database. Only high-confidence entity links survive. Stories
   without a canonical entity are dropped.
7. The deterministic ranking policy selects one to ten stories and application
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
strategy metadata, coverage, model identifiers and token usage, evidence
metadata, ranked stories, and canonical snapshots. The database enforces an
object-shaped JSON value and one-to-ten stories, and uniquely constrains
`(strategy_key, report_date)`. Reports are insert-only and retained indefinitely.

Public endpoints are documented in `docs/api.md`. Report history returns bounded
summaries; complete content is returned only by latest and by-ID reads. Community
reports are not part of the FPL ingestion change feed.

## Failure policy and recovery

Fetch, parse, extraction, response-size, disallowed-host, and unavailable-content
failures are stable source-level outcomes. The run continues when other sources
succeed, and published coverage names failed sources and counts exclusions.
Unavailable native captions count as exclusions rather than failures.

Invalid manifest configuration, missing or malformed credentials,
authentication failures, rate limiting, database errors, OpenAI failures,
refusals or incomplete output, schema violations, invented citations, invalid
entity IDs, and internal business-invariant failures abort the SQS delivery. A
zero-story result is not published. SQS retries and then moves a repeatedly
failing job to the dedicated DLQ. Recovery is to fix the systemic cause and
redrive the exact job; idempotency makes this safe.

Structured logs retain report ID, strategy/date, story and failed-source counts,
and aggregate input/output tokens. They do not include source bodies or secret
values. Alarms cover worker errors, queue age, worker DLQ depth, Scheduler
delivery errors and drops, and the absence of a daily dispatch attempt for 26
hours.

## Production review and rollout

Provider terms, permitted retention, attribution, and privacy must be reviewed
before activation, especially X content and sending third-party YouTube material
to Supadata and OpenAI. The source catalog must contain only deliberately reviewed
public publishers. Evidence URLs remain visible to users; report prose must be a
paraphrase of community discussion, not copied source text or verified advice.

Roll out in this order:

1. Deploy migration 3 and the application resources with
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
