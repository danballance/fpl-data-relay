# React Data Explorer

The read-only React application under `client/` runs locally through Vite and
in production from the private S3/CloudFront origin. It calls only the relay;
it never calls the upstream FPL API.

Install and run locally:

```fish
uv run npm --prefix client ci
set -x RELAY_API_PROXY_TARGET http://127.0.0.1:8000
uv run npm --prefix client run dev
```

Vite proxies `/api` to the configured relay and removes the prefix. Production
uses the same paths through CloudFront.

Before rendering database-backed routes, the client calls `/api/readyz`. A
`database_waking` response shows a full-page waking state and retries every
five seconds for eight attempts. Manual retry is available after exhaustion.
Database and schema failures use distinct error states.

The Activity page uses cursor polling rather than SSE. It:

- requests pages of 100 until caught up;
- polls every 15 seconds while the page is visible;
- pauses when the document is hidden;
- immediately catches up when visibility returns;
- carries forward the latest known ID; and
- relies on React Query to prevent overlapping requests.

The UI exposes `Polling`, `Paused`, and `Error` states.

Run the complete frontend gate with:

```fish
uv run npm --prefix client run check
```

This performs strict TypeScript checking, ESLint, Vitest coverage at or above
90%, and a production Vite build.
