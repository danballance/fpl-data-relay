import { RelayApiError, errorMessage } from "../api/errors";

export function StatusPanel({
  state,
  error,
  onRetry,
}: {
  state: "loading" | "error";
  error?: unknown;
  onRetry?: () => void;
}) {
  if (state === "loading") {
    return (
      <div className="status-panel" role="status">
        <span className="spinner" aria-hidden="true" />
        Loading relay data…
      </div>
    );
  }

  const notIngested = error instanceof RelayApiError && error.status === 503;
  const unavailable = error instanceof RelayApiError && error.status === 0;
  return (
    <div className="status-panel status-panel--error" role="alert">
      <div>
        <strong>
          {notIngested
            ? "Data not ingested"
            : unavailable
              ? "Relay unavailable"
              : "Request failed"}
        </strong>
        <p>{errorMessage(error)}</p>
      </div>
      {onRetry === undefined ? null : (
        <button className="button" type="button" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}
