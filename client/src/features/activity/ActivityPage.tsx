import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { useEffect, useMemo, useRef, useState } from "react";

import { errorMessage } from "../../api/errors";
import { useRelayApi } from "../../api/provider";
import type { RelayApi } from "../../api/relay-api";
import type { ChangeEvent } from "../../api/types";
import { ResourcePage } from "../../components/ResourcePage";
import { compactHash, formatDateTime } from "../../lib/format";

export const CHANGE_PAGE_SIZE = 100;
export const CHANGE_POLL_MILLISECONDS = 15_000;

export async function readChangesAfter(
  api: RelayApi,
  afterId: number,
  signal: AbortSignal,
): Promise<ChangeEvent[]> {
  const events: ChangeEvent[] = [];
  let cursor = afterId;
  while (true) {
    const page = await api.listChangeEvents(
      cursor,
      CHANGE_PAGE_SIZE,
      signal,
    );
    events.push(...page.items);
    if (page.next_after_id === null) return events;
    if (page.next_after_id <= cursor) {
      throw new Error("Change-event history did not advance its cursor.");
    }
    cursor = page.next_after_id;
  }
}

export async function readChangeHistory(
  api: RelayApi,
  signal: AbortSignal,
): Promise<ChangeEvent[]> {
  return readChangesAfter(api, 0, signal);
}

export function mergeChangeEvents(
  history: ChangeEvent[],
  polled: ChangeEvent[],
): ChangeEvent[] {
  return Array.from(
    new Map([...history, ...polled].map((event) => [event.id, event])).values(),
  ).sort((left, right) => left.id - right.id);
}

export function ActivityPage() {
  const api = useRelayApi();
  const [hidden, setHidden] = useState(document.hidden);
  const eventsRef = useRef<ChangeEvent[]>([]);
  const lastSeenId = useRef(0);
  const changesQuery = useQuery({
    queryKey: ["change-events"],
    queryFn: async ({ signal }) => {
      const polled = await readChangesAfter(api, lastSeenId.current, signal);
      const events = mergeChangeEvents(eventsRef.current, polled);
      eventsRef.current = events;
      lastSeenId.current = Math.max(
        lastSeenId.current,
        events.at(-1)?.id ?? 0,
      );
      return events;
    },
    retry: false,
    refetchInterval: hidden ? false : CHANGE_POLL_MILLISECONDS,
    refetchIntervalInBackground: false,
  });
  const events = changesQuery.data ?? [];

  useEffect(() => {
    const visibilityChanged = () => {
      setHidden(document.hidden);
      if (!document.hidden) void changesQuery.refetch();
    };
    document.addEventListener("visibilitychange", visibilityChanged);
    return () =>
      document.removeEventListener("visibilitychange", visibilityChanged);
  }, [changesQuery]);

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
      description="Stored ingestion changes refreshed with cursor polling."
      query={changesQuery}
      records={events}
      columns={columns}
      getRowId={(row) => String(row.id)}
      getRowLabel={(row) => `Change ${row.id} · ${row.event_name}`}
      controls={
        <div className="stream-control">
          <span
            className={`stream-state stream-state--${
              hidden ? "paused" : changesQuery.isError ? "error" : "polling"
            }`}
          >
            <span aria-hidden="true" />
            {hidden ? "Paused" : changesQuery.isError ? "Error" : "Polling"}
          </span>
          {changesQuery.isError ? (
            <span>{errorMessage(changesQuery.error)}</span>
          ) : null}
          {changesQuery.isError ? (
            <button
              className="button"
              type="button"
              onClick={() => void changesQuery.refetch()}
            >
              Retry from {events.at(-1)?.id ?? 0}
            </button>
          ) : null}
        </div>
      }
      rawResponse={{ items: events, next_after_id: null }}
      emptyMessage="No change events are stored yet."
    />
  );
}
