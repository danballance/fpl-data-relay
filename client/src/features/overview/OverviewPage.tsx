import { useQuery } from "@tanstack/react-query";
import { Link, useLocation } from "react-router-dom";

import { useSelection } from "../../app/selection";
import { useRelayApi } from "../../api/provider";
import { PageHeader } from "../../components/PageHeader";

function CountCard({
  label,
  value,
  path,
}: {
  label: string;
  value: number | undefined;
  path: string;
}) {
  const location = useLocation();
  return (
    <Link
      className="count-card"
      to={{ pathname: path, search: location.search }}
    >
      <span>{label}</span>
      <strong>{value === undefined ? "—" : value.toLocaleString()}</strong>
      <small>Open data →</small>
    </Link>
  );
}

export function OverviewPage() {
  const api = useRelayApi();
  const selection = useSelection();
  const enabled = selection.seasonId !== undefined;
  const events = useQuery({
    queryKey: ["events", selection.seasonId],
    queryFn: ({ signal }) => api.listEvents(selection.seasonId!, signal),
    enabled,
    retry: false,
  });
  const teams = useQuery({
    queryKey: ["teams", selection.seasonId],
    queryFn: ({ signal }) => api.listTeams(selection.seasonId!, signal),
    enabled,
    retry: false,
  });
  const players = useQuery({
    queryKey: ["elements", selection.seasonId],
    queryFn: ({ signal }) => api.listElements(selection.seasonId!, signal),
    enabled,
    retry: false,
  });
  const fixtures = useQuery({
    queryKey: ["fixtures", selection.seasonId, "season"],
    queryFn: ({ signal }) => api.listFixtures(selection.seasonId!, signal),
    enabled,
    retry: false,
  });
  const selectedSeason = selection.seasons.find(
    (season) => season.id === selection.seasonId,
  );
  const selectedEvent = selection.events.find(
    (event) => event.id === selection.eventId,
  );

  return (
    <>
      <PageHeader
        eyebrow="Stored relay data"
        title="Explore what the relay is holding."
        description="Inspect normalized reference, live, fixture, and change-event records without touching the upstream FPL API."
        actions={
          <Link className="button button--primary" to="/players">
            Browse players
          </Link>
        }
      />
      <section className="overview-selection">
        <div>
          <span>Selected season</span>
          <strong>{selectedSeason?.id ?? "No season selected"}</strong>
          <small>
            {selectedSeason?.is_current
              ? "Marked current by ingestion"
              : "Choose an explicit season above"}
          </small>
        </div>
        <div>
          <span>Selected event</span>
          <strong>{selectedEvent?.name ?? "No event selected"}</strong>
          <small>
            {selectedEvent?.is_current
              ? "Marked current by ingestion"
              : "Required for live player data"}
          </small>
        </div>
        <div>
          <span>Relay schema</span>
          <strong>
            {selection.health === undefined
              ? "Unavailable"
              : `Version ${selection.health.schema_version}`}
          </strong>
          <small>Reported by /healthz</small>
        </div>
      </section>
      <section className="overview-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Selected season</p>
            <h2>Stored entity counts</h2>
          </div>
          {enabled ? null : <span>Select a season to load counts.</span>}
        </div>
        <div className="count-grid">
          <CountCard label="Events" value={events.data?.length} path="/events" />
          <CountCard label="Teams" value={teams.data?.length} path="/teams" />
          <CountCard
            label="Players"
            value={players.data?.length}
            path="/players"
          />
          <CountCard
            label="Fixtures"
            value={fixtures.data?.length}
            path="/fixtures"
          />
        </div>
      </section>
      <section className="overview-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Validation workflow</p>
            <h2>Move from reference data to live changes</h2>
          </div>
        </div>
        <div className="workflow-grid">
          <article>
            <span>01</span>
            <h3>Confirm reference entities</h3>
            <p>Check seasons, teams, positions, players, and fixtures.</p>
          </article>
          <article>
            <span>02</span>
            <h3>Inspect an event</h3>
            <p>Choose a gameweek and compare its fixtures and live totals.</p>
          </article>
          <article>
            <span>03</span>
            <h3>Watch ingestion activity</h3>
            <p>Review stored change metadata and follow the SSE feed.</p>
          </article>
        </div>
      </section>
    </>
  );
}
