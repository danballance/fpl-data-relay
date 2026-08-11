import { describe, expect, it, vi } from "vitest";

import type { RelayApi } from "../../api/relay-api";
import {
  changeEvent,
  entityChange,
  makeFakeRelayApi,
} from "../../test/fakeRelayApi";
import {
  CHANGE_PAGE_SIZE,
  mergeChangeEvents,
  readChangesAfter,
  readEntityChanges,
} from "./ActivityPage";

describe("change-event activity", () => {
  it("catches up with forward cursor polling", async () => {
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
    const result = await readChangesAfter(
      api,
      0,
      new AbortController().signal,
    );
    expect(result).toHaveLength(CHANGE_PAGE_SIZE + 1);
    expect(listChangeEvents).toHaveBeenLastCalledWith(
      CHANGE_PAGE_SIZE,
      CHANGE_PAGE_SIZE,
      expect.any(AbortSignal),
    );
  });

  it("rejects a polling cursor that does not advance", async () => {
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
      readChangesAfter(api, 0, new AbortController().signal),
    ).rejects.toThrow("did not advance");
  });

  it("deduplicates and orders history newest first", () => {
    expect(
      mergeChangeEvents(
        [{ ...changeEvent, id: 2 }],
        [
          { ...changeEvent, id: 2, event_name: "newer" },
          { ...changeEvent, id: 1 },
        ],
      ),
    ).toEqual([
      { ...changeEvent, id: 2, event_name: "newer" },
      { ...changeEvent, id: 1 },
    ]);
  });

  it("loads bounded entity detail pages", async () => {
    const listEntityChanges = vi
      .fn<RelayApi["listEntityChanges"]>()
      .mockResolvedValueOnce({
        items: [entityChange],
        next_after_id: 1,
      })
      .mockResolvedValueOnce({
        items: [{ ...entityChange, id: 2, entity_key: "11" }],
        next_after_id: null,
      });
    const api = makeFakeRelayApi({ listEntityChanges });

    await expect(
      readEntityChanges(api, changeEvent.id, new AbortController().signal),
    ).resolves.toHaveLength(2);
    expect(listEntityChanges).toHaveBeenLastCalledWith(
      changeEvent.id,
      1,
      CHANGE_PAGE_SIZE,
      expect.any(AbortSignal),
    );
  });
});
