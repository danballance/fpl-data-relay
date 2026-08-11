import type { ReactNode } from "react";
import {
  NavLink,
  Outlet,
  useLocation,
} from "react-router-dom";

import { ApiDocsLink } from "../components/ApiDocsLink";
import { useSelection } from "./selection";

const NAVIGATION = [
  {
    label: "Relay",
    links: [
      ["/", "Overview"],
      ["/seasons", "Seasons"],
    ],
  },
  {
    label: "Reference data",
    links: [
      ["/events", "Events"],
      ["/phases", "Phases"],
      ["/teams", "Teams"],
      ["/element-types", "Element types"],
      ["/players", "Players"],
      ["/fixtures", "Fixtures"],
    ],
  },
  {
    label: "Live data",
    links: [
      ["/event-status", "Event status"],
      ["/live-players", "Live players"],
    ],
  },
  {
    label: "Changes",
    links: [["/activity", "Activity"]],
  },
] as const;

function PersistentNavLink({
  to,
  children,
}: {
  to: string;
  children: ReactNode;
}) {
  const location = useLocation();
  return (
    <NavLink to={{ pathname: to, search: location.search }}>{children}</NavLink>
  );
}

export function AppShell() {
  const selection = useSelection();
  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">FR</span>
          <div>
            <strong>Relay Explorer</strong>
            <span>Local development client</span>
          </div>
        </div>
        <div className="topbar__controls">
          <div
            className={`connection connection--${selection.healthState}`}
            title={
              selection.health === undefined
                ? "Relay health unavailable"
                : `Schema version ${selection.health.schema_version}`
            }
          >
            <span aria-hidden="true" />
            {selection.healthState === "online"
              ? "Relay online"
              : selection.healthState === "loading"
                ? "Checking relay"
                : "Relay offline"}
          </div>
          <label className="select-field">
            <span>Season</span>
            <select
              aria-label="Season"
              value={selection.seasonId ?? ""}
              onChange={(event) =>
                selection.setSeasonId(event.target.value || undefined)
              }
            >
              <option value="">Select season</option>
              {selection.seasons.map((season) => (
                <option key={season.id} value={season.id}>
                  {season.id}
                  {season.is_current ? " · current" : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="select-field">
            <span>Event</span>
            <select
              aria-label="Event"
              disabled={selection.seasonId === undefined}
              value={selection.eventId ?? ""}
              onChange={(event) =>
                selection.setEventId(
                  event.target.value === ""
                    ? undefined
                    : Number(event.target.value),
                )
              }
            >
              <option value="">Select event</option>
              {selection.events.map((event) => (
                <option key={event.id} value={event.id}>
                  {event.name}
                  {event.is_current ? " · current" : ""}
                </option>
              ))}
            </select>
          </label>
          <button
            className="button"
            type="button"
            onClick={() => void selection.refreshAll()}
          >
            Refresh all
          </button>
        </div>
      </header>
      <aside className="sidebar">
        <nav aria-label="Explorer sections">
          {NAVIGATION.map((section) => (
            <section key={section.label}>
              <h2>{section.label}</h2>
              {section.links.map(([path, label]) => (
                <PersistentNavLink key={path} to={path}>
                  {label}
                </PersistentNavLink>
              ))}
            </section>
          ))}
        </nav>
        <div className="sidebar-footer">
          <ApiDocsLink
            label="API reference"
            operation={null}
            variant="navigation"
          />
          <p className="sidebar-note">
            Read-only. Responses come from stored relay data.
          </p>
        </div>
      </aside>
      <main className="main-content">
        <Outlet />
      </main>
    </div>
  );
}
