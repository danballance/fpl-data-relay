import type {
  Element,
  ElementType,
  Event,
  EventStatusDay,
  Fixture,
  Team,
} from "../api/types";

export function formatDateTime(value: string | null | undefined): string {
  if (value === null || value === undefined) {
    return "—";
  }
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function formatBoolean(value: boolean | null | undefined): string {
  if (value === null || value === undefined) {
    return "—";
  }
  return value ? "Yes" : "No";
}

export function formatCost(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "—";
  }
  return `£${(value / 10).toFixed(1)}m`;
}

export function fixtureStatus(
  fixture: Pick<Fixture, "finished" | "finished_provisional" | "started">,
): "Scheduled" | "Live" | "Provisional" | "Confirmed" {
  if (fixture.finished) {
    return "Confirmed";
  }
  if (fixture.finished_provisional) {
    return "Provisional";
  }
  return fixture.started ? "Live" : "Scheduled";
}

export function eventPointsStatus(
  status: Pick<EventStatusDay, "points">,
): "Not started" | "Live" | "Provisional" | "Confirmed" {
  if (status.points === "r") {
    return "Confirmed";
  }
  if (status.points === "l") {
    return "Live";
  }
  if (status.points === "p") {
    return "Provisional";
  }
  return "Not started";
}

export function playerName(element: Element): string {
  return `${element.first_name} ${element.second_name}`.trim();
}

export interface RelayLookups {
  team: (id: number) => string;
  elementType: (id: number) => string;
  element: (id: number) => string;
  event: (id: number | null | undefined) => string;
}

export function createLookups({
  teams,
  elementTypes,
  elements,
  events,
}: {
  teams: Team[];
  elementTypes: ElementType[];
  elements: Element[];
  events: Event[];
}): RelayLookups {
  const teamNames = new Map(teams.map((team) => [team.id, team.name]));
  const typeNames = new Map(
    elementTypes.map((elementType) => [
      elementType.id,
      elementType.singular_name,
    ]),
  );
  const elementNames = new Map(
    elements.map((element) => [element.id, playerName(element)]),
  );
  const eventNames = new Map(events.map((event) => [event.id, event.name]));

  return {
    team: (id) => teamNames.get(id) ?? `Team ${id}`,
    elementType: (id) => typeNames.get(id) ?? `Type ${id}`,
    element: (id) => elementNames.get(id) ?? `Player ${id}`,
    event: (id) =>
      id === null || id === undefined
        ? "Unscheduled"
        : (eventNames.get(id) ?? `Event ${id}`),
  };
}

export function compactHash(value: string): string {
  return value.length <= 14 ? value : `${value.slice(0, 12)}…`;
}
