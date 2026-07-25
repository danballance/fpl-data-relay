import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  type ColumnDef,
  type OnChangeFn,
  type PaginationState,
  type SortingState,
  useReactTable,
} from "@tanstack/react-table";
import { useMemo } from "react";

export const TABLE_PAGE_SIZE = 25;

export function DataTable<T>({
  data,
  columns,
  getRowId,
  search,
  sorting,
  page,
  onSearchChange,
  onSortingChange,
  onPageChange,
  onInspect,
  emptyMessage,
}: {
  data: T[];
  columns: ColumnDef<T>[];
  getRowId: (row: T) => string;
  search: string;
  sorting: SortingState;
  page: number;
  onSearchChange: (value: string) => void;
  onSortingChange: OnChangeFn<SortingState>;
  onPageChange: (value: number) => void;
  onInspect: (row: T) => void;
  emptyMessage: string;
}) {
  const tableColumns = useMemo<ColumnDef<T>[]>(
    () => [
      ...columns,
      {
        id: "inspect",
        header: "",
        enableSorting: false,
        cell: ({ row }) => (
          <button
            className="text-button"
            type="button"
            onClick={() => onInspect(row.original)}
          >
            Inspect
          </button>
        ),
      },
    ],
    [columns, onInspect],
  );
  const pagination = { pageIndex: page, pageSize: TABLE_PAGE_SIZE };
  const onPaginationChange: OnChangeFn<PaginationState> = (updater) => {
    const next = typeof updater === "function" ? updater(pagination) : updater;
    onPageChange(next.pageIndex);
  };
  const table = useReactTable({
    data,
    columns: tableColumns,
    getRowId,
    state: {
      globalFilter: search,
      sorting,
      pagination,
    },
    onSortingChange,
    onPaginationChange,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    autoResetPageIndex: false,
  });

  return (
    <div className="table-region">
      <div className="table-toolbar">
        <label className="search-field">
          <span className="sr-only">Search records</span>
          <input
            type="search"
            value={search}
            placeholder="Search records"
            onChange={(event) => onSearchChange(event.target.value)}
          />
        </label>
        <span className="record-count">
          {table.getFilteredRowModel().rows.length.toLocaleString()} records
        </span>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <th key={header.id}>
                    {header.isPlaceholder ? null : (
                      <button
                        className="column-heading"
                        disabled={!header.column.getCanSort()}
                        type="button"
                        onClick={header.column.getToggleSortingHandler()}
                      >
                        {flexRender(
                          header.column.columnDef.header,
                          header.getContext(),
                        )}
                        {{
                          asc: " ↑",
                          desc: " ↓",
                        }[header.column.getIsSorted() as string] ?? ""}
                      </button>
                    )}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr key={row.id}>
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {table.getFilteredRowModel().rows.length === 0 ? (
        <div className="empty-state">{emptyMessage}</div>
      ) : null}
      <footer className="table-footer">
        <span>
          Page {table.getState().pagination.pageIndex + 1} of{" "}
          {Math.max(table.getPageCount(), 1)}
        </span>
        <div className="button-group">
          <button
            className="button"
            disabled={!table.getCanPreviousPage()}
            type="button"
            onClick={() => table.previousPage()}
          >
            Previous
          </button>
          <button
            className="button"
            disabled={!table.getCanNextPage()}
            type="button"
            onClick={() => table.nextPage()}
          >
            Next
          </button>
        </div>
      </footer>
    </div>
  );
}
