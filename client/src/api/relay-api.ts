import createClient from "openapi-fetch";

import type { paths } from "./generated";
import { RelayApiError } from "./errors";
import { consumeSse } from "./sse";
import type {
  ChangeEvent,
  ChangeEvents,
  Element,
  ElementType,
  Event,
  EventStatus,
  Fixture,
  Health,
  LiveElement,
  Phase,
  Season,
  Team,
} from "./types";

export interface RelayApi {
  getHealth(signal: AbortSignal): Promise<Health>;
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
  watchChangeEvents({
    afterId,
    signal,
    onEvent,
  }: {
    afterId: number;
    signal: AbortSignal;
    onEvent: (event: ChangeEvent) => void;
  }): Promise<void>;
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
  if (result.data !== undefined) {
    return result.data;
  }
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
            params: {
              path: { season_id: seasonId, event_id: eventId },
            },
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
            params: {
              path: { season_id: seasonId, team_id: teamId },
            },
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
      transport({
        path: `/v1/seasons/${seasonId}/elements`,
        request: () =>
          client.GET("/v1/seasons/{season_id}/elements", {
            params: { path: { season_id: seasonId } },
            signal,
          }),
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
      transport({
        path: `/v1/seasons/${seasonId}/fixtures`,
        request: () =>
          client.GET("/v1/seasons/{season_id}/fixtures", {
            params: { path: { season_id: seasonId } },
            signal,
          }),
      }),
    listEventFixtures: (seasonId, eventId, signal) =>
      transport({
        path: `/v1/seasons/${seasonId}/events/${eventId}/fixtures`,
        request: () =>
          client.GET("/v1/seasons/{season_id}/events/{event_id}/fixtures", {
            params: {
              path: { season_id: seasonId, event_id: eventId },
            },
            signal,
          }),
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
      transport({
        path: `/v1/seasons/${seasonId}/events/${eventId}/live-elements`,
        request: () =>
          client.GET(
            "/v1/seasons/{season_id}/events/{event_id}/live-elements",
            {
              params: {
                path: { season_id: seasonId, event_id: eventId },
              },
              signal,
            },
          ),
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
      transport({
        path: `/v1/change-events?after_id=${afterId}&limit=${limit}`,
        request: () =>
          client.GET("/v1/change-events", {
            params: { query: { after_id: afterId, limit } },
            signal,
          }),
      }),
    watchChangeEvents: async ({ afterId, signal, onEvent }) => {
      const path = "/v1/stream";
      let response: Response;
      try {
        response = await fetchImplementation(`${baseUrl}${path}`, {
          headers: {
            Accept: "text/event-stream",
            "Last-Event-ID": String(afterId),
          },
          signal,
        });
      } catch (error) {
        if (error instanceof DOMException) {
          throw error;
        }
        throw new RelayApiError({
          status: 0,
          detail:
            error instanceof Error
              ? error.message
              : "Could not connect to the relay stream.",
          path,
        });
      }
      if (!response.ok) {
        let error: unknown;
        try {
          error = await response.json();
        } catch {
          error = undefined;
        }
        throw new RelayApiError({
          status: response.status,
          detail: backendDetail(error, response.status),
          path,
        });
      }
      if (response.body === null) {
        throw new RelayApiError({
          status: response.status,
          detail: "The relay stream returned no response body.",
          path,
        });
      }
      await consumeSse({ body: response.body, onEvent });
      if (!signal.aborted) {
        throw new RelayApiError({
          status: response.status,
          detail: "The relay change stream disconnected.",
          path,
        });
      }
    },
  };
}
