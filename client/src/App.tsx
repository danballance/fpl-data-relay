import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { Navigate, Route, Routes } from "react-router-dom";

import { RelayApiProvider } from "./api/provider";
import type { RelayApi } from "./api/relay-api";
import { AppShell } from "./app/AppShell";
import { SelectionProvider } from "./app/selection";
import { ActivityPage } from "./features/activity/ActivityPage";
import {
  EventStatusPage,
  LivePlayersPage,
} from "./features/live/LivePages";
import { OverviewPage } from "./features/overview/OverviewPage";
import {
  ElementTypesPage,
  EventsPage,
  FixturesPage,
  PhasesPage,
  PlayersPage,
  SeasonsPage,
  TeamsPage,
} from "./features/reference/ReferencePages";

export function App({
  api,
  queryClient,
}: {
  api: RelayApi;
  queryClient: QueryClient;
}) {
  return (
    <QueryClientProvider client={queryClient}>
      <RelayApiProvider api={api}>
        <SelectionProvider>
          <Routes>
            <Route element={<AppShell />}>
              <Route index element={<OverviewPage />} />
              <Route path="seasons" element={<SeasonsPage />} />
              <Route path="events" element={<EventsPage />} />
              <Route path="phases" element={<PhasesPage />} />
              <Route path="teams" element={<TeamsPage />} />
              <Route path="element-types" element={<ElementTypesPage />} />
              <Route path="players" element={<PlayersPage />} />
              <Route path="fixtures" element={<FixturesPage />} />
              <Route path="event-status" element={<EventStatusPage />} />
              <Route path="live-players" element={<LivePlayersPage />} />
              <Route path="activity" element={<ActivityPage />} />
              <Route path="*" element={<Navigate replace to="/" />} />
            </Route>
          </Routes>
        </SelectionProvider>
      </RelayApiProvider>
    </QueryClientProvider>
  );
}
