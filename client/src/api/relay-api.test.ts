import { describe, expect, it, vi } from "vitest";

import {
  changeEvent,
  element,
  entityChange,
  elementType,
  event,
  fixture,
  health,
  ingestionStatus,
  liveElement,
  season,
  status,
  team,
} from "../test/fakeRelayApi";
import { RelayApiError } from "./errors";
import { createRelayApi } from "./relay-api";

function jsonResponse(value: unknown, statusCode = 200): Response {
  return new Response(JSON.stringify(value), {
    status: statusCode,
    headers: { "content-type": "application/json" },
  });
}

describe("relay API adapter", () => {
  it("maps every finite endpoint to the relay base URL", async () => {
    const requested: string[] = [];
    const fetchImplementation = vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      requested.push(url);
      if (url.endsWith("/healthz")) return jsonResponse(health);
      if (url.endsWith("/readyz")) {
        return jsonResponse({ status: "ready", schema_version: 1 });
      }
      if (url.endsWith("/v1/seasons")) return jsonResponse([season]);
      if (url.endsWith("/v1/seasons/current")) return jsonResponse(season);
      if (url.includes("/element-types")) return jsonResponse([elementType]);
      if (url.match(/\/elements\/10$/)) return jsonResponse(element);
      if (url.includes("/elements?after_id=")) {
        return jsonResponse({ items: [element], next_after_id: null });
      }
      if (url.match(/\/teams\/1$/)) return jsonResponse(team);
      if (url.endsWith("/teams")) return jsonResponse([team]);
      if (url.includes("/live-elements/10")) return jsonResponse(liveElement);
      if (url.includes("/live-elements?after_id=")) {
        return jsonResponse({ items: [liveElement], next_after_id: null });
      }
      if (url.endsWith("/event-status")) return jsonResponse(status);
      if (url.includes("/fixtures?after_id=")) {
        return jsonResponse({ items: [fixture], next_after_id: null });
      }
      if (url.match(/\/events\/1$/)) return jsonResponse(event);
      if (url.endsWith("/events/current")) return jsonResponse(event);
      if (url.endsWith("/events")) return jsonResponse([event]);
      if (url.endsWith("/phases")) return jsonResponse([]);
      if (url.startsWith("http://relay/v1/change-events/recent")) {
        return jsonResponse({ items: [changeEvent], next_before_id: null });
      }
      if (url.startsWith("http://relay/v1/change-events/history")) {
        return jsonResponse({ items: [changeEvent], next_before_id: null });
      }
      if (url.includes("/v1/change-events/1/entity-changes")) {
        return jsonResponse({ items: [entityChange], next_after_id: null });
      }
      if (url.endsWith("/v1/ingestion-status")) {
        return jsonResponse(ingestionStatus);
      }
      if (url.startsWith("http://relay/v1/change-events?")) {
        return jsonResponse({ items: [changeEvent], next_after_id: null });
      }
      if (url.endsWith("/v1/seasons/2025-26")) return jsonResponse(season);
      throw new Error(`Unexpected URL ${url}`);
    });
    const api = createRelayApi({
      baseUrl: "http://relay",
      fetchImplementation,
    });
    const signal = new AbortController().signal;

    expect(await api.getHealth(signal)).toEqual(health);
    expect(await api.getReadiness(signal)).toEqual({
      status: "ready",
      schema_version: 1,
    });
    expect(await api.listSeasons(signal)).toEqual([season]);
    expect(await api.getCurrentSeason(signal)).toEqual(season);
    expect(await api.getSeason(season.id, signal)).toEqual(season);
    expect(await api.listEvents(season.id, signal)).toEqual([event]);
    expect(await api.getCurrentEvent(season.id, signal)).toEqual(event);
    expect(await api.getEvent(season.id, 1, signal)).toEqual(event);
    expect(await api.listPhases(season.id, signal)).toEqual([]);
    expect(await api.listTeams(season.id, signal)).toEqual([team]);
    expect(await api.getTeam(season.id, 1, signal)).toEqual(team);
    expect(await api.listElementTypes(season.id, signal)).toEqual([
      elementType,
    ]);
    expect(await api.listElements(season.id, signal)).toEqual([element]);
    expect(await api.getElement(season.id, 10, signal)).toEqual(element);
    expect(await api.listFixtures(season.id, signal)).toEqual([fixture]);
    expect(await api.listEventFixtures(season.id, 1, signal)).toEqual([
      fixture,
    ]);
    expect(await api.getEventStatus(season.id, signal)).toEqual(status);
    expect(await api.listLiveElements(season.id, 1, signal)).toEqual([
      liveElement,
    ]);
    expect(await api.getLiveElement(season.id, 1, 10, signal)).toEqual(
      liveElement,
    );
    expect(await api.listChangeEvents(0, 100, signal)).toEqual({
      items: [changeEvent],
      next_after_id: null,
    });
    expect(await api.listRecentChangeEvents(100, signal)).toEqual({
      items: [changeEvent],
      next_before_id: null,
    });
    expect(await api.listChangeEventHistory(1, 100, signal)).toEqual({
      items: [changeEvent],
      next_before_id: null,
    });
    expect(await api.listEntityChanges(1, 0, 100, signal)).toEqual({
      items: [entityChange],
      next_after_id: null,
    });
    expect(await api.getIngestionStatus(signal)).toEqual(ingestionStatus);
    expect(requested).toContain(
      "http://relay/v1/change-events?after_id=0&limit=100",
    );
    expect(requested).toContain(
      "http://relay/v1/change-events/recent?limit=100",
    );
    expect(requested).toContain(
      "http://relay/v1/change-events/history?before_id=1&limit=100",
    );
    expect(requested).toContain(
      "http://relay/v1/change-events/1/entity-changes?after_id=0&limit=100",
    );
    expect(requested).toContain("http://relay/v1/ingestion-status");
  });

  it("rejects an empty base URL", () => {
    expect(() =>
      createRelayApi({
        baseUrl: " ",
        fetchImplementation: vi.fn(),
      }),
    ).toThrow("must not be empty");
  });

  it("preserves backend details and supplies generic HTTP details", async () => {
    const detailed = createRelayApi({
      baseUrl: "http://relay",
      fetchImplementation: vi.fn(async () =>
        jsonResponse({ detail: "FPL event status data has not been ingested." }, 503),
      ),
    });
    await expect(
      detailed.getEventStatus(season.id, new AbortController().signal),
    ).rejects.toEqual(
      new RelayApiError({
        status: 503,
        detail: "FPL event status data has not been ingested.",
        path: `/v1/seasons/${season.id}/event-status`,
      }),
    );

    const generic = createRelayApi({
      baseUrl: "http://relay",
      fetchImplementation: vi.fn(async () => jsonResponse({}, 500)),
    });
    await expect(
      generic.getHealth(new AbortController().signal),
    ).rejects.toMatchObject({
      status: 500,
      detail: "Relay request failed with HTTP 500.",
    });
  });

  it("wraps network failures but preserves abort errors", async () => {
    const failed = createRelayApi({
      baseUrl: "http://relay",
      fetchImplementation: vi.fn(async () => {
        throw new Error("connection refused");
      }),
    });
    await expect(
      failed.getHealth(new AbortController().signal),
    ).rejects.toMatchObject({
      status: 0,
      detail: "connection refused",
      path: "/healthz",
    });

    const abort = new DOMException("stopped", "AbortError");
    const aborted = createRelayApi({
      baseUrl: "http://relay",
      fetchImplementation: vi.fn(async () => {
        throw abort;
      }),
    });
    await expect(
      aborted.getHealth(new AbortController().signal),
    ).rejects.toBe(abort);
  });

  it("preserves waking error codes for the readiness gate", async () => {
    const api = createRelayApi({
      baseUrl: "/api",
      fetchImplementation: vi.fn(async () =>
        jsonResponse(
          {
            code: "database_waking",
            detail: "The database is waking from idle. Retry shortly.",
            retry_after_seconds: 5,
          },
          503,
        ),
      ),
    });
    await expect(
      api.getReadiness(new AbortController().signal),
    ).rejects.toMatchObject({
      status: 503,
      code: "database_waking",
      retryAfterSeconds: 5,
    });
  });

  it("follows paginated collection cursors and rejects stalled cursors", async () => {
    const fetchImplementation = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ items: [element], next_after_id: 10 }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          items: [{ ...element, id: 11 }],
          next_after_id: null,
        }),
      );
    const api = createRelayApi({ baseUrl: "/api", fetchImplementation });
    await expect(
      api.listElements(season.id, new AbortController().signal),
    ).resolves.toHaveLength(2);
    expect(fetchImplementation).toHaveBeenLastCalledWith(
      expect.stringContaining("after_id=10"),
      expect.any(Object),
    );

    const stalled = createRelayApi({
      baseUrl: "/api",
      fetchImplementation: vi.fn(async () =>
        jsonResponse({ items: [element], next_after_id: 0 }),
      ),
    });
    await expect(
      stalled.listElements(season.id, new AbortController().signal),
    ).rejects.toThrow("did not advance");
  });

  it("reports non-JSON HTTP responses from raw endpoints", async () => {
    const api = createRelayApi({
      baseUrl: "/api",
      fetchImplementation: vi.fn(async () =>
        new Response("not json", { status: 500 }),
      ),
    });
    await expect(
      api.getReadiness(new AbortController().signal),
    ).rejects.toMatchObject({
      status: 500,
      detail: "Relay request failed with HTTP 500.",
    });
  });
});
