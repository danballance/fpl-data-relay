import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import type { ReactNode } from "react";
import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

import { useTableUrlState } from "../lib/query-state";
import { DataTable } from "./DataTable";
import { JsonView } from "./JsonView";
import { PageHeader } from "./PageHeader";
import { RecordInspector } from "./RecordInspector";
import { StatusPanel } from "./StatusPanel";

export interface DetailLoader<T> {
  queryKey: readonly unknown[];
  load: (id: string, signal: AbortSignal) => Promise<T>;
}

export interface ResourceQuery {
  data: unknown;
  error: Error | null;
  isError: boolean;
  isFetching: boolean;
  isPending: boolean;
  refetch: () => Promise<unknown>;
}

export function ResourcePage<T>({
  eyebrow,
  title,
  description,
  query,
  records: recordsOverride,
  columns,
  getRowId,
  getRowLabel,
  detailLoader,
  beforeTable,
  controls,
  renderDetail,
  emptyMessage,
  rawResponse,
}: {
  eyebrow: string;
  title: string;
  description: string;
  query: ResourceQuery;
  records?: T[];
  columns: ColumnDef<T>[];
  getRowId: (row: T) => string;
  getRowLabel: (row: T) => string;
  detailLoader?: DetailLoader<T>;
  beforeTable?: ReactNode;
  controls?: ReactNode;
  renderDetail?: (record: unknown) => ReactNode;
  emptyMessage: string;
  rawResponse?: unknown;
}) {
  const records =
    recordsOverride ??
    (Array.isArray(query.data) ? (query.data as T[]) : undefined);
  const tableState = useTableUrlState();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedId = searchParams.get("record");
  const selectedRecord =
    records?.find((row) => getRowId(row) === selectedId) ?? null;
  const detailQuery = useQuery({
    queryKey: [
      ...(detailLoader?.queryKey ?? ["list-record"]),
      selectedId ?? "none",
    ],
    queryFn: ({ signal }) => detailLoader!.load(selectedId!, signal),
    enabled:
      detailLoader !== undefined &&
      selectedId !== null &&
      selectedRecord !== null,
    retry: false,
  });

  const inspect = useCallback(
    (record: T) => {
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        next.set("record", getRowId(record));
        return next;
      });
    },
    [getRowId, setSearchParams],
  );
  const close = () =>
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.delete("record");
      return next;
    });

  return (
    <>
      <PageHeader
        eyebrow={eyebrow}
        title={title}
        description={description}
        actions={
          <button
            className="button"
            disabled={query.isFetching}
            type="button"
            onClick={() => void query.refetch()}
          >
            {query.isFetching ? "Refreshing…" : "Refresh"}
          </button>
        }
      />
      {beforeTable}
      {controls === undefined ? null : (
        <div className="filter-bar">{controls}</div>
      )}
      {query.isPending ? <StatusPanel state="loading" /> : null}
      {query.isError ? (
        <StatusPanel
          state="error"
          error={query.error}
          onRetry={() => void query.refetch()}
        />
      ) : null}
      {records === undefined ? null : (
        <>
          <DataTable
            data={records}
            columns={columns}
            getRowId={getRowId}
            search={tableState.search}
            sorting={tableState.sorting}
            page={tableState.page}
            onSearchChange={tableState.setSearch}
            onSortingChange={tableState.setSorting}
            onPageChange={tableState.setPage}
            onInspect={inspect}
            emptyMessage={emptyMessage}
          />
          <details className="raw-response">
            <summary>View raw response</summary>
            <JsonView value={rawResponse ?? query.data} />
          </details>
        </>
      )}
      {selectedRecord === null ? null : (
        <RecordInspector
          title={getRowLabel(selectedRecord)}
          record={detailQuery.data ?? selectedRecord}
          loading={detailQuery.isPending && detailLoader !== undefined}
          error={detailQuery.error ?? null}
          onClose={close}
          renderFields={renderDetail}
        />
      )}
    </>
  );
}
