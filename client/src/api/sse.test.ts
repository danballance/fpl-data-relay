import { describe, expect, it, vi } from "vitest";

import { changeEvent } from "../test/fakeRelayApi";
import { consumeSse, isChangeEvent, parseSseFrame } from "./sse";

function body(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
      controller.close();
    },
  });
}

describe("SSE parsing", () => {
  it("parses data frames and ignores comments", () => {
    expect(parseSseFrame(": heartbeat")).toBeUndefined();
    expect(parseSseFrame('event: change\ndata: {"id": 7}')).toEqual({
      id: 7,
    });
    expect(parseSseFrame('data: {"name":\ndata: "relay"}')).toEqual({
      name: "relay",
    });
  });

  it("recognises only relay change-event payloads", () => {
    expect(isChangeEvent(changeEvent)).toBe(true);
    expect(isChangeEvent(null)).toBe(false);
    expect(isChangeEvent({ id: "1" })).toBe(false);
    expect(isChangeEvent({ ...changeEvent, payload_hash: 2 })).toBe(false);
  });

  it("consumes split frames, heartbeats, and a final unterminated frame", async () => {
    const onEvent = vi.fn();
    await consumeSse({
      body: body([
        ": heartbeat\n\n",
        `id: 1\ndata: ${JSON.stringify(changeEvent).slice(0, 40)}`,
        `${JSON.stringify(changeEvent).slice(40)}\n\n`,
        `data: ${JSON.stringify({ ...changeEvent, id: 2 })}`,
      ]),
      onEvent,
    });
    expect(onEvent).toHaveBeenCalledTimes(2);
    expect(onEvent).toHaveBeenLastCalledWith({ ...changeEvent, id: 2 });
  });

  it("fails fast for malformed stream JSON and invalid event shapes", async () => {
    await expect(
      consumeSse({ body: body(["data: not-json\n\n"]), onEvent: vi.fn() }),
    ).rejects.toThrow("Unexpected token");
    await expect(
      consumeSse({
        body: body(['data: {"id": 1}\n\n']),
        onEvent: vi.fn(),
      }),
    ).rejects.toThrow("invalid change event");
    await expect(
      consumeSse({
        body: body(['data: {"id": 1}']),
        onEvent: vi.fn(),
      }),
    ).rejects.toThrow("invalid change event");
  });
});
