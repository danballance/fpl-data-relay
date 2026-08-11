import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { errorMessage } from "../../api/errors";
import { useRelayApi } from "../../api/provider";
import type { RelayApi } from "../../api/relay-api";
import type {
  ChangeEvent,
  EntityChange,
  IngestionStatus,
} from "../../api/types";
import { API_OPERATIONS } from "../../components/ApiDocsLink";
import { ResourcePage } from "../../components/ResourcePage";
import { StructuredValue } from "../../components/StructuredValue";
import { formatDateTime } from "../../lib/format";

export const CHANGE_PAGE_SIZE = 100;
export const CHANGE_POLL_MILLISECONDS = 15_000;
const EMPTY_CHANGE_EVENTS: ChangeEvent[] = [];

type OperationFilter = "all" | "created" | "updated" | "deleted";
type ChangeValue = EntityChange["fields"][number]["before"];
type PipelineStatus = IngestionStatus["reference"];
type DetailedChangeEvent = ChangeEvent & {
  entity_changes: EntityChange[];
};

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
      throw new Error("Change-event polling did not advance its cursor.");
    }
    cursor = page.next_after_id;
  }
}

export async function readEntityChanges(
  api: RelayApi,
  changeEventId: number,
  signal: AbortSignal,
): Promise<EntityChange[]> {
  const changes: EntityChange[] = [];
  let cursor = 0;
  while (true) {
    const page = await api.listEntityChanges(
      changeEventId,
      cursor,
      CHANGE_PAGE_SIZE,
      signal,
    );
    changes.push(...page.items);
    if (page.next_after_id === null) return changes;
    if (page.next_after_id <= cursor) {
      throw new Error("Entity-change detail did not advance its cursor.");
    }
    cursor = page.next_after_id;
  }
}

export function mergeChangeEvents(
  history: ChangeEvent[],
  polled: ChangeEvent[],
): ChangeEvent[] {
  return Array.from(
    new Map([...history, ...polled].map((event) => [event.id, event])).values(),
  ).sort((left, right) => right.id - left.id);
}

function operationCount(event: ChangeEvent, operation: OperationFilter) {
  if (operation === "created") return event.created_count;
  if (operation === "updated") return event.updated_count;
  if (operation === "deleted") return event.deleted_count;
  return event.created_count + event.updated_count + event.deleted_count;
}

function cadenceLabel(seconds: number) {
  if (seconds < 60) return `${seconds} seconds`;
  const minutes = seconds / 60;
  return `${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
}

function FreshnessCard({
  label,
  status,
  error,
}: {
  label: string;
  status: PipelineStatus | undefined;
  error: Error | null;
}) {
  const state = error === null ? (status?.state ?? "initializing") : "error";
  return (
    <article className={`freshness-card freshness-card--${state}`}>
      <div className="freshness-card__heading">
        <div>
          <p className="eyebrow">{label}</p>
          <h2>{state}</h2>
        </div>
        <span className={`freshness-indicator freshness-indicator--${state}`}>
          {state}
        </span>
      </div>
      {error === null ? (
        <dl>
          <div>
            <dt>Expected cadence</dt>
            <dd>
              {status === undefined
                ? "Waiting for status"
                : cadenceLabel(status.expected_interval_seconds)}
            </dd>
          </div>
          <div>
            <dt>Last successful check</dt>
            <dd>
              {status?.last_checked_at === null || status === undefined
                ? "Not checked yet"
                : formatDateTime(status.last_checked_at)}
            </dd>
          </div>
          <div>
            <dt>Last actual change</dt>
            <dd>
              {status?.last_changed_at === null || status === undefined
                ? "No change recorded"
                : formatDateTime(status.last_changed_at)}
            </dd>
          </div>
          {status?.current_window_end === null || status === undefined ? null : (
            <div>
              <dt>Live window ends</dt>
              <dd>{formatDateTime(status.current_window_end)}</dd>
            </div>
          )}
          {status?.next_window_start === null || status === undefined ? null : (
            <div>
              <dt>Next live window</dt>
              <dd>{formatDateTime(status.next_window_start)}</dd>
            </div>
          )}
        </dl>
      ) : (
        <p className="freshness-card__error">{error.message}</p>
      )}
    </article>
  );
}

function fieldLabel(field: string) {
  const labels: Record<string, string> = {
    now_cost: "Player price",
    news: "Injury / availability news",
    status: "Player status",
    chance_of_playing_next_round: "Availability next gameweek",
    chance_of_playing_this_round: "Availability this gameweek",
    kickoff_time: "Fixture kickoff",
    event: "Gameweek",
  };
  return labels[field] ?? field.replaceAll("_", " ");
}

function FieldValue({ field, changeValue }: {
  field: string;
  changeValue: ChangeValue;
}) {
  if (!changeValue.present) {
    return <span className="change-value change-value--absent">Not present</span>;
  }
  if (changeValue.value === null) {
    return <span className="change-value change-value--null">null</span>;
  }
  if (field === "now_cost" && typeof changeValue.value === "number") {
    return <span>£{(changeValue.value / 10).toFixed(1)}m</span>;
  }
  if (field === "kickoff_time" && typeof changeValue.value === "string") {
    return <span>{formatDateTime(changeValue.value)}</span>;
  }
  if (typeof changeValue.value === "object") {
    return <StructuredValue value={changeValue.value} />;
  }
  if (changeValue.value === "") {
    return <span className="change-value change-value--empty">Empty string</span>;
  }
  return <StructuredValue value={changeValue.value} />;
}

function ChangeEventDetail({ record }: { record: DetailedChangeEvent }) {
  return (
    <div className="change-detail">
      <div className="change-detail__summary">
        <span>{record.entity_family}</span>
        <strong>
          {record.created_count} created · {record.updated_count} updated ·{" "}
          {record.deleted_count} deleted
        </strong>
        <small>
          {record.source_key} · checked {formatDateTime(record.fetched_at)}
        </small>
      </div>
      {record.entity_changes.length === 0 ? (
        <p className="change-detail__empty">No entity details were returned.</p>
      ) : (
        <div className="entity-change-list">
          {record.entity_changes.map((entity) => (
            <article className="entity-change" key={entity.id}>
              <header>
                <div>
                  <span className={`operation operation--${entity.kind}`}>
                    {entity.kind}
                  </span>
                  <h3>{entity.entity_label}</h3>
                </div>
                <code>{entity.entity_key}</code>
              </header>
              <div className="field-change-list">
                {entity.fields.map((field) => (
                  <div className="field-change" key={field.field}>
                    <h4>{fieldLabel(field.field)}</h4>
                    <div>
                      <section>
                        <small>Before</small>
                        <FieldValue field={field.field} changeValue={field.before} />
                      </section>
                      <span aria-hidden="true">→</span>
                      <section>
                        <small>After</small>
                        <FieldValue field={field.field} changeValue={field.after} />
                      </section>
                    </div>
                  </div>
                ))}
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}

export function ActivityPage() {
  const api = useRelayApi();
  const queryClient = useQueryClient();
  const [hidden, setHidden] = useState(document.hidden);
  const [family, setFamily] = useState("all");
  const [operation, setOperation] = useState<OperationFilter>("all");
  const [nextBeforeId, setNextBeforeId] = useState<number | null | undefined>();
  const [olderError, setOlderError] = useState<Error | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const eventsRef = useRef<ChangeEvent[]>([]);
  const lastSeenId = useRef(0);
  const initialized = useRef(false);
  const changesQuery = useQuery({
    queryKey: ["change-events"],
    queryFn: async ({ signal }) => {
      if (!initialized.current) {
        const page = await api.listRecentChangeEvents(CHANGE_PAGE_SIZE, signal);
        const events = mergeChangeEvents([], page.items);
        eventsRef.current = events;
        lastSeenId.current = Math.max(0, ...events.map((event) => event.id));
        initialized.current = true;
        setNextBeforeId(page.next_before_id);
        return events;
      }
      const polled = await readChangesAfter(api, lastSeenId.current, signal);
      const events = mergeChangeEvents(eventsRef.current, polled);
      eventsRef.current = events;
      lastSeenId.current = Math.max(
        lastSeenId.current,
        ...events.map((event) => event.id),
      );
      return events;
    },
    retry: false,
    refetchInterval: hidden ? false : CHANGE_POLL_MILLISECONDS,
    refetchIntervalInBackground: false,
  });
  const statusQuery = useQuery({
    queryKey: ["ingestion-status"],
    queryFn: ({ signal }) => api.getIngestionStatus(signal),
    retry: false,
    refetchInterval: hidden ? false : CHANGE_POLL_MILLISECONDS,
    refetchIntervalInBackground: false,
  });
  const events = changesQuery.data ?? EMPTY_CHANGE_EVENTS;

  useEffect(() => {
    const visibilityChanged = () => {
      setHidden(document.hidden);
      if (!document.hidden) {
        void changesQuery.refetch();
        void statusQuery.refetch();
      }
    };
    document.addEventListener("visibilitychange", visibilityChanged);
    return () =>
      document.removeEventListener("visibilitychange", visibilityChanged);
  }, [changesQuery, statusQuery]);

  const loadOlder = useCallback(async () => {
    if (nextBeforeId === null || nextBeforeId === undefined) return;
    setLoadingOlder(true);
    setOlderError(null);
    try {
      const page = await api.listChangeEventHistory(
        nextBeforeId,
        CHANGE_PAGE_SIZE,
        new AbortController().signal,
      );
      const merged = mergeChangeEvents(eventsRef.current, page.items);
      eventsRef.current = merged;
      setNextBeforeId(page.next_before_id);
      queryClient.setQueryData(["change-events"], merged);
    } catch (error) {
      setOlderError(error instanceof Error ? error : new Error(String(error)));
    } finally {
      setLoadingOlder(false);
    }
  }, [api, nextBeforeId, queryClient]);

  const families = useMemo(
    () => Array.from(new Set(events.map((event) => event.entity_family))).sort(),
    [events],
  );
  const filteredEvents = useMemo(
    () =>
      events.filter(
        (event) =>
          (family === "all" || event.entity_family === family) &&
          operationCount(event, operation) > 0,
      ),
    [events, family, operation],
  );

  const columns = useMemo<ColumnDef<ChangeEvent>[]>(
    () => [
      {
        accessorKey: "created_at",
        header: "Observed",
        cell: ({ getValue }) => formatDateTime(getValue<string>()),
      },
      { accessorKey: "entity_family", header: "Family" },
      { accessorKey: "source_key", header: "Source" },
      {
        accessorKey: "source_event_id",
        header: "Gameweek",
        cell: ({ getValue }) => getValue<number | null>() ?? "—",
      },
      { accessorKey: "created_count", header: "Created" },
      { accessorKey: "updated_count", header: "Updated" },
      { accessorKey: "deleted_count", header: "Deleted" },
    ],
    [],
  );

  return (
    <ResourcePage
      eyebrow="Change feed"
      title="Activity"
      description="Accurate entity changes, ingestion freshness, and source checks."
      apiOperations={[
        API_OPERATIONS.listRecentChangeEvents,
        API_OPERATIONS.listChangeEvents,
        API_OPERATIONS.listChangeEventHistory,
        API_OPERATIONS.getIngestionStatus,
      ]}
      query={changesQuery}
      records={filteredEvents}
      columns={columns}
      getRowId={(row) => String(row.id)}
      getRowLabel={(row) => `Change ${row.id} · ${row.event_name}`}
      detailLoader={{
        apiOperation: API_OPERATIONS.listEntityChanges,
        queryKey: ["change-event-entities"],
        load: async (id, signal) => {
          const event = eventsRef.current.find((item) => item.id === Number(id));
          if (event === undefined) {
            throw new Error(`Change event ${id} is not loaded.`);
          }
          return {
            ...event,
            entity_changes: await readEntityChanges(api, event.id, signal),
          };
        },
      }}
      renderDetail={(record) => (
        <ChangeEventDetail record={record as DetailedChangeEvent} />
      )}
      beforeTable={
        <section className="freshness-grid" aria-label="Ingestion freshness">
          <FreshnessCard
            label="Reference ingestion"
            status={statusQuery.data?.reference}
            error={statusQuery.error}
          />
          <FreshnessCard
            label="Live ingestion"
            status={statusQuery.data?.live}
            error={statusQuery.error}
          />
        </section>
      }
      controls={
        <>
          <label>
            Family
            <select value={family} onChange={(event) => setFamily(event.target.value)}>
              <option value="all">All families</option>
              {families.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label>
            Operation
            <select
              value={operation}
              onChange={(event) =>
                setOperation(event.target.value as OperationFilter)
              }
            >
              <option value="all">Any change</option>
              <option value="created">Created</option>
              <option value="updated">Updated</option>
              <option value="deleted">Deleted</option>
            </select>
          </label>
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
            <button
              className="button"
              disabled={nextBeforeId === null || nextBeforeId === undefined || loadingOlder}
              type="button"
              onClick={() => void loadOlder()}
            >
              {loadingOlder
                ? "Loading older…"
                : nextBeforeId === null
                  ? "All history loaded"
                  : "Load older"}
            </button>
          </div>
          {olderError === null ? null : (
            <span className="filter-error">{errorMessage(olderError)}</span>
          )}
        </>
      }
      rawResponse={{
        items: filteredEvents,
        next_before_id: nextBeforeId ?? null,
        ingestion_status: statusQuery.data ?? null,
      }}
      emptyMessage="No change events match these filters."
    />
  );
}
