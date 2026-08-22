import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { RelayApiError } from "./api/errors";
import type { RelayApi } from "./api/relay-api";
import type { CommunityReport } from "./api/types";
import { createAppQueryClient } from "./app/queryClient";
import {
  changeEvent,
  communityReport,
  communityReportSummary,
  communityStrategy,
  element,
  entityChange,
  ingestionStatus,
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
  it("renders community coverage, evidence, entities, and history", async () => {
    const history = vi
      .fn<RelayApi["listCommunityReportHistory"]>()
      .mockResolvedValue({
        items: [
          {
            ...communityReportSummary,
            id: 6,
            report_date: "2026-08-12",
          },
        ],
        next_before_id: null,
      });
    const api = makeFakeRelayApi({
      listCommunityStrategies: async () => [communityStrategy],
      getLatestCommunityReport: async () => communityReport,
      getCommunityReport: async () => communityReport,
      listRecentCommunityReports: async () => ({
        items: [communityReportSummary],
        next_before_id: 7,
      }),
      listCommunityReportHistory: history,
    });
    renderApplication("/community", api);

    expect(
      await screen.findByRole("heading", {
        name: "What the FPL community is discussing.",
      }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", {
        name: communityReport.content.stories[0].headline,
      }),
    ).toBeInTheDocument();
    expect(screen.getByText(/1 configured source/)).toBeInTheDocument();
    expect(screen.getByText(/1 of 10 target stories/)).toBeInTheDocument();
    expect(screen.getByText("Documents reused")).toBeInTheDocument();
    expect(screen.getByText("1 / 1")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /FPL Analyst/ }),
    ).toHaveAttribute("href", "https://x.com/analyst/status/1");
    expect(screen.getByRole("link", { name: /Ada/ })).toHaveAttribute(
      "href",
      "/players?season=2025-26&record=10",
    );
    expect(screen.getByLabelText("Community strategy")).toHaveValue(
      communityStrategy.key,
    );
    expect(screen.getByLabelText("Historical report")).toHaveTextContent(
      "2026-08-13 · 1 stories",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Load older reports" }),
    );
    expect(
      await screen.findByRole("option", { name: "2026-08-12 · 1 stories" }),
    ).toBeInTheDocument();
    expect(history).toHaveBeenCalledWith(
      communityStrategy.key,
      7,
      100,
      expect.any(AbortSignal),
    );
  });

  it("renders every entity shape and navigates report query state", async () => {
    const completeReport: CommunityReport = structuredClone(communityReport);
    const baseStory = completeReport.content.stories[0];
    const playerEntity = baseStory.entities[0];
    if (playerEntity.entity_type !== "player") {
      throw new Error("Community fixture must start with a player entity.");
    }
    completeReport.content.coverage = {
      ...completeReport.content.coverage,
      configured_source_count: 1,
      successful_source_count: 1,
      failed_sources: [],
    };
    completeReport.content.stories = Array.from(
      { length: 10 },
      (_, index) => ({
        ...baseStory,
        rank: index + 1,
        headline: `${baseStory.headline} ${index + 1}`,
        entities:
          index === 0
            ? [
                {
                  ...playerEntity,
                  snapshot: {
                    ...playerEntity.snapshot,
                    now_cost: null,
                    total_points: null,
                  },
                },
                {
                  entity_type: "team",
                  season_id: "2025-26",
                  entity_id: 2,
                  display_name: "Southbank FC",
                  snapshot: {
                    name: "Southbank FC",
                    short_name: "SOU",
                    strength: null,
                    strength_overall_home: null,
                    strength_overall_away: null,
                    strength_attack_home: null,
                    strength_attack_away: null,
                    strength_defence_home: null,
                    strength_defence_away: null,
                  },
                },
                {
                  entity_type: "event",
                  season_id: "2025-26",
                  entity_id: 2,
                  display_name: "Gameweek 2",
                  snapshot: {
                    name: "Gameweek 2",
                    deadline_time: null,
                    average_entry_score: null,
                    highest_score: null,
                    highest_scoring_entry: null,
                    finished: false,
                    data_checked: false,
                    is_previous: false,
                    is_current: false,
                    is_next: true,
                  },
                },
                {
                  entity_type: "fixture",
                  season_id: "2025-26",
                  entity_id: 30,
                  display_name: "NOR v SOU",
                  snapshot: {
                    event_id: 1,
                    kickoff_time: "2026-08-15T14:00:00Z",
                    home_team_id: 1,
                    home_team_name: "Northbridge FC",
                    away_team_id: 2,
                    away_team_name: "Southbank FC",
                    home_score: 2,
                    away_score: 1,
                    started: true,
                    finished: true,
                  },
                },
                {
                  entity_type: "fixture",
                  season_id: "2025-26",
                  entity_id: 31,
                  display_name: "SOU v NOR",
                  snapshot: {
                    event_id: null,
                    kickoff_time: null,
                    home_team_id: 2,
                    home_team_name: "Southbank FC",
                    away_team_id: 1,
                    away_team_name: "Northbridge FC",
                    home_score: null,
                    away_score: null,
                    started: false,
                    finished: false,
                  },
                },
              ]
            : baseStory.entities,
      }),
    );
    const secondStrategy = {
      ...communityStrategy,
      key: "second-community-strategy",
      name: "Second strategy",
    };
    const latest = vi
      .fn<RelayApi["getLatestCommunityReport"]>()
      .mockResolvedValue(completeReport);
    const historical = vi
      .fn<RelayApi["getCommunityReport"]>()
      .mockResolvedValue(completeReport);
    renderApplication(
      `/community?strategy=${communityStrategy.key}&report=7`,
      makeFakeRelayApi({
        listCommunityStrategies: async () => [
          communityStrategy,
          secondStrategy,
        ],
        getLatestCommunityReport: latest,
        getCommunityReport: historical,
        listRecentCommunityReports: async () => ({
          items: [communityReportSummary],
          next_before_id: null,
        }),
      }),
    );

    expect(
      await screen.findByText(/price unavailable/),
    ).toBeInTheDocument();
    expect(screen.getByText(/strength —/)).toBeInTheDocument();
    expect(screen.getByText(/Open · average score —/)).toBeInTheDocument();
    expect(
      screen.getByText(/Northbridge FC v Southbank FC · 2–1/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Southbank FC v Northbridge FC · not started/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/target stories/)).not.toBeInTheDocument();
    expect(historical).toHaveBeenCalledWith(7, expect.any(AbortSignal));

    await userEvent.selectOptions(
      screen.getByLabelText("Historical report"),
      "",
    );
    await waitFor(() => expect(latest).toHaveBeenCalled());
    await userEvent.selectOptions(
      screen.getByLabelText("Historical report"),
      "7",
    );
    expect(screen.getByLabelText("Historical report")).toHaveValue("7");
    await userEvent.selectOptions(
      screen.getByLabelText("Community strategy"),
      secondStrategy.key,
    );
    await waitFor(() =>
      expect(latest).toHaveBeenCalledWith(
        secondStrategy.key,
        expect.any(AbortSignal),
      ),
    );
  });

  it("recovers requests and ignores invalid report IDs", async () => {
    const strategyRequest = vi
      .fn<RelayApi["listCommunityStrategies"]>()
      .mockRejectedValueOnce(new Error("Strategy catalog unavailable."))
      .mockResolvedValue([communityStrategy]);
    const reportRequest = vi
      .fn<RelayApi["getLatestCommunityReport"]>()
      .mockRejectedValueOnce(
        new RelayApiError({
          status: 500,
          detail: "Report failed.",
          path: "/v1/community-reports/latest",
        }),
      )
      .mockResolvedValue(communityReport);
    renderApplication(
      `/community?strategy=${communityStrategy.key}&report=invalid`,
      makeFakeRelayApi({
        listCommunityStrategies: strategyRequest,
        getLatestCommunityReport: reportRequest,
      }),
    );

    expect(await screen.findByText("Request failed")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(
      await screen.findByText("Community report unavailable"),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(
      await screen.findByRole("heading", {
        name: communityReport.content.stories[0].headline,
      }),
    ).toBeInTheDocument();
    expect(reportRequest).toHaveBeenCalledWith(
      communityStrategy.key,
      expect.any(AbortSignal),
    );
  });

  it.each([
    [
      "unknown strategy",
      "/community?strategy=unknown",
      makeFakeRelayApi({
        listCommunityStrategies: async () => [communityStrategy],
      }),
      "Unknown strategy",
    ],
    [
      "known strategy without a report",
      `/community?strategy=${communityStrategy.key}`,
      makeFakeRelayApi({
        listCommunityStrategies: async () => [communityStrategy],
        getLatestCommunityReport: async () => {
          throw new RelayApiError({
            status: 503,
            detail: "No report has been generated.",
            path: "/v1/community-reports/latest",
          });
        },
      }),
      "No report generated",
    ],
    [
      "fatal report API failure",
      `/community?strategy=${communityStrategy.key}`,
      makeFakeRelayApi({
        listCommunityStrategies: async () => [communityStrategy],
        getLatestCommunityReport: async () => {
          throw new RelayApiError({
            status: 500,
            detail: "Database unavailable.",
            path: "/v1/community-reports/latest",
          });
        },
      }),
      "Community report unavailable",
    ],
  ])("shows a dedicated %s state", async (_label, path, api, expected) => {
    renderApplication(path, api);
    expect(await screen.findByText(expected)).toBeInTheDocument();
  });

  it("selects only the explicitly current season and event", async () => {
    renderApplication("/");
    expect(
      await screen.findByRole("heading", {
        name: "Explore what the relay is holding.",
      }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByLabelText("Season")).toHaveValue("2025-26"),
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Event")).toHaveValue("1"),
    );
    expect(screen.getByText("Version 5")).toBeInTheDocument();
    expect(screen.getAllByText("1").length).toBeGreaterThan(0);
    const apiReference = screen.getByRole("link", { name: /API reference/ });
    expect(apiReference).toHaveAttribute("href", "/api/docs");
    expect(apiReference).toHaveAttribute("target", "_blank");
    expect(apiReference).toHaveAttribute("rel", "noreferrer");
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
    [
      "/seasons",
      "Seasons",
      "2025-26",
      "/api/docs#/Reference%20Data/list_seasons",
    ],
    [
      "/events?season=2025-26&event=1",
      "Events",
      "Gameweek 1",
      "/api/docs#/Reference%20Data/list_events",
    ],
    [
      "/phases?season=2025-26&event=1",
      "Phases",
      "Overall",
      "/api/docs#/Reference%20Data/list_phases",
    ],
    [
      "/teams?season=2025-26&event=1",
      "Teams",
      "Northbridge FC",
      "/api/docs#/Reference%20Data/list_teams",
    ],
    [
      "/element-types?season=2025-26&event=1",
      "Element types",
      "Midfielder",
      "/api/docs#/Reference%20Data/list_element_types",
    ],
    [
      "/players?season=2025-26&event=1",
      "Players",
      "Ada Striker",
      "/api/docs#/Reference%20Data/list_elements",
    ],
    [
      "/fixtures?season=2025-26&event=1",
      "Fixtures",
      "Northbridge FC",
      "/api/docs#/Reference%20Data/list_fixtures",
    ],
    [
      "/event-status?season=2025-26&event=1",
      "Event status",
      "Bonus added",
      "/api/docs#/Live%20Data/get_event_status",
    ],
    [
      "/live-players?season=2025-26&event=1",
      "Live players",
      "Ada Striker",
      "/api/docs#/Live%20Data/list_live_elements",
    ],
    [
      "/activity?season=2025-26&event=1",
      "Activity",
      "elements",
      "/api/docs#/Change%20Events/list_recent_change_events",
    ],
  ])("renders and documents the curated %s route", async (
    path,
    heading,
    tableText,
    swaggerHref,
  ) => {
    renderApplication(path);
    expect(
      await screen.findByRole("heading", { name: heading }),
    ).toBeInTheDocument();
    const table = await screen.findByRole("table");
    expect(await within(table).findByText(tableText)).toBeInTheDocument();
    if (heading === "Activity") {
      await userEvent.click(screen.getByText("API endpoints"));
      expect(screen.getByRole("link", { name: /Recent changes/ })).toHaveAttribute(
        "href",
        swaggerHref,
      );
    } else {
      expect(
        screen.getByRole("link", { name: /Explore endpoint/ }),
      ).toHaveAttribute("href", swaggerHref);
    }
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
    expect(screen.getByRole("link", { name: /API endpoint/ })).toHaveAttribute(
      "href",
      "/api/docs#/Reference%20Data/get_element",
    );
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
    expect(
      screen.getByRole("link", { name: /Explore endpoint/ }),
    ).toHaveAttribute("href", "/api/docs#/Reference%20Data/list_fixtures");
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
    expect(
      screen.getByRole("link", { name: /Explore endpoint/ }),
    ).toHaveAttribute(
      "href",
      "/api/docs#/Reference%20Data/list_event_fixtures",
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
      screen.getByText(
        "Latest stored event-status response · leagues: not reported.",
      ),
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
    expect(await screen.findByText("Checking relay")).toBeInTheDocument();
    rejectHealth?.(new Error("offline"));
    expect(await screen.findByText("Relay offline")).toBeInTheDocument();
  });

  it("distinguishes readiness failure and supports a manual retry", async () => {
    const getReadiness = vi
      .fn<RelayApi["getReadiness"]>()
      .mockRejectedValueOnce(
        new RelayApiError({
          status: 503,
          detail: "Database unavailable.",
          path: "/readyz",
          code: "database_unavailable",
        }),
      )
      .mockResolvedValue({ status: "ready", schema_version: 1 });
    renderApplication("/", makeFakeRelayApi({ getReadiness }));
    expect(
      await screen.findByRole("heading", { name: "Service unavailable" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Database unavailable.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry now" }));
    expect(
      await screen.findByRole("heading", {
        name: "Explore what the relay is holding.",
      }),
    ).toBeInTheDocument();
    expect(getReadiness).toHaveBeenCalledTimes(2);
  });

  it("shows stored activity with polling status", async () => {
    const api = makeFakeRelayApi({
      listRecentChangeEvents: async () => ({
        items: [
          {
            ...changeEvent,
            id: 2,
            entity_family: "fixtures",
            event_name: "fixtures.updated",
          },
        ],
        next_before_id: null,
      }),
    });
    renderApplication("/activity?season=2025-26&event=1", api);
    expect(
      await within(await screen.findByRole("table")).findByText("fixtures"),
    ).toBeInTheDocument();
    expect(screen.getByText("Polling")).toBeInTheDocument();
  });

  it("shows an activity polling error and retries from the cursor", async () => {
    const listRecentChangeEvents = vi
      .fn<RelayApi["listRecentChangeEvents"]>()
      .mockRejectedValueOnce(new Error("poll failed"))
      .mockResolvedValue({
        items: [changeEvent],
        next_before_id: null,
      });
    renderApplication(
      "/activity?season=2025-26&event=1",
      makeFakeRelayApi({ listRecentChangeEvents }),
    );
    expect((await screen.findAllByText("poll failed")).length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(
      await within(await screen.findByRole("table")).findByText("elements"),
    ).toBeInTheDocument();
    expect(listRecentChangeEvents).toHaveBeenCalledTimes(2);
  });

  it("loads older activity, filters it, and renders entity before/after values", async () => {
    const older = {
      ...changeEvent,
      id: 1,
      entity_family: "fixtures" as const,
      event_name: "fixtures.updated",
      source_key: "fixtures" as const,
    };
    const listChangeEventHistory = vi
      .fn<RelayApi["listChangeEventHistory"]>()
      .mockResolvedValue({ items: [older], next_before_id: null });
    const api = makeFakeRelayApi({
      listRecentChangeEvents: async () => ({
        items: [{ ...changeEvent, id: 2 }],
        next_before_id: 2,
      }),
      listChangeEventHistory,
      listEntityChanges: async () => ({
        items: [entityChange],
        next_after_id: null,
      }),
    });
    renderApplication("/activity?season=2025-26&event=1", api);

    expect(
      await within(await screen.findByRole("table")).findByText("elements"),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByText("API endpoints"));
    expect(screen.getByRole("link", { name: /Recent changes/ })).toHaveAttribute(
      "href",
      "/api/docs#/Change%20Events/list_recent_change_events",
    );
    expect(screen.getByRole("link", { name: /Catch-up polling/ })).toHaveAttribute(
      "href",
      "/api/docs#/Change%20Events/list_change_events",
    );
    expect(screen.getByRole("link", { name: /Older history/ })).toHaveAttribute(
      "href",
      "/api/docs#/Change%20Events/list_change_event_history",
    );
    expect(screen.getByRole("link", { name: /Ingestion status/ })).toHaveAttribute(
      "href",
      "/api/docs#/Change%20Events/get_ingestion_status",
    );
    await userEvent.click(screen.getByRole("button", { name: "Load older" }));
    expect(
      await within(screen.getByRole("table")).findAllByText("fixtures"),
    ).toHaveLength(2);
    expect(listChangeEventHistory).toHaveBeenCalledWith(
      2,
      100,
      expect.any(AbortSignal),
    );

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Family" }),
      "elements",
    );
    expect(
      within(screen.getByRole("table")).queryByText("fixtures"),
    ).not.toBeInTheDocument();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Operation" }),
      "deleted",
    );
    expect(
      screen.getByText("No change events match these filters."),
    ).toBeInTheDocument();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Operation" }),
      "updated",
    );
    await userEvent.click(screen.getByRole("button", { name: "Inspect" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("link", { name: /API endpoint/ })).toHaveAttribute(
      "href",
      "/api/docs#/Change%20Events/list_entity_changes",
    );
    expect(within(dialog).getByText("Player price")).toBeInTheDocument();
    expect(within(dialog).getByText("£7.5m")).toBeInTheDocument();
    expect(within(dialog).getByText("£7.6m")).toBeInTheDocument();
    expect(within(dialog).getByText("Not present")).toBeInTheDocument();
    expect(within(dialog).getByText("null")).toBeInTheDocument();
    expect(within(dialog).getByText("medical update")).toBeInTheDocument();
  });

  it("surfaces stale ingestion independently from activity polling", async () => {
    const api = makeFakeRelayApi({
      getIngestionStatus: async () => ({
        ...ingestionStatus,
        reference: { ...ingestionStatus.reference, state: "stale" },
      }),
    });
    renderApplication("/activity?season=2025-26&event=1", api);
    const freshness = await screen.findByRole("region", {
      name: "Ingestion freshness",
    });
    expect(within(freshness).getAllByText("stale")).toHaveLength(2);
    expect(screen.getByText("Polling")).toBeInTheDocument();
  });

  it("pauses activity polling while the document is hidden", async () => {
    vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    renderApplication("/activity?season=2025-26&event=1");
    expect(await screen.findByText("Paused")).toBeInTheDocument();
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
