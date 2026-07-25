import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { RelayApiError } from "./api/errors";
import type { RelayApi } from "./api/relay-api";
import { createAppQueryClient } from "./app/queryClient";
import {
  changeEvent,
  element,
  makeFakeRelayApi,
} from "./test/fakeRelayApi";

function renderApplication(path: string, api: RelayApi = makeFakeRelayApi()) {
  const queryClient = createAppQueryClient();
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App api={api} queryClient={queryClient} />
    </MemoryRouter>,
  );
}

describe("relay explorer application", () => {
  it("selects only the explicitly current season and event", async () => {
    renderApplication("/");
    expect(
      screen.getByRole("heading", {
        name: "Explore what the relay is holding.",
      }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByLabelText("Season")).toHaveValue("2025-26"),
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Event")).toHaveValue("1"),
    );
    expect(screen.getByText("Version 4")).toBeInTheDocument();
    expect(screen.getAllByText("1").length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: "Refresh all" }));
    await userEvent.selectOptions(screen.getByLabelText("Event"), "");
    await userEvent.selectOptions(screen.getByLabelText("Event"), "1");
    await userEvent.selectOptions(screen.getByLabelText("Season"), "");
    await userEvent.selectOptions(screen.getByLabelText("Season"), "2025-26");

    await userEvent.click(screen.getByRole("link", { name: "Teams" }));
    expect(
      await screen.findByRole("heading", { name: "Teams" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Season")).toHaveValue("2025-26");
  });

  it.each([
    ["/seasons", "Seasons", "2025-26"],
    ["/events?season=2025-26&event=1", "Events", "Gameweek 1"],
    ["/phases?season=2025-26&event=1", "Phases", "Overall"],
    ["/teams?season=2025-26&event=1", "Teams", "Northbridge FC"],
    [
      "/element-types?season=2025-26&event=1",
      "Element types",
      "Midfielder",
    ],
    ["/players?season=2025-26&event=1", "Players", "Ada Striker"],
    ["/fixtures?season=2025-26&event=1", "Fixtures", "Northbridge FC"],
    [
      "/event-status?season=2025-26&event=1",
      "Event status",
      "Bonus added",
    ],
    [
      "/live-players?season=2025-26&event=1",
      "Live players",
      "Ada Striker",
    ],
    ["/activity?season=2025-26&event=1", "Activity", "elements.updated"],
  ])("renders the curated %s route", async (path, heading, tableText) => {
    renderApplication(path);
    expect(
      await screen.findByRole("heading", { name: heading }),
    ).toBeInTheDocument();
    const table = await screen.findByRole("table");
    expect(within(table).getByText(tableText)).toBeInTheDocument();
  });

  it("supports player filters, search, detail loading, and raw JSON", async () => {
    const secondPlayer = {
      ...element,
      id: 11,
      first_name: "Beth",
      second_name: "Keeper",
      web_name: "Beth",
      team: 2,
      element_type: 1,
    };
    const api = makeFakeRelayApi({
      listElements: async () => [element, secondPlayer],
      getElement: async (_seasonId, elementId) =>
        elementId === 10 ? element : secondPlayer,
      listElementTypes: async () => [
        {
          id: 1,
          singular_name: "Goalkeeper",
        },
        {
          id: 3,
          singular_name: "Midfielder",
        },
      ],
    });
    renderApplication("/players?season=2025-26&event=1", api);
    expect(await screen.findByText("Ada Striker")).toBeInTheDocument();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Team/ }),
      "2",
    );
    expect(screen.queryByText("Ada Striker")).not.toBeInTheDocument();
    expect(screen.getByText("Beth Keeper")).toBeInTheDocument();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Position/ }),
      "1",
    );
    await userEvent.type(screen.getByRole("searchbox"), "Beth");
    await userEvent.click(screen.getByRole("button", { name: "Inspect" }));
    expect(
      await screen.findByRole("heading", { name: "Beth Keeper" }),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Raw JSON" }));
    expect(
      within(screen.getByRole("dialog")).getByTestId("json-view"),
    ).toHaveTextContent('"id": 11');
  });

  it("switches fixtures to the selected event endpoint", async () => {
    const listEventFixtures = vi
      .fn<RelayApi["listEventFixtures"]>()
      .mockResolvedValue([]);
    const api = makeFakeRelayApi({ listEventFixtures });
    renderApplication("/fixtures?season=2025-26&event=1", api);
    expect(await screen.findByText("Northbridge FC")).toBeInTheDocument();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Scope/ }),
      "event",
    );
    expect(
      await screen.findByText("No fixtures are stored for this scope."),
    ).toBeInTheDocument();
    expect(listEventFixtures).toHaveBeenCalledWith(
      "2025-26",
      1,
      expect.any(AbortSignal),
    );
  });

  it("requires manual selection when current markers are unavailable", async () => {
    const notIngested = new RelayApiError({
      status: 503,
      detail: "Current value has not been ingested.",
      path: "/current",
    });
    const noCurrentSeason = makeFakeRelayApi({
      getCurrentSeason: async () => {
        throw notIngested;
      },
    });
    renderApplication("/events", noCurrentSeason);
    expect(
      await screen.findByRole("heading", { name: "Choose a season" }),
    ).toBeInTheDocument();
  });

  it.each([
    "/phases",
    "/teams",
    "/element-types",
    "/players",
    "/fixtures",
    "/event-status",
    "/live-players",
  ])("requires a season on %s", async (path) => {
    const api = makeFakeRelayApi({
      getCurrentSeason: async () => {
        throw new RelayApiError({
          status: 503,
          detail: "No current season.",
          path: "/current",
        });
      },
    });
    renderApplication(path, api);
    expect(
      await screen.findByRole("heading", { name: "Choose a season" }),
    ).toBeInTheDocument();
  });

  it("requires an event for event-scoped views", async () => {
    const api = makeFakeRelayApi({
      getCurrentEvent: async () => {
        throw new RelayApiError({
          status: 503,
          detail: "No current event.",
          path: "/current",
        });
      },
    });
    renderApplication("/live-players?season=2025-26", api);
    expect(
      await screen.findByRole("heading", { name: "Choose an event" }),
    ).toBeInTheDocument();
  });

  it("requires an event when fixture scope is event-specific", async () => {
    const api = makeFakeRelayApi({
      getCurrentEvent: async () => {
        throw new RelayApiError({
          status: 503,
          detail: "No current event.",
          path: "/current",
        });
      },
    });
    renderApplication("/fixtures?season=2025-26&scope=event", api);
    expect(
      await screen.findByRole("heading", { name: "Choose an event" }),
    ).toBeInTheDocument();
  });

  it("labels previous, next, and unmarked events", async () => {
    const api = makeFakeRelayApi({
      listEvents: async () => [
        {
          id: 2,
          name: "Previous week",
          is_previous: true,
          is_current: false,
          is_next: false,
        },
        {
          id: 3,
          name: "Next week",
          is_previous: false,
          is_current: false,
          is_next: true,
        },
        {
          id: 4,
          name: "Future week",
          is_previous: false,
          is_current: false,
          is_next: false,
        },
      ],
    });
    renderApplication("/events?season=2025-26&event=2", api);
    const table = await screen.findByRole("table");
    expect(within(table).getByText("Previous")).toBeInTheDocument();
    expect(within(table).getByText("Next")).toBeInTheDocument();
    expect(within(table).getAllByText("—").length).toBeGreaterThan(0);
  });

  it("renders an event-status response without league metadata", async () => {
    const api = makeFakeRelayApi({
      getEventStatus: async () => ({ status: [] }),
    });
    renderApplication("/event-status?season=2025-26&event=1", api);
    expect(
      await screen.findByText(
        "The stored event-status response has no status rows.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Latest stored event-status response."),
    ).toBeInTheDocument();
  });

  it("surfaces not-ingested and unavailable errors", async () => {
    const notIngested = makeFakeRelayApi({
      getEventStatus: async () => {
        throw new RelayApiError({
          status: 503,
          detail: "FPL event status data has not been ingested yet.",
          path: "/event-status",
        });
      },
    });
    const { unmount } = renderApplication(
      "/event-status?season=2025-26&event=1",
      notIngested,
    );
    expect(await screen.findByText("Data not ingested")).toBeInTheDocument();
    unmount();

    const offline = makeFakeRelayApi({
      listTeams: async () => {
        throw new RelayApiError({
          status: 0,
          detail: "Connection refused.",
          path: "/teams",
        });
      },
    });
    renderApplication("/teams?season=2025-26&event=1", offline);
    expect(await screen.findByText("Relay unavailable")).toBeInTheDocument();
  });

  it("shows checking and offline health states", async () => {
    let rejectHealth: ((error: Error) => void) | undefined;
    const healthPromise = new Promise<never>((_resolve, reject) => {
      rejectHealth = reject;
    });
    const api = makeFakeRelayApi({
      getHealth: async () => healthPromise,
    });
    renderApplication("/", api);
    expect(screen.getByText("Checking relay")).toBeInTheDocument();
    rejectHealth?.(new Error("offline"));
    expect(await screen.findByText("Relay offline")).toBeInTheDocument();
  });

  it("appends streamed events and reconnects explicitly after failure", async () => {
    const watchChangeEvents = vi
      .fn<RelayApi["watchChangeEvents"]>()
      .mockImplementationOnce(async ({ onEvent }) => {
        onEvent({
          ...changeEvent,
          id: 2,
          entity_family: "fixtures",
          event_name: "fixtures.updated",
        });
        throw new Error("stream ended");
      })
      .mockImplementation(async () => {
        throw new Error("still ended");
      });
    const api = makeFakeRelayApi({ watchChangeEvents });
    renderApplication("/activity?season=2025-26&event=1", api);
    expect(await screen.findByText("fixtures.updated")).toBeInTheDocument();
    const reconnect = await screen.findByRole("button", {
      name: "Reconnect from 2",
    });
    await userEvent.click(reconnect);
    await waitFor(() => expect(watchChangeEvents).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("still ended")).toBeInTheDocument();
  });

  it("redirects unknown routes to the overview", async () => {
    renderApplication("/unknown");
    expect(
      await screen.findByRole("heading", {
        name: "Explore what the relay is holding.",
      }),
    ).toBeInTheDocument();
  });
});
