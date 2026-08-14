import {
  QueryClientProvider,
  type QueryClient,
  useQuery,
} from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Route, Routes } from "react-router-dom";

import { RelayApiProvider } from "./api/provider";
import type { RelayApi } from "./api/relay-api";
import { RelayApiError, errorMessage } from "./api/errors";
import { AppShell } from "./app/AppShell";
import { SelectionProvider } from "./app/selection";
import { ActivityPage } from "./features/activity/ActivityPage";
import { CommunityPage } from "./features/community/CommunityPage";
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
        <ServiceReadinessGate api={api}>
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
              <Route path="community" element={<CommunityPage />} />
              <Route path="*" element={<OverviewPage />} />
            </Route>
          </Routes>
          </SelectionProvider>
        </ServiceReadinessGate>
      </RelayApiProvider>
    </QueryClientProvider>
  );
}

function ServiceReadinessGate({
  api,
  children,
}: {
  api: RelayApi;
  children: ReactNode;
}) {
  const readiness = useQuery({
    queryKey: ["readiness"],
    queryFn: ({ signal }) => api.getReadiness(signal),
    retry: (failureCount, error) =>
      error instanceof RelayApiError &&
      error.code === "database_waking" &&
      failureCount < 8,
    retryDelay: 5_000,
  });
  if (readiness.isSuccess) return children;
  const waking =
    readiness.error instanceof RelayApiError &&
    readiness.error.code === "database_waking";
  return (
    <main className="service-gate" aria-live="polite">
      <div className="service-gate__panel">
        <span className="brand-mark">FR</span>
        <h1>
          {readiness.isPending || waking
            ? "Service waking up"
            : "Service unavailable"}
        </h1>
        <p>
          {readiness.isPending || waking
            ? "The database was idle. We’ll retry every five seconds."
            : errorMessage(readiness.error)}
        </p>
        {readiness.isError ? (
          <button
            className="button"
            type="button"
            onClick={() => void readiness.refetch()}
          >
            Retry now
          </button>
        ) : null}
      </div>
    </main>
  );
}
