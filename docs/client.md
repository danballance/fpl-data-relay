# React Data Explorer

The read-only React application under `client/` runs locally through Vite and
in production from the private S3/CloudFront origin. It calls only the relay;
it never calls the upstream FPL API.

After the one-time root-level `make setup`, run the client on its own with:

```fish
make client
```

Use `make dev` instead to prepare and start PostgreSQL and the API before
running the client. Both commands load the explicit proxy target from
`client/.env.local`.

Vite proxies `/api` to the configured relay and removes the prefix. Production
uses the same paths through CloudFront.

The Explorer navigation links to Swagger UI at `/api/docs`. Resource pages
deep-link to the documented OpenAPI operation that supplies the visible data;
record inspectors link to their detail operation. Pages with multiple visible
operations expose a compact endpoint menu, and the Fixtures link follows the
selected season or event scope. Documentation opens in a new tab so the
Explorer's current selection and filters remain in place.

Before rendering database-backed routes, the client calls `/api/readyz`. A
`database_waking` response shows a full-page waking state and retries every
five seconds for eight attempts. Manual retry is available after exhaustion.
Database and schema failures use distinct error states.

The Activity page uses cursor polling rather than SSE. It:

- initially requests the newest 100 family summaries;
- loads older pages only when requested;
- catches up from the greatest seen ID, following bounded forward pages;
- polls every 15 seconds while the page is visible;
- pauses when the document is hidden;
- immediately catches up when visibility returns;
- relies on React Query to prevent overlapping requests.

Reference and live freshness cards distinguish initializing, healthy, stale,
idle, and active polling states. Family, operation, and text filters narrow the
newest-first feed. Inspecting a summary loads its affected entities and renders
before/after field values, including explicit absent and null values. Player
prices use millions, while player availability/news, fixture kickoff, and
gameweek changes have recognizable labels. Raw JSON remains available.

The UI exposes `Polling`, `Paused`, and `Error` states independently of source
freshness.

The Community page preserves its strategy and historical-report selectors in
the URL query string. It shows the report window and generation time, story and
source coverage, reused-document cache metrics, partial-collection and short-report warnings, explainable
momentum components, evidence links, and generation-time entity snapshots.
Entity cards deep-link to the existing player, team, event, or fixture Explorer
page. The page always labels its contents as automated summaries of community
discussion rather than verified recommendations, and distinguishes loading,
unknown strategy, no generated report, and fatal API states.

Run the complete frontend gate with:

```fish
make check
```

This performs the Python gate plus strict TypeScript checking, ESLint, Vitest
coverage at or above 90%, and a production Vite build. Use
`uv run npm --prefix client run check` when diagnosing only the frontend.
