import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ColumnDef, SortingState } from "@tanstack/react-table";
import {
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { RelayApiError } from "../api/errors";
import {
  ApiDocsActions,
  API_OPERATIONS,
  swaggerOperationHref,
} from "./ApiDocsLink";
import { DataTable } from "./DataTable";
import { JsonView } from "./JsonView";
import { RecordInspector } from "./RecordInspector";
import { ResourcePage } from "./ResourcePage";
import { StatusPanel } from "./StatusPanel";
import { StructuredValue } from "./StructuredValue";

interface Row {
  id: number;
  name: string;
}

function TableHarness({
  onInspect,
}: {
  onInspect: (row: Row) => void;
}) {
  const [search, setSearch] = useState("");
  const [sorting, setSorting] = useState<SortingState>([]);
  const [page, setPage] = useState(0);
  const data = Array.from({ length: 30 }, (_, index) => ({
    id: index + 1,
    name: `Record ${String(index + 1).padStart(2, "0")}`,
  }));
  const columns: ColumnDef<Row>[] = [
    { accessorKey: "id", header: "ID" },
    { accessorKey: "name", header: "Name" },
  ];
  return (
    <DataTable
      data={data}
      columns={columns}
      getRowId={(row) => String(row.id)}
      search={search}
      sorting={sorting}
      page={page}
      onSearchChange={setSearch}
      onSortingChange={setSorting}
      onPageChange={setPage}
      onInspect={onInspect}
      emptyMessage="Nothing matched."
    />
  );
}

describe("shared explorer components", () => {
  it("renders loading and classified error states", async () => {
    const retry = vi.fn();
    const { rerender } = render(<StatusPanel state="loading" />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading relay data");

    rerender(
      <StatusPanel
        state="error"
        error={
          new RelayApiError({
            status: 503,
            detail: "Not ingested.",
            path: "/resource",
          })
        }
        onRetry={retry}
      />,
    );
    expect(screen.getByText("Data not ingested")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalledOnce();

    rerender(
      <StatusPanel
        state="error"
        error={
          new RelayApiError({
            status: 0,
            detail: "Offline.",
            path: "/resource",
          })
        }
      />,
    );
    expect(screen.getByText("Relay unavailable")).toBeInTheDocument();

    rerender(<StatusPanel state="error" error="bad" />);
    expect(screen.getByText("An unknown error occurred.")).toBeInTheDocument();
  });

  it("renders structured and exact JSON values", () => {
    const { rerender } = render(
      <StructuredValue
        value={{
          name: "Relay",
          enabled: true,
          value: null,
          nested: { count: 2 },
          rows: ["one", "two"],
          empty: [],
        }}
      />,
    );
    expect(screen.getByText("Relay")).toBeInTheDocument();
    expect(screen.getByText("Empty list")).toBeInTheDocument();
    expect(screen.getByText("null")).toBeInTheDocument();

    rerender(<JsonView value={{ name: "Relay" }} />);
    expect(screen.getByTestId("json-view")).toHaveTextContent(
      '"name": "Relay"',
    );
  });

  it("switches inspector modes and closes from either close surface", async () => {
    const close = vi.fn();
    const { container } = render(
      <RecordInspector
        title="Record 1"
        record={{ id: 1, name: "Relay" }}
        loading={false}
        error={null}
        onClose={close}
        apiOperation={API_OPERATIONS.getSeason}
      />,
    );
    const endpointLink = screen.getByRole("link", { name: /API endpoint/ });
    expect(endpointLink).toHaveAttribute(
      "href",
      "/api/docs#/Reference%20Data/get_season",
    );
    expect(endpointLink).toHaveAttribute("target", "_blank");
    expect(screen.getByText("Relay")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Raw JSON" }));
    expect(screen.getByTestId("json-view")).toHaveTextContent('"id": 1');
    fireEvent.mouseDown(screen.getByRole("dialog"));
    expect(close).not.toHaveBeenCalled();
    fireEvent.mouseDown(container.querySelector(".inspector-backdrop")!);
    expect(close).toHaveBeenCalledOnce();
    await userEvent.click(screen.getByRole("button", { name: "Close details" }));
    expect(close).toHaveBeenCalledTimes(2);
  });

  it("builds typed operation links and rejects empty action groups", () => {
    expect(swaggerOperationHref(API_OPERATIONS.getEventStatus)).toBe(
      "/api/docs#/Live%20Data/get_event_status",
    );
    render(
      <ApiDocsActions
        operations={[
          API_OPERATIONS.listRecentChangeEvents,
          API_OPERATIONS.getIngestionStatus,
        ]}
      />,
    );
    expect(screen.getByText("API endpoints")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Recent changes/ })).toHaveAttribute(
      "href",
      "/api/docs#/Change%20Events/list_recent_change_events",
    );
    expect(() => render(<ApiDocsActions operations={[]} />)).toThrow(
      "API documentation actions require at least one operation.",
    );
  });

  it("renders inspector loading and error content", () => {
    const { rerender } = render(
      <RecordInspector
        title="Loading"
        record={null}
        loading
        error={null}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("Loading relay data…")).toBeInTheDocument();
    rerender(
      <RecordInspector
        title="Broken"
        record={null}
        loading={false}
        error={new Error("detail failed")}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("detail failed")).toBeInTheDocument();
  });

  it(
    "searches, sorts, pages, and inspects table rows",
    async () => {
      const inspect = vi.fn();
      render(<TableHarness onInspect={inspect} />);
      expect(screen.getByText("30 records")).toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "Next" }));
      expect(screen.getByText("Record 30")).toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "Previous" }));
      await userEvent.click(screen.getByRole("button", { name: /^Name/ }));
      expect(
        screen.getByRole("button", { name: /Name ↑/ }),
      ).toBeInTheDocument();
      await userEvent.type(screen.getByRole("searchbox"), "Record 30");
      expect(screen.getByText("1 records")).toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "Inspect" }));
      expect(inspect).toHaveBeenCalledWith({ id: 30, name: "Record 30" });
      await userEvent.clear(screen.getByRole("searchbox"));
      await userEvent.type(screen.getByRole("searchbox"), "missing");
      expect(screen.getByText("Nothing matched.")).toBeInTheDocument();
    },
    10_000,
  );

  it("composes resource loading, detail, refresh, and raw response behavior", async () => {
    const refetch = vi.fn(async () => undefined);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const query = {
      data: [{ id: 1, name: "Relay" }],
      error: null,
      isError: false,
      isFetching: false,
      isPending: false,
      refetch,
    };
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <ResourcePage
            eyebrow="Test"
            title="Records"
            description="Test records."
            apiOperations={[API_OPERATIONS.listSeasons]}
            query={query}
            columns={[{ accessorKey: "name", header: "Name" }]}
            getRowId={(row: Row) => String(row.id)}
            getRowLabel={(row: Row) => row.name}
            detailLoader={{
              apiOperation: API_OPERATIONS.getSeason,
              queryKey: ["record"],
              load: async () => ({ id: 1, name: "Detailed relay" }),
            }}
            controls={<span>Controls</span>}
            emptyMessage="Empty"
            rawResponse={{ rows: query.data }}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(refetch).toHaveBeenCalledOnce();
    await userEvent.click(screen.getByRole("button", { name: "Inspect" }));
    expect(await screen.findByText("Detailed relay")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Close details" }));
    await userEvent.click(screen.getByText("View raw response"));
    expect(screen.getByTestId("json-view")).toHaveTextContent('"rows"');
    expect(screen.getByText("Controls")).toBeInTheDocument();
  });

  it("renders resource loading and request errors", () => {
    const queryClient = new QueryClient();
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <ResourcePage<Row>
            eyebrow="Test"
            title="Loading"
            description="Loading."
            apiOperations={[API_OPERATIONS.listSeasons]}
            query={{
              data: undefined,
              error: null,
              isError: false,
              isFetching: true,
              isPending: true,
              refetch: async () => undefined,
            }}
            columns={[]}
            getRowId={(row) => String(row.id)}
            getRowLabel={(row) => row.name}
            emptyMessage="Empty"
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByText("Loading relay data…")).toBeInTheDocument();
    rerender(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <ResourcePage<Row>
            eyebrow="Test"
            title="Error"
            description="Error."
            apiOperations={[API_OPERATIONS.listSeasons]}
            query={{
              data: undefined,
              error: new Error("request failed"),
              isError: true,
              isFetching: false,
              isPending: false,
              refetch: async () => undefined,
            }}
            columns={[]}
            getRowId={(row) => String(row.id)}
            getRowLabel={(row) => row.name}
            emptyMessage="Empty"
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByText("request failed")).toBeInTheDocument();
  });
});
