import { describe, expect, it } from "vitest";

import {
  element,
  elementType,
  event,
  team,
} from "../test/fakeRelayApi";
import {
  compactHash,
  createLookups,
  formatBoolean,
  formatCost,
  formatDateTime,
  playerName,
} from "./format";

describe("relay formatting", () => {
  it("formats scalar values without inventing missing data", () => {
    expect(formatBoolean(true)).toBe("Yes");
    expect(formatBoolean(false)).toBe("No");
    expect(formatBoolean(null)).toBe("—");
    expect(formatCost(75)).toBe("£7.5m");
    expect(formatCost(undefined)).toBe("—");
    expect(formatDateTime(null)).toBe("—");
    expect(formatDateTime("2025-08-15T18:30:00Z")).toMatch("15 Aug 2025");
    expect(playerName(element)).toBe("Ada Striker");
    expect(compactHash("short")).toBe("short");
    expect(compactHash("a".repeat(64))).toBe(`${"a".repeat(12)}…`);
  });

  it("resolves stored identifiers and labels unknown identifiers explicitly", () => {
    const lookups = createLookups({
      teams: [team],
      elementTypes: [elementType],
      elements: [element],
      events: [event],
    });
    expect(lookups.team(1)).toBe("Northbridge FC");
    expect(lookups.team(99)).toBe("Team 99");
    expect(lookups.elementType(3)).toBe("Midfielder");
    expect(lookups.elementType(4)).toBe("Type 4");
    expect(lookups.element(10)).toBe("Ada Striker");
    expect(lookups.element(11)).toBe("Player 11");
    expect(lookups.event(1)).toBe("Gameweek 1");
    expect(lookups.event(2)).toBe("Event 2");
    expect(lookups.event(null)).toBe("Unscheduled");
  });
});
