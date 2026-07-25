import { describe, expect, it, vi } from "vitest";

import type { RelayApi } from "../../api/relay-api";
import {
  changeEvent,
  makeFakeRelayApi,
} from "../../test/fakeRelayApi";
import {
  CHANGE_PAGE_SIZE,
  mergeChangeEvents,
  readChangeHistory,
} from "./ActivityPage";

describe("change-event history", () => {
  it("follows the cursor until the final partial page", async () => {
    const firstPage = Array.from({ length: CHANGE_PAGE_SIZE }, (_, index) => ({
      ...changeEvent,
      id: index + 1,
    }));
    const listChangeEvents = vi
      .fn<RelayApi["listChangeEvents"]>()
      .mockResolvedValueOnce({
        items: firstPage,
        next_after_id: CHANGE_PAGE_SIZE,
      })
      .mockResolvedValueOnce({
        items: [{ ...changeEvent, id: CHANGE_PAGE_SIZE + 1 }],
        next_after_id: null,
      });
    const api = makeFakeRelayApi({ listChangeEvents });
    const result = await readChangeHistory(
      api,
      new AbortController().signal,
    );
    expect(result).toHaveLength(CHANGE_PAGE_SIZE + 1);
    expect(listChangeEvents).toHaveBeenLastCalledWith(
      CHANGE_PAGE_SIZE,
      CHANGE_PAGE_SIZE,
      expect.any(AbortSignal),
    );
  });

  it("rejects a full page whose cursor does not advance", async () => {
    const repeated = Array.from({ length: CHANGE_PAGE_SIZE }, () => ({
      ...changeEvent,
      id: 0,
    }));
    const api = makeFakeRelayApi({
      listChangeEvents: async () => ({
        items: repeated,
        next_after_id: 0,
      }),
    });
    await expect(
      readChangeHistory(api, new AbortController().signal),
    ).rejects.toThrow("did not advance");
  });

  it("deduplicates and orders history plus live events", () => {
    expect(
      mergeChangeEvents(
        [{ ...changeEvent, id: 2 }],
        [
          { ...changeEvent, id: 2, event_name: "newer" },
          { ...changeEvent, id: 1 },
        ],
      ),
    ).toEqual([
      { ...changeEvent, id: 1 },
      { ...changeEvent, id: 2, event_name: "newer" },
    ]);
  });
});
