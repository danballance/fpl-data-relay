# API

All REST endpoints serve the latest data already stored in Postgres.

## Resources

- `GET /healthz`
- `GET /v1/bootstrap-static`
- `GET /v1/fixtures`
- `GET /v1/events/current/fixtures`
- `GET /v1/event-status`
- `GET /v1/events/current/live`
- `GET /v1/change-events?after_id=0&limit=100`
- `GET /v1/stream`

Resource endpoints return upstream-shaped JSON and include:

- `ETag`
- `X-FPL-Relay-Fetched-At`
- `X-FPL-Relay-Checked-At`
- `X-FPL-Relay-Resource-Key`

The SSE stream emits stored change-event metadata. It accepts the standard
`Last-Event-ID` header to replay missed events.

