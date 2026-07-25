import { useQuery } from "@tanstack/react-query";

import { useRelayApi } from "../../api/provider";
import { createLookups, type RelayLookups } from "../../lib/format";

export function useReferenceLookups(
  seasonId: string | undefined,
): RelayLookups {
  const api = useRelayApi();
  const teams = useQuery({
    queryKey: ["teams", seasonId],
    queryFn: ({ signal }) => api.listTeams(seasonId!, signal),
    enabled: seasonId !== undefined,
    retry: false,
  });
  const elementTypes = useQuery({
    queryKey: ["element-types", seasonId],
    queryFn: ({ signal }) => api.listElementTypes(seasonId!, signal),
    enabled: seasonId !== undefined,
    retry: false,
  });
  const elements = useQuery({
    queryKey: ["elements", seasonId],
    queryFn: ({ signal }) => api.listElements(seasonId!, signal),
    enabled: seasonId !== undefined,
    retry: false,
  });
  const events = useQuery({
    queryKey: ["events", seasonId],
    queryFn: ({ signal }) => api.listEvents(seasonId!, signal),
    enabled: seasonId !== undefined,
    retry: false,
  });

  return createLookups({
    teams: teams.data ?? [],
    elementTypes: elementTypes.data ?? [],
    elements: elements.data ?? [],
    events: events.data ?? [],
  });
}
