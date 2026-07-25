import { describe, expect, it, vi } from "vitest";

import {
  changeEvent,
  element,
  elementType,
  event,
  fixture,
  health,
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
      if (url.endsWith("/v1/seasons")) return jsonResponse([season]);
      if (url.endsWith("/v1/seasons/current")) return jsonResponse(season);
      if (url.includes("/element-types")) return jsonResponse([elementType]);
      if (url.match(/\/elements\/10$/)) return jsonResponse(element);
      if (url.endsWith("/elements")) return jsonResponse([element]);
      if (url.match(/\/teams\/1$/)) return jsonResponse(team);
      if (url.endsWith("/teams")) return jsonResponse([team]);
      if (url.includes("/live-elements/10")) return jsonResponse(liveElement);
      if (url.endsWith("/live-elements")) return jsonResponse([liveElement]);
      if (url.endsWith("/event-status")) return jsonResponse(status);
      if (url.endsWith("/fixtures")) return jsonResponse([fixture]);
      if (url.match(/\/events\/1$/)) return jsonResponse(event);
      if (url.endsWith("/events/current")) return jsonResponse(event);
      if (url.endsWith("/events")) return jsonResponse([event]);
      if (url.endsWith("/phases")) return jsonResponse([]);
      if (url.startsWith("http://relay/v1/change-events")) {
        return jsonResponse({ events: [changeEvent] });
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
      events: [changeEvent],
    });
    expect(requested).toContain(
      "http://relay/v1/change-events?after_id=0&limit=100",
    );
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

  it("streams change events with a replay header and reports disconnects", async () => {
    const encoder = new TextEncoder();
    const onEvent = vi.fn();
    const fetchImplementation = vi.fn(async () => {
      return new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(
              encoder.encode(`data: ${JSON.stringify(changeEvent)}\n\n`),
            );
            controller.close();
          },
        }),
        { status: 200 },
      );
    });
    const api = createRelayApi({
      baseUrl: "/api",
      fetchImplementation,
    });
    await expect(
      api.watchChangeEvents({
        afterId: 42,
        signal: new AbortController().signal,
        onEvent,
      }),
    ).rejects.toMatchObject({ detail: "The relay change stream disconnected." });
    expect(onEvent).toHaveBeenCalledWith(changeEvent);
    expect(fetchImplementation).toHaveBeenCalledWith(
      "/api/v1/stream",
      expect.objectContaining({
        headers: {
          Accept: "text/event-stream",
          "Last-Event-ID": "42",
        },
      }),
    );
  });

  it("handles stream connection, HTTP, and body failures", async () => {
    const signal = new AbortController().signal;
    const network = createRelayApi({
      baseUrl: "/api",
      fetchImplementation: vi.fn(async () => {
        throw new Error("stream unavailable");
      }),
    });
    await expect(
      network.watchChangeEvents({ afterId: 0, signal, onEvent: vi.fn() }),
    ).rejects.toMatchObject({ status: 0, detail: "stream unavailable" });

    const http = createRelayApi({
      baseUrl: "/api",
      fetchImplementation: vi.fn(async () =>
        jsonResponse({ detail: "Bad stream id." }, 400),
      ),
    });
    await expect(
      http.watchChangeEvents({ afterId: 0, signal, onEvent: vi.fn() }),
    ).rejects.toMatchObject({ status: 400, detail: "Bad stream id." });

    const nonJson = createRelayApi({
      baseUrl: "/api",
      fetchImplementation: vi.fn(async () => new Response("bad", { status: 500 })),
    });
    await expect(
      nonJson.watchChangeEvents({ afterId: 0, signal, onEvent: vi.fn() }),
    ).rejects.toMatchObject({
      status: 500,
      detail: "Relay request failed with HTTP 500.",
    });

    const empty = createRelayApi({
      baseUrl: "/api",
      fetchImplementation: vi.fn(async () => new Response(null, { status: 200 })),
    });
    await expect(
      empty.watchChangeEvents({ afterId: 0, signal, onEvent: vi.fn() }),
    ).rejects.toMatchObject({ detail: "The relay stream returned no response body." });
  });
});
