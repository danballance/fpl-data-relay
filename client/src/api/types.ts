import type { components } from "./generated";

export type Health = components["schemas"]["HealthResponse"];
export interface Readiness {
  status: "ready";
  schema_version: number;
}
export type Season = components["schemas"]["Season"];
export type Event = components["schemas"]["Event"];
export type Phase = components["schemas"]["Phase"];
export type Team = components["schemas"]["Team"];
export type ElementType = components["schemas"]["ElementType"];
export type Element = components["schemas"]["Element"];
export type Fixture = components["schemas"]["Fixture"];
export type EventStatus = components["schemas"]["EventStatusResponse"];
export type EventStatusDay = components["schemas"]["EventStatusDay"];
export type LiveElement = components["schemas"]["LiveElement"];
export type ChangeEvent = components["schemas"]["ChangeEventResponse"];
export interface CursorPage<Item> {
  items: Item[];
  next_after_id: number | null;
}
export type ChangeEvents = CursorPage<ChangeEvent>;

export type RelayRecord =
  | Season
  | Event
  | Phase
  | Team
  | ElementType
  | Element
  | Fixture
  | EventStatusDay
  | LiveElement
  | ChangeEvent;
