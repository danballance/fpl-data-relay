import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { useEffect, useMemo, useRef, useState } from "react";

import { errorMessage } from "../../api/errors";
import { useRelayApi } from "../../api/provider";
import type { ChangeEvent } from "../../api/types";
import type { RelayApi } from "../../api/relay-api";
import { ResourcePage } from "../../components/ResourcePage";
import { compactHash, formatDateTime } from "../../lib/format";

export const CHANGE_PAGE_SIZE = 1_000;

export async function readChangeHistory(
  api: RelayApi,
  signal: AbortSignal,
): Promise<ChangeEvent[]> {
  const events: ChangeEvent[] = [];
  let afterId = 0;

  while (true) {
    const page = await api.listChangeEvents(
      afterId,
      CHANGE_PAGE_SIZE,
      signal,
    );
    events.push(...page.events);
    if (page.events.length < CHANGE_PAGE_SIZE) {
      return events;
    }
    const nextId = page.events.at(-1)?.id;
    if (nextId === undefined || nextId <= afterId) {
      throw new Error("Change-event history did not advance its cursor.");
    }
    afterId = nextId;
  }
}

export function mergeChangeEvents(
  history: ChangeEvent[],
  live: ChangeEvent[],
): ChangeEvent[] {
  return Array.from(
    new Map([...history, ...live].map((event) => [event.id, event])).values(),
  ).sort((left, right) => left.id - right.id);
}

export function ActivityPage() {
  const api = useRelayApi();
  const historyQuery = useQuery({
    queryKey: ["change-events"],
    queryFn: ({ signal }) => readChangeHistory(api, signal),
    retry: false,
  });
  const [liveEvents, setLiveEvents] = useState<ChangeEvent[]>([]);
  const [streamState, setStreamState] = useState<
    "connecting" | "live" | "disconnected"
  >("connecting");
  const [streamError, setStreamError] = useState<string | null>(null);
  const [reconnect, setReconnect] = useState(0);
  const lastSeenId = useRef(0);

  const records = useMemo(
    () => mergeChangeEvents(historyQuery.data ?? [], liveEvents),
    [historyQuery.data, liveEvents],
  );

  useEffect(() => {
    if (historyQuery.data === undefined) {
      return;
    }
    const controller = new AbortController();
    lastSeenId.current = Math.max(
      lastSeenId.current,
      historyQuery.data.at(-1)?.id ?? 0,
    );
    void api
      .watchChangeEvents({
        afterId: lastSeenId.current,
        signal: controller.signal,
        onEvent: (event) => {
          lastSeenId.current = Math.max(lastSeenId.current, event.id);
          setLiveEvents((current) =>
            current.some((item) => item.id === event.id)
              ? current
              : [...current, event],
          );
          setStreamState("live");
        },
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        setStreamState("disconnected");
        setStreamError(errorMessage(error));
      });
    return () => controller.abort();
  }, [api, historyQuery.data, reconnect]);

  const columns = useMemo<ColumnDef<ChangeEvent>[]>(
    () => [
      { accessorKey: "id", header: "ID" },
      {
        accessorKey: "created_at",
        header: "Created",
        cell: ({ getValue }) => formatDateTime(getValue<string>()),
      },
      { accessorKey: "entity_family", header: "Family" },
      { accessorKey: "event_name", header: "Event name" },
      { accessorKey: "season_id", header: "Season" },
      { accessorKey: "event_id", header: "Gameweek" },
      { accessorKey: "source_key", header: "Source" },
      {
        accessorKey: "payload_hash",
        header: "Payload hash",
        cell: ({ getValue }) => (
          <code title={getValue<string>()}>
            {compactHash(getValue<string>())}
          </code>
        ),
      },
    ],
    [],
  );

  return (
    <ResourcePage
      eyebrow="Change feed"
      title="Activity"
      description="Stored ingestion changes followed by the live relay stream."
      query={historyQuery}
      records={records}
      columns={columns}
      getRowId={(row) => String(row.id)}
      getRowLabel={(row) => `Change ${row.id} · ${row.event_name}`}
      controls={
        <div className="stream-control">
          <span className={`stream-state stream-state--${streamState}`}>
            <span aria-hidden="true" />
            {streamState === "connecting"
              ? "Connecting"
              : streamState === "live"
                ? "Live"
                : "Disconnected"}
          </span>
          {streamError === null ? null : <span>{streamError}</span>}
          {streamState === "disconnected" ? (
            <button
              className="button"
              type="button"
              onClick={() => {
                setStreamState("connecting");
                setStreamError(null);
                setReconnect((value) => value + 1);
              }}
            >
              Reconnect from {records.at(-1)?.id ?? 0}
            </button>
          ) : null}
        </div>
      }
      rawResponse={{ events: records }}
      emptyMessage="No change events are stored yet."
    />
  );
}
