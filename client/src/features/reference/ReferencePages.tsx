import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { useMemo } from "react";

import { useRelayApi } from "../../api/provider";
import type {
  Element,
  ElementType,
  Event,
  Fixture,
  Phase,
  Season,
  Team,
} from "../../api/types";
import { API_OPERATIONS } from "../../components/ApiDocsLink";
import { ResourcePage } from "../../components/ResourcePage";
import { SelectionRequired } from "../../components/SelectionRequired";
import {
  formatBoolean,
  formatCost,
  formatDateTime,
  fixtureStatus,
  playerName,
} from "../../lib/format";
import { useFilterParam } from "../../lib/query-state";
import { useSelection } from "../../app/selection";
import { useReferenceLookups } from "./useReferenceLookups";

export function SeasonsPage() {
  const api = useRelayApi();
  const query = useQuery({
    queryKey: ["seasons"],
    queryFn: ({ signal }) => api.listSeasons(signal),
    retry: false,
  });
  const columns = useMemo<ColumnDef<Season>[]>(
    () => [
      { accessorKey: "id", header: "Season" },
      { accessorKey: "start_year", header: "Starts" },
      { accessorKey: "end_year", header: "Ends" },
      {
        accessorKey: "first_deadline_time",
        header: "First deadline",
        cell: ({ getValue }) => formatDateTime(getValue<string>()),
      },
      {
        accessorKey: "last_deadline_time",
        header: "Last deadline",
        cell: ({ getValue }) => formatDateTime(getValue<string>()),
      },
      {
        accessorKey: "is_current",
        header: "Current",
        cell: ({ getValue }) => formatBoolean(getValue<boolean>()),
      },
    ],
    [],
  );
  return (
    <ResourcePage
      eyebrow="Relay"
      title="Seasons"
      description="Derived season boundaries stored by the relay."
      apiOperations={[API_OPERATIONS.listSeasons]}
      query={query}
      columns={columns}
      getRowId={(row) => row.id}
      getRowLabel={(row) => row.id}
      detailLoader={{
        apiOperation: API_OPERATIONS.getSeason,
        queryKey: ["season"],
        load: (id, signal) => api.getSeason(id, signal),
      }}
      emptyMessage="No seasons have been ingested."
    />
  );
}

export function EventsPage() {
  const api = useRelayApi();
  const { seasonId } = useSelection();
  const query = useQuery({
    queryKey: ["events", seasonId],
    queryFn: ({ signal }) => api.listEvents(seasonId!, signal),
    enabled: seasonId !== undefined,
    retry: false,
  });
  const columns = useMemo<ColumnDef<Event>[]>(
    () => [
      { accessorKey: "id", header: "ID" },
      { accessorKey: "name", header: "Event" },
      {
        accessorKey: "deadline_time",
        header: "Deadline",
        cell: ({ getValue }) => formatDateTime(getValue<string | null>()),
      },
      { accessorKey: "average_entry_score", header: "Average" },
      { accessorKey: "highest_score", header: "Highest" },
      {
        accessorKey: "finished",
        header: "Finished",
        cell: ({ getValue }) => formatBoolean(getValue<boolean | null>()),
      },
      {
        accessorKey: "data_checked",
        header: "Checked",
        cell: ({ getValue }) => formatBoolean(getValue<boolean | null>()),
      },
      {
        id: "state",
        header: "State",
        accessorFn: (row) =>
          row.is_current
            ? "Current"
            : row.is_previous
              ? "Previous"
              : row.is_next
                ? "Next"
                : "—",
      },
    ],
    [],
  );
  if (seasonId === undefined) {
    return <SelectionRequired kind="season">{null}</SelectionRequired>;
  }
  return (
    <ResourcePage
      eyebrow={seasonId}
      title="Events"
      description="Gameweek metadata stored for the selected season."
      apiOperations={[API_OPERATIONS.listEvents]}
      query={query}
      columns={columns}
      getRowId={(row) => String(row.id)}
      getRowLabel={(row) => row.name}
      detailLoader={{
        apiOperation: API_OPERATIONS.getEvent,
        queryKey: ["event", seasonId],
        load: (id, signal) => api.getEvent(seasonId, Number(id), signal),
      }}
      emptyMessage="No events are stored for this season."
    />
  );
}

export function PhasesPage() {
  const api = useRelayApi();
  const { seasonId } = useSelection();
  const query = useQuery({
    queryKey: ["phases", seasonId],
    queryFn: ({ signal }) => api.listPhases(seasonId!, signal),
    enabled: seasonId !== undefined,
    retry: false,
  });
  const columns = useMemo<ColumnDef<Phase>[]>(
    () => [
      { accessorKey: "id", header: "ID" },
      { accessorKey: "name", header: "Phase" },
      { accessorKey: "start_event", header: "Starts" },
      { accessorKey: "stop_event", header: "Stops" },
    ],
    [],
  );
  if (seasonId === undefined) {
    return <SelectionRequired kind="season">{null}</SelectionRequired>;
  }
  return (
    <ResourcePage
      eyebrow={seasonId}
      title="Phases"
      description="Competition phases and their event boundaries."
      apiOperations={[API_OPERATIONS.listPhases]}
      query={query}
      columns={columns}
      getRowId={(row) => String(row.id)}
      getRowLabel={(row) => row.name}
      emptyMessage="No phases are stored for this season."
    />
  );
}

export function TeamsPage() {
  const api = useRelayApi();
  const { seasonId } = useSelection();
  const query = useQuery({
    queryKey: ["teams", seasonId],
    queryFn: ({ signal }) => api.listTeams(seasonId!, signal),
    enabled: seasonId !== undefined,
    retry: false,
  });
  const columns = useMemo<ColumnDef<Team>[]>(
    () => [
      { accessorKey: "id", header: "ID" },
      { accessorKey: "name", header: "Team" },
      { accessorKey: "short_name", header: "Short" },
      { accessorKey: "strength", header: "Strength" },
      { accessorKey: "strength_attack_home", header: "Attack H" },
      { accessorKey: "strength_attack_away", header: "Attack A" },
      { accessorKey: "strength_defence_home", header: "Defence H" },
      { accessorKey: "strength_defence_away", header: "Defence A" },
    ],
    [],
  );
  if (seasonId === undefined) {
    return <SelectionRequired kind="season">{null}</SelectionRequired>;
  }
  return (
    <ResourcePage
      eyebrow={seasonId}
      title="Teams"
      description="Premier League teams and normalized strength values."
      apiOperations={[API_OPERATIONS.listTeams]}
      query={query}
      columns={columns}
      getRowId={(row) => String(row.id)}
      getRowLabel={(row) => row.name}
      detailLoader={{
        apiOperation: API_OPERATIONS.getTeam,
        queryKey: ["team", seasonId],
        load: (id, signal) => api.getTeam(seasonId, Number(id), signal),
      }}
      emptyMessage="No teams are stored for this season."
    />
  );
}

export function ElementTypesPage() {
  const api = useRelayApi();
  const { seasonId } = useSelection();
  const query = useQuery({
    queryKey: ["element-types", seasonId],
    queryFn: ({ signal }) => api.listElementTypes(seasonId!, signal),
    enabled: seasonId !== undefined,
    retry: false,
  });
  const columns = useMemo<ColumnDef<ElementType>[]>(
    () => [
      { accessorKey: "id", header: "ID" },
      { accessorKey: "singular_name", header: "Position" },
      { accessorKey: "singular_name_short", header: "Short" },
      { accessorKey: "element_count", header: "Players" },
      { accessorKey: "squad_select", header: "Squad select" },
      { accessorKey: "squad_min_play", header: "Min play" },
      { accessorKey: "squad_max_play", header: "Max play" },
    ],
    [],
  );
  if (seasonId === undefined) {
    return <SelectionRequired kind="season">{null}</SelectionRequired>;
  }
  return (
    <ResourcePage
      eyebrow={seasonId}
      title="Element types"
      description="Player position definitions stored for the selected season."
      apiOperations={[API_OPERATIONS.listElementTypes]}
      query={query}
      columns={columns}
      getRowId={(row) => String(row.id)}
      getRowLabel={(row) => row.singular_name}
      emptyMessage="No element types are stored for this season."
    />
  );
}

export function PlayersPage() {
  const api = useRelayApi();
  const selection = useSelection();
  const [teamFilter, setTeamFilter] = useFilterParam("team");
  const [typeFilter, setTypeFilter] = useFilterParam("position");
  const query = useQuery({
    queryKey: ["elements", selection.seasonId],
    queryFn: ({ signal }) => api.listElements(selection.seasonId!, signal),
    enabled: selection.seasonId !== undefined,
    retry: false,
  });
  const records = useMemo(
    () =>
      (query.data ?? []).filter(
        (row) =>
          (teamFilter === undefined || String(row.team) === teamFilter) &&
          (typeFilter === undefined ||
            String(row.element_type) === typeFilter),
      ),
    [query.data, teamFilter, typeFilter],
  );
  const lookups = useReferenceLookups(selection.seasonId);
  const columns = useMemo<ColumnDef<Element>[]>(
    () => [
      { accessorKey: "id", header: "ID" },
      {
        id: "name",
        header: "Player",
        accessorFn: playerName,
      },
      {
        id: "team_name",
        header: "Team",
        accessorFn: (row) => lookups.team(row.team),
      },
      {
        id: "position",
        header: "Position",
        accessorFn: (row) => lookups.elementType(row.element_type),
      },
      {
        accessorKey: "now_cost",
        header: "Cost",
        cell: ({ getValue }) => formatCost(getValue<number | null>()),
      },
      { accessorKey: "status", header: "Status" },
      { accessorKey: "form", header: "Form" },
      { accessorKey: "total_points", header: "Points" },
      { accessorKey: "selected_by_percent", header: "Selected %" },
    ],
    [lookups],
  );
  if (selection.seasonId === undefined) {
    return <SelectionRequired kind="season">{null}</SelectionRequired>;
  }
  return (
    <ResourcePage
      eyebrow={selection.seasonId}
      title="Players"
      description="Normalized player records with resolved team and position names."
      apiOperations={[API_OPERATIONS.listElements]}
      query={query}
      records={records}
      columns={columns}
      getRowId={(row) => String(row.id)}
      getRowLabel={playerName}
      detailLoader={{
        apiOperation: API_OPERATIONS.getElement,
        queryKey: ["element", selection.seasonId],
        load: (id, signal) =>
          api.getElement(selection.seasonId!, Number(id), signal),
      }}
      controls={
        <>
          <label>
            Team
            <select
              value={teamFilter ?? ""}
              onChange={(event) =>
                setTeamFilter(event.target.value || undefined)
              }
            >
              <option value="">All teams</option>
              {selection.seasonId === undefined
                ? null
                : Array.from(
                    new Map(
                      (query.data ?? []).map((row) => [
                        row.team,
                        lookups.team(row.team),
                      ]),
                    ),
                  ).map(([id, name]) => (
                    <option key={id} value={id}>
                      {name}
                    </option>
                  ))}
            </select>
          </label>
          <label>
            Position
            <select
              value={typeFilter ?? ""}
              onChange={(event) =>
                setTypeFilter(event.target.value || undefined)
              }
            >
              <option value="">All positions</option>
              {Array.from(
                new Map(
                  (query.data ?? []).map((row) => [
                    row.element_type,
                    lookups.elementType(row.element_type),
                  ]),
                ),
              ).map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        </>
      }
      emptyMessage="No players match the current filters."
    />
  );
}

export function FixturesPage() {
  const api = useRelayApi();
  const selection = useSelection();
  const [scope, setScope] = useFilterParam("scope");
  const eventScope = scope === "event";
  const query = useQuery({
    queryKey: [
      "fixtures",
      selection.seasonId,
      eventScope ? selection.eventId : "season",
    ],
    queryFn: ({ signal }) =>
      eventScope
        ? api.listEventFixtures(
            selection.seasonId!,
            selection.eventId!,
            signal,
          )
        : api.listFixtures(selection.seasonId!, signal),
    enabled:
      selection.seasonId !== undefined &&
      (!eventScope || selection.eventId !== undefined),
    retry: false,
  });
  const lookups = useReferenceLookups(selection.seasonId);
  const columns = useMemo<ColumnDef<Fixture>[]>(
    () => [
      { accessorKey: "id", header: "ID" },
      {
        id: "event_name",
        header: "Event",
        accessorFn: (row) => lookups.event(row.event),
      },
      {
        accessorKey: "kickoff_time",
        header: "Kickoff",
        cell: ({ getValue }) => formatDateTime(getValue<string | null>()),
      },
      {
        id: "home_team",
        header: "Home",
        accessorFn: (row) => lookups.team(row.team_h),
      },
      { accessorKey: "team_h_score", header: "H" },
      { accessorKey: "team_a_score", header: "A" },
      {
        id: "away_team",
        header: "Away",
        accessorFn: (row) => lookups.team(row.team_a),
      },
      { id: "status", header: "Status", accessorFn: fixtureStatus },
    ],
    [lookups],
  );
  if (selection.seasonId === undefined) {
    return <SelectionRequired kind="season">{null}</SelectionRequired>;
  }
  if (eventScope && selection.eventId === undefined) {
    return <SelectionRequired kind="event">{null}</SelectionRequired>;
  }
  return (
    <ResourcePage
      eyebrow={selection.seasonId}
      title="Fixtures"
      description={
        eventScope
          ? "Fixtures assigned to the selected event."
          : "All fixtures stored for the selected season."
      }
      apiOperations={[
        eventScope
          ? API_OPERATIONS.listEventFixtures
          : API_OPERATIONS.listFixtures,
      ]}
      query={query}
      columns={columns}
      getRowId={(row) => String(row.id)}
      getRowLabel={(row) =>
        `${lookups.team(row.team_h)} vs ${lookups.team(row.team_a)}`
      }
      controls={
        <label>
          Scope
          <select
            value={eventScope ? "event" : "season"}
            onChange={(event) =>
              setScope(event.target.value === "event" ? "event" : undefined)
            }
          >
            <option value="season">Whole season</option>
            <option value="event">Selected event</option>
          </select>
        </label>
      }
      emptyMessage="No fixtures are stored for this scope."
    />
  );
}
