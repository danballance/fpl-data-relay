import createClient from "openapi-fetch";

import type { paths } from "./generated";
import { RelayApiError } from "./errors";
import type {
  ChangeEvents,
  ChangeEventHistory,
  CommunityReport,
  CommunityReportHistory,
  CommunityStrategy,
  Element,
  ElementType,
  Event,
  EventStatus,
  Fixture,
  Health,
  LiveElement,
  EntityChanges,
  IngestionStatus,
  Phase,
  Readiness,
  Season,
  Team,
} from "./types";

const PAGE_SIZE = 100;

export interface RelayApi {
  getHealth(signal: AbortSignal): Promise<Health>;
  getReadiness(signal: AbortSignal): Promise<Readiness>;
  listSeasons(signal: AbortSignal): Promise<Season[]>;
  getCurrentSeason(signal: AbortSignal): Promise<Season>;
  getSeason(seasonId: string, signal: AbortSignal): Promise<Season>;
  listEvents(seasonId: string, signal: AbortSignal): Promise<Event[]>;
  getCurrentEvent(seasonId: string, signal: AbortSignal): Promise<Event>;
  getEvent(
    seasonId: string,
    eventId: number,
    signal: AbortSignal,
  ): Promise<Event>;
  listPhases(seasonId: string, signal: AbortSignal): Promise<Phase[]>;
  listTeams(seasonId: string, signal: AbortSignal): Promise<Team[]>;
  getTeam(
    seasonId: string,
    teamId: number,
    signal: AbortSignal,
  ): Promise<Team>;
  listElementTypes(
    seasonId: string,
    signal: AbortSignal,
  ): Promise<ElementType[]>;
  listElements(seasonId: string, signal: AbortSignal): Promise<Element[]>;
  getElement(
    seasonId: string,
    elementId: number,
    signal: AbortSignal,
  ): Promise<Element>;
  listFixtures(seasonId: string, signal: AbortSignal): Promise<Fixture[]>;
  listEventFixtures(
    seasonId: string,
    eventId: number,
    signal: AbortSignal,
  ): Promise<Fixture[]>;
  getEventStatus(seasonId: string, signal: AbortSignal): Promise<EventStatus>;
  listLiveElements(
    seasonId: string,
    eventId: number,
    signal: AbortSignal,
  ): Promise<LiveElement[]>;
  getLiveElement(
    seasonId: string,
    eventId: number,
    elementId: number,
    signal: AbortSignal,
  ): Promise<LiveElement>;
  listChangeEvents(
    afterId: number,
    limit: number,
    signal: AbortSignal,
  ): Promise<ChangeEvents>;
  listRecentChangeEvents(
    limit: number,
    signal: AbortSignal,
  ): Promise<ChangeEventHistory>;
  listChangeEventHistory(
    beforeId: number,
    limit: number,
    signal: AbortSignal,
  ): Promise<ChangeEventHistory>;
  listEntityChanges(
    changeEventId: number,
    afterId: number,
    limit: number,
    signal: AbortSignal,
  ): Promise<EntityChanges>;
  getIngestionStatus(signal: AbortSignal): Promise<IngestionStatus>;
  listCommunityStrategies(signal: AbortSignal): Promise<CommunityStrategy[]>;
  getLatestCommunityReport(
    strategyKey: string,
    signal: AbortSignal,
  ): Promise<CommunityReport>;
  getCommunityReport(
    reportId: number,
    signal: AbortSignal,
  ): Promise<CommunityReport>;
  listRecentCommunityReports(
    strategyKey: string,
    limit: number,
    signal: AbortSignal,
  ): Promise<CommunityReportHistory>;
  listCommunityReportHistory(
    strategyKey: string,
    beforeId: number,
    limit: number,
    signal: AbortSignal,
  ): Promise<CommunityReportHistory>;
}

interface ApiResult<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

function backendDetail(error: unknown, status: number): string {
  if (
    typeof error === "object" &&
    error !== null &&
    "detail" in error &&
    typeof error.detail === "string"
  ) {
    return error.detail;
  }
  return `Relay request failed with HTTP ${status}.`;
}

function unwrap<T>(result: ApiResult<T>, path: string): T {
  if (result.data !== undefined) return result.data;
  throw new RelayApiError({
    status: result.response.status,
    detail: backendDetail(result.error, result.response.status),
    path,
  });
}

async function transport<T>({
  path,
  request,
}: {
  path: string;
  request: () => Promise<ApiResult<T>>;
}): Promise<T> {
  try {
    return unwrap(await request(), path);
  } catch (error) {
    if (error instanceof RelayApiError || error instanceof DOMException) {
      throw error;
    }
    throw new RelayApiError({
      status: 0,
      detail: error instanceof Error ? error.message : "Relay request failed.",
      path,
    });
  }
}

async function requestJson<T>({
  baseUrl,
  path,
  signal,
  fetchImplementation,
}: {
  baseUrl: string;
  path: string;
  signal: AbortSignal;
  fetchImplementation: typeof fetch;
}): Promise<T> {
  let response: Response;
  try {
    response = await fetchImplementation(`${baseUrl}${path}`, { signal });
  } catch (error) {
    if (error instanceof DOMException) throw error;
    throw new RelayApiError({
      status: 0,
      detail: error instanceof Error ? error.message : "Relay request failed.",
      path,
    });
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = undefined;
  }
  if (!response.ok) {
    const record =
      typeof body === "object" && body !== null
        ? (body as Record<string, unknown>)
        : {};
    throw new RelayApiError({
      status: response.status,
      detail: backendDetail(body, response.status),
      path,
      code: typeof record.code === "string" ? record.code : undefined,
      retryAfterSeconds:
        typeof record.retry_after_seconds === "number"
          ? record.retry_after_seconds
          : undefined,
    });
  }
  return body as T;
}

async function readAllPages<Item>({
  baseUrl,
  path,
  signal,
  fetchImplementation,
}: {
  baseUrl: string;
  path: string;
  signal: AbortSignal;
  fetchImplementation: typeof fetch;
}): Promise<Item[]> {
  const items: Item[] = [];
  let afterId = 0;
  while (true) {
    const separator = path.includes("?") ? "&" : "?";
    const page = await requestJson<{
      items: Item[];
      next_after_id: number | null;
    }>({
      baseUrl,
      path:
        `${path}${separator}after_id=${afterId}` +
        `&limit=${PAGE_SIZE}`,
      signal,
      fetchImplementation,
    });
    items.push(...page.items);
    if (page.next_after_id === null) return items;
    if (page.next_after_id <= afterId) {
      throw new Error("Relay cursor page did not advance.");
    }
    afterId = page.next_after_id;
  }
}

export function createRelayApi({
  baseUrl,
  fetchImplementation,
}: {
  baseUrl: string;
  fetchImplementation: typeof fetch;
}): RelayApi {
  if (baseUrl.trim() === "") {
    throw new Error("Relay API base URL must not be empty.");
  }
  const client = createClient<paths>({
    baseUrl,
    fetch: fetchImplementation,
  });
  return {
    getHealth: (signal) =>
      transport({
        path: "/healthz",
        request: () => client.GET("/healthz", { signal }),
      }),
    getReadiness: (signal) =>
      requestJson({
        baseUrl,
        path: "/readyz",
        signal,
        fetchImplementation,
      }),
    listSeasons: (signal) =>
      transport({
        path: "/v1/seasons",
        request: () => client.GET("/v1/seasons", { signal }),
      }),
    getCurrentSeason: (signal) =>
      transport({
        path: "/v1/seasons/current",
        request: () => client.GET("/v1/seasons/current", { signal }),
      }),
    getSeason: (seasonId, signal) =>
      transport({
        path: `/v1/seasons/${seasonId}`,
        request: () =>
          client.GET("/v1/seasons/{season_id}", {
            params: { path: { season_id: seasonId } },
            signal,
          }),
      }),
    listEvents: (seasonId, signal) =>
      transport({
        path: `/v1/seasons/${seasonId}/events`,
        request: () =>
          client.GET("/v1/seasons/{season_id}/events", {
            params: { path: { season_id: seasonId } },
            signal,
          }),
      }),
    getCurrentEvent: (seasonId, signal) =>
      transport({
        path: `/v1/seasons/${seasonId}/events/current`,
        request: () =>
          client.GET("/v1/seasons/{season_id}/events/current", {
            params: { path: { season_id: seasonId } },
            signal,
          }),
      }),
    getEvent: (seasonId, eventId, signal) =>
      transport({
        path: `/v1/seasons/${seasonId}/events/${eventId}`,
        request: () =>
          client.GET("/v1/seasons/{season_id}/events/{event_id}", {
            params: { path: { season_id: seasonId, event_id: eventId } },
            signal,
          }),
      }),
    listPhases: (seasonId, signal) =>
      transport({
        path: `/v1/seasons/${seasonId}/phases`,
        request: () =>
          client.GET("/v1/seasons/{season_id}/phases", {
            params: { path: { season_id: seasonId } },
            signal,
          }),
      }),
    listTeams: (seasonId, signal) =>
      transport({
        path: `/v1/seasons/${seasonId}/teams`,
        request: () =>
          client.GET("/v1/seasons/{season_id}/teams", {
            params: { path: { season_id: seasonId } },
            signal,
          }),
      }),
    getTeam: (seasonId, teamId, signal) =>
      transport({
        path: `/v1/seasons/${seasonId}/teams/${teamId}`,
        request: () =>
          client.GET("/v1/seasons/{season_id}/teams/{team_id}", {
            params: { path: { season_id: seasonId, team_id: teamId } },
            signal,
          }),
      }),
    listElementTypes: (seasonId, signal) =>
      transport({
        path: `/v1/seasons/${seasonId}/element-types`,
        request: () =>
          client.GET("/v1/seasons/{season_id}/element-types", {
            params: { path: { season_id: seasonId } },
            signal,
          }),
      }),
    listElements: (seasonId, signal) =>
      readAllPages({
        baseUrl,
        path: `/v1/seasons/${seasonId}/elements`,
        signal,
        fetchImplementation,
      }),
    getElement: (seasonId, elementId, signal) =>
      transport({
        path: `/v1/seasons/${seasonId}/elements/${elementId}`,
        request: () =>
          client.GET("/v1/seasons/{season_id}/elements/{element_id}", {
            params: {
              path: { season_id: seasonId, element_id: elementId },
            },
            signal,
          }),
      }),
    listFixtures: (seasonId, signal) =>
      readAllPages({
        baseUrl,
        path: `/v1/seasons/${seasonId}/fixtures`,
        signal,
        fetchImplementation,
      }),
    listEventFixtures: (seasonId, eventId, signal) =>
      readAllPages({
        baseUrl,
        path: `/v1/seasons/${seasonId}/events/${eventId}/fixtures`,
        signal,
        fetchImplementation,
      }),
    getEventStatus: (seasonId, signal) =>
      transport({
        path: `/v1/seasons/${seasonId}/event-status`,
        request: () =>
          client.GET("/v1/seasons/{season_id}/event-status", {
            params: { path: { season_id: seasonId } },
            signal,
          }),
      }),
    listLiveElements: (seasonId, eventId, signal) =>
      readAllPages({
        baseUrl,
        path:
          `/v1/seasons/${seasonId}/events/${eventId}` +
          "/live-elements",
        signal,
        fetchImplementation,
      }),
    getLiveElement: (seasonId, eventId, elementId, signal) =>
      transport({
        path:
          `/v1/seasons/${seasonId}/events/${eventId}` +
          `/live-elements/${elementId}`,
        request: () =>
          client.GET(
            "/v1/seasons/{season_id}/events/{event_id}/live-elements/{element_id}",
            {
              params: {
                path: {
                  season_id: seasonId,
                  event_id: eventId,
                  element_id: elementId,
                },
              },
              signal,
            },
          ),
      }),
    listChangeEvents: (afterId, limit, signal) =>
      requestJson({
        baseUrl,
        path: `/v1/change-events?after_id=${afterId}&limit=${limit}`,
        signal,
        fetchImplementation,
      }),
    listRecentChangeEvents: (limit, signal) =>
      requestJson({
        baseUrl,
        path: `/v1/change-events/recent?limit=${limit}`,
        signal,
        fetchImplementation,
      }),
    listChangeEventHistory: (beforeId, limit, signal) =>
      requestJson({
        baseUrl,
        path:
          `/v1/change-events/history?before_id=${beforeId}` +
          `&limit=${limit}`,
        signal,
        fetchImplementation,
      }),
    listEntityChanges: (changeEventId, afterId, limit, signal) =>
      requestJson({
        baseUrl,
        path:
          `/v1/change-events/${changeEventId}/entity-changes` +
          `?after_id=${afterId}&limit=${limit}`,
        signal,
        fetchImplementation,
      }),
    getIngestionStatus: (signal) =>
      requestJson({
        baseUrl,
        path: "/v1/ingestion-status",
        signal,
        fetchImplementation,
      }),
    listCommunityStrategies: (signal) =>
      requestJson({
        baseUrl,
        path: "/v1/community-strategies",
        signal,
        fetchImplementation,
      }),
    getLatestCommunityReport: (strategyKey, signal) =>
      requestJson({
        baseUrl,
        path:
          "/v1/community-reports/latest?strategy_key=" +
          encodeURIComponent(strategyKey),
        signal,
        fetchImplementation,
      }),
    getCommunityReport: (reportId, signal) =>
      requestJson({
        baseUrl,
        path: `/v1/community-reports/${reportId}`,
        signal,
        fetchImplementation,
      }),
    listRecentCommunityReports: (strategyKey, limit, signal) =>
      requestJson({
        baseUrl,
        path:
          "/v1/community-reports/recent?strategy_key=" +
          `${encodeURIComponent(strategyKey)}&limit=${limit}`,
        signal,
        fetchImplementation,
      }),
    listCommunityReportHistory: (
      strategyKey,
      beforeId,
      limit,
      signal,
    ) =>
      requestJson({
        baseUrl,
        path:
          "/v1/community-reports/history?strategy_key=" +
          `${encodeURIComponent(strategyKey)}&before_id=${beforeId}` +
          `&limit=${limit}`,
        signal,
        fetchImplementation,
      }),
  };
}
