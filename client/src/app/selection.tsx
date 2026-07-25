import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
} from "react";
import { useSearchParams } from "react-router-dom";

import { useRelayApi } from "../api/provider";
import type { Event, Health, Season } from "../api/types";

interface SelectionContextValue {
  health: Health | undefined;
  healthState: "loading" | "online" | "offline";
  seasons: Season[];
  events: Event[];
  seasonId: string | undefined;
  eventId: number | undefined;
  setSeasonId: (value: string | undefined) => void;
  setEventId: (value: number | undefined) => void;
  refreshAll: () => Promise<void>;
}

const SelectionContext = createContext<SelectionContextValue | null>(null);

function positiveInteger(value: string | null): number | undefined {
  if (value === null) {
    return undefined;
  }
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 1 ? parsed : undefined;
}

export function SelectionProvider({ children }: { children: ReactNode }) {
  const api = useRelayApi();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const seasonId = searchParams.get("season") ?? undefined;
  const eventId = positiveInteger(searchParams.get("event"));

  const healthQuery = useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) => api.getHealth(signal),
    retry: false,
    refetchInterval: 30_000,
  });
  const seasonsQuery = useQuery({
    queryKey: ["seasons"],
    queryFn: ({ signal }) => api.listSeasons(signal),
    retry: false,
  });
  const currentSeasonQuery = useQuery({
    queryKey: ["seasons", "current"],
    queryFn: ({ signal }) => api.getCurrentSeason(signal),
    retry: false,
  });
  const eventsQuery = useQuery({
    queryKey: ["events", seasonId],
    queryFn: ({ signal }) => api.listEvents(seasonId!, signal),
    enabled: seasonId !== undefined,
    retry: false,
  });
  const currentEventQuery = useQuery({
    queryKey: ["events", seasonId, "current"],
    queryFn: ({ signal }) => api.getCurrentEvent(seasonId!, signal),
    enabled: seasonId !== undefined,
    retry: false,
  });

  useEffect(() => {
    if (seasonId !== undefined || currentSeasonQuery.data === undefined) {
      return;
    }
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("season", currentSeasonQuery.data.id);
      return next;
    });
  }, [currentSeasonQuery.data, seasonId, setSearchParams]);

  useEffect(() => {
    if (
      seasonId === undefined ||
      eventId !== undefined ||
      currentEventQuery.data === undefined
    ) {
      return;
    }
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("event", String(currentEventQuery.data.id));
      return next;
    });
  }, [
    currentEventQuery.data,
    eventId,
    seasonId,
    setSearchParams,
  ]);

  const setSeasonId = (value: string | undefined) =>
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (value === undefined) {
        next.delete("season");
      } else {
        next.set("season", value);
      }
      next.delete("event");
      next.delete("record");
      next.delete("page");
      return next;
    });
  const setEventId = (value: number | undefined) =>
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (value === undefined) {
        next.delete("event");
      } else {
        next.set("event", String(value));
      }
      next.delete("record");
      next.delete("page");
      return next;
    });

  return (
    <SelectionContext.Provider
      value={{
        health: healthQuery.data,
        healthState: healthQuery.isPending
          ? "loading"
          : healthQuery.isError
            ? "offline"
            : "online",
        seasons: seasonsQuery.data ?? [],
        events: eventsQuery.data ?? [],
        seasonId,
        eventId,
        setSeasonId,
        setEventId,
        refreshAll: async () => {
          await queryClient.invalidateQueries();
        },
      }}
    >
      {children}
    </SelectionContext.Provider>
  );
}

export function useSelection(): SelectionContextValue {
  const value = useContext(SelectionContext);
  if (value === null) {
    throw new Error("SelectionProvider is missing.");
  }
  return value;
}
