import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { useMemo } from "react";

import { useSelection } from "../../app/selection";
import { useRelayApi } from "../../api/provider";
import type { EventStatusDay, LiveElement } from "../../api/types";
import { API_OPERATIONS } from "../../components/ApiDocsLink";
import { ResourcePage } from "../../components/ResourcePage";
import { SelectionRequired } from "../../components/SelectionRequired";
import {
  formatBoolean,
} from "../../lib/format";
import { useReferenceLookups } from "../reference/useReferenceLookups";

export function EventStatusPage() {
  const api = useRelayApi();
  const { seasonId } = useSelection();
  const query = useQuery({
    queryKey: ["event-status", seasonId],
    queryFn: ({ signal }) => api.getEventStatus(seasonId!, signal),
    enabled: seasonId !== undefined,
    retry: false,
  });
  const lookups = useReferenceLookups(seasonId);
  const columns = useMemo<ColumnDef<EventStatusDay>[]>(
    () => [
      {
        id: "event_name",
        header: "Event",
        accessorFn: (row) => lookups.event(row.event),
      },
      { accessorKey: "date", header: "Date" },
      {
        accessorKey: "bonus_added",
        header: "Bonus added",
        cell: ({ getValue }) => formatBoolean(getValue<boolean>()),
      },
      {
        accessorKey: "leagues_updated",
        header: "Leagues updated",
        cell: ({ getValue }) => formatBoolean(getValue<boolean | null>()),
      },
    ],
    [lookups],
  );
  if (seasonId === undefined) {
    return <SelectionRequired kind="season">{null}</SelectionRequired>;
  }
  return (
    <ResourcePage
      eyebrow={seasonId}
      title="Event status"
      description={`Latest stored event-status response${
        query.data?.leagues ? ` · leagues: ${query.data.leagues}` : ""
      }.`}
      apiOperations={[API_OPERATIONS.getEventStatus]}
      query={query}
      records={query.data?.status}
      columns={columns}
      getRowId={(row) => `${row.event}-${row.date}`}
      getRowLabel={(row) => `${lookups.event(row.event)} · ${row.date}`}
      rawResponse={query.data}
      emptyMessage="The stored event-status response has no status rows."
    />
  );
}

export function LivePlayersPage() {
  const api = useRelayApi();
  const selection = useSelection();
  const query = useQuery({
    queryKey: ["live-elements", selection.seasonId, selection.eventId],
    queryFn: ({ signal }) =>
      api.listLiveElements(
        selection.seasonId!,
        selection.eventId!,
        signal,
      ),
    enabled:
      selection.seasonId !== undefined && selection.eventId !== undefined,
    retry: false,
  });
  const lookups = useReferenceLookups(selection.seasonId);
  const columns = useMemo<ColumnDef<LiveElement>[]>(
    () => [
      { accessorKey: "id", header: "ID" },
      {
        id: "player",
        header: "Player",
        accessorFn: (row) => lookups.element(row.id),
      },
      { accessorKey: "stats.total_points", header: "Points" },
      { accessorKey: "stats.minutes", header: "Minutes" },
      { accessorKey: "stats.goals_scored", header: "Goals" },
      { accessorKey: "stats.assists", header: "Assists" },
      { accessorKey: "stats.clean_sheets", header: "Clean sheets" },
      { accessorKey: "stats.bonus", header: "Bonus" },
      { accessorKey: "stats.bps", header: "BPS" },
      {
        accessorKey: "stats.in_dreamteam",
        header: "Dream team",
        cell: ({ getValue }) => formatBoolean(getValue<boolean | null>()),
      },
    ],
    [lookups],
  );
  if (selection.seasonId === undefined) {
    return <SelectionRequired kind="season">{null}</SelectionRequired>;
  }
  if (selection.eventId === undefined) {
    return <SelectionRequired kind="event">{null}</SelectionRequired>;
  }
  return (
    <ResourcePage
      eyebrow={`${selection.seasonId} · ${lookups.event(selection.eventId)}`}
      title="Live players"
      description="Live player totals and fixture-level points explanations."
      apiOperations={[API_OPERATIONS.listLiveElements]}
      query={query}
      columns={columns}
      getRowId={(row) => String(row.id)}
      getRowLabel={(row) => lookups.element(row.id)}
      detailLoader={{
        apiOperation: API_OPERATIONS.getLiveElement,
        queryKey: [
          "live-element",
          selection.seasonId,
          selection.eventId,
        ],
        load: (id, signal) =>
          api.getLiveElement(
            selection.seasonId!,
            selection.eventId!,
            Number(id),
            signal,
          ),
      }}
      emptyMessage="No live player records are stored for this event."
    />
  );
}
