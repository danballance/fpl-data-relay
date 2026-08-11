import type { RelayApi } from "../api/relay-api";
import type {
  ChangeEvent,
  Element,
  ElementType,
  Event,
  EventStatus,
  Fixture,
  Health,
  LiveElement,
  EntityChange,
  IngestionStatus,
  Season,
  Team,
} from "../api/types";

export const health: Health = { status: "ok", schema_version: 2 };
export const season: Season = {
  id: "2025-26",
  start_year: 2025,
  end_year: 2026,
  first_deadline_time: "2025-08-15T18:30:00Z",
  last_deadline_time: "2026-05-24T15:00:00Z",
  is_current: true,
};
export const event: Event = {
  id: 1,
  name: "Gameweek 1",
  deadline_time: "2025-08-15T18:30:00Z",
  average_entry_score: 55,
  highest_score: 121,
  finished: true,
  data_checked: true,
  is_previous: false,
  is_current: true,
  is_next: false,
};
export const team: Team = {
  id: 1,
  name: "Northbridge FC",
  short_name: "NOR",
  strength: 3,
  strength_attack_home: 1180,
  strength_attack_away: 1150,
  strength_defence_home: 1170,
  strength_defence_away: 1120,
};
export const elementType: ElementType = {
  id: 3,
  singular_name: "Midfielder",
  singular_name_short: "MID",
  element_count: 1,
  squad_select: 5,
  squad_min_play: 2,
  squad_max_play: 5,
};
export const element: Element = {
  id: 10,
  first_name: "Ada",
  second_name: "Striker",
  web_name: "Ada",
  team: 1,
  element_type: 3,
  now_cost: 75,
  status: "a",
  form: "6.3",
  total_points: 18,
  selected_by_percent: "12.5",
};
export const fixture: Fixture = {
  id: 20,
  event: 1,
  finished: true,
  kickoff_time: "2025-08-16T14:00:00Z",
  started: true,
  team_h: 1,
  team_h_score: 2,
  team_a: 2,
  team_a_score: 1,
};
export const status: EventStatus = {
  leagues: "Updated",
  status: [
    {
      event: 1,
      bonus_added: true,
      date: "2025-08-16",
      leagues_updated: true,
    },
  ],
};
export const liveElement: LiveElement = {
  id: 10,
  stats: {
    total_points: 9,
    minutes: 90,
    goals_scored: 1,
    assists: 1,
    clean_sheets: 0,
    bonus: 2,
    bps: 31,
    in_dreamteam: true,
  },
  explain: [],
};
export const changeEvent: ChangeEvent = {
  id: 1,
  season_id: "2025-26",
  entity_family: "elements",
  event_name: "elements.updated",
  source_key: "bootstrap-static",
  source_event_id: null,
  payload_hash: "a".repeat(64),
  created_count: 0,
  updated_count: 1,
  deleted_count: 0,
  fetched_at: "2025-08-16T15:00:00Z",
  created_at: "2025-08-16T15:00:01Z",
};
export const entityChange: EntityChange = {
  id: 1,
  change_event_id: 1,
  entity_key: "10",
  entity_label: "Ada (10)",
  kind: "updated",
  fields: [
    {
      field: "now_cost",
      before: { present: true, value: 75 },
      after: { present: true, value: 76 },
    },
    {
      field: "news",
      before: { present: false, value: null },
      after: { present: true, value: null },
    },
    {
      field: "availability_context",
      before: { present: true, value: { source: "team update" } },
      after: { present: true, value: { source: "medical update" } },
    },
  ],
  created_at: "2025-08-16T15:00:01Z",
};
export const ingestionStatus: IngestionStatus = {
  season_id: "2025-26",
  checked_at: "2025-08-16T15:00:02Z",
  reference: {
    state: "healthy",
    expected_interval_seconds: 900,
    stale_after_seconds: 1800,
    last_checked_at: "2025-08-16T15:00:00Z",
    last_changed_at: "2025-08-16T14:45:00Z",
    current_window_end: null,
    next_window_start: null,
  },
  live: {
    state: "idle",
    expected_interval_seconds: 60,
    stale_after_seconds: 120,
    last_checked_at: "2025-08-16T14:00:00Z",
    last_changed_at: "2025-08-16T14:00:00Z",
    current_window_end: null,
    next_window_start: "2025-08-17T13:50:00Z",
  },
};

export function makeFakeRelayApi(
  overrides: Partial<RelayApi> = {},
): RelayApi {
  const api: RelayApi = {
    getHealth: async () => health,
    getReadiness: async () => ({ status: "ready", schema_version: 2 }),
    listSeasons: async () => [season],
    getCurrentSeason: async () => season,
    getSeason: async () => season,
    listEvents: async () => [event],
    getCurrentEvent: async () => event,
    getEvent: async () => event,
    listPhases: async () => [
      { id: 1, name: "Overall", start_event: 1, stop_event: 38 },
    ],
    listTeams: async () => [
      team,
      { id: 2, name: "Southbank FC", short_name: "SOU" },
    ],
    getTeam: async () => team,
    listElementTypes: async () => [elementType],
    listElements: async () => [element],
    getElement: async () => element,
    listFixtures: async () => [fixture],
    listEventFixtures: async () => [fixture],
    getEventStatus: async () => status,
    listLiveElements: async () => [liveElement],
    getLiveElement: async () => liveElement,
    listChangeEvents: async () => ({
      items: [changeEvent],
      next_after_id: null,
    }),
    listRecentChangeEvents: async () => ({
      items: [changeEvent],
      next_before_id: null,
    }),
    listChangeEventHistory: async () => ({
      items: [],
      next_before_id: null,
    }),
    listEntityChanges: async () => ({
      items: [entityChange],
      next_after_id: null,
    }),
    getIngestionStatus: async () => ingestionStatus,
  };
  return { ...api, ...overrides };
}
