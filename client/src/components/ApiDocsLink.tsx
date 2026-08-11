import type { operations } from "../api/generated";

type OperationId = keyof operations;
type OpenApiTag =
  | "Service"
  | "Reference Data"
  | "Live Data"
  | "Change Events";

export interface ApiOperation {
  label: string;
  operationId: OperationId;
  tag: OpenApiTag;
}

export const API_OPERATIONS = {
  listSeasons: {
    label: "List seasons",
    operationId: "list_seasons",
    tag: "Reference Data",
  },
  getSeason: {
    label: "Get season",
    operationId: "get_season",
    tag: "Reference Data",
  },
  listEvents: {
    label: "List events",
    operationId: "list_events",
    tag: "Reference Data",
  },
  getEvent: {
    label: "Get event",
    operationId: "get_event",
    tag: "Reference Data",
  },
  listPhases: {
    label: "List phases",
    operationId: "list_phases",
    tag: "Reference Data",
  },
  listTeams: {
    label: "List teams",
    operationId: "list_teams",
    tag: "Reference Data",
  },
  getTeam: {
    label: "Get team",
    operationId: "get_team",
    tag: "Reference Data",
  },
  listElementTypes: {
    label: "List element types",
    operationId: "list_element_types",
    tag: "Reference Data",
  },
  listElements: {
    label: "List players",
    operationId: "list_elements",
    tag: "Reference Data",
  },
  getElement: {
    label: "Get player",
    operationId: "get_element",
    tag: "Reference Data",
  },
  listFixtures: {
    label: "List season fixtures",
    operationId: "list_fixtures",
    tag: "Reference Data",
  },
  listEventFixtures: {
    label: "List event fixtures",
    operationId: "list_event_fixtures",
    tag: "Reference Data",
  },
  getEventStatus: {
    label: "Get event status",
    operationId: "get_event_status",
    tag: "Live Data",
  },
  listLiveElements: {
    label: "List live players",
    operationId: "list_live_elements",
    tag: "Live Data",
  },
  getLiveElement: {
    label: "Get live player",
    operationId: "get_live_element",
    tag: "Live Data",
  },
  listRecentChangeEvents: {
    label: "Recent changes",
    operationId: "list_recent_change_events",
    tag: "Change Events",
  },
  listChangeEvents: {
    label: "Catch-up polling",
    operationId: "list_change_events",
    tag: "Change Events",
  },
  listChangeEventHistory: {
    label: "Older history",
    operationId: "list_change_event_history",
    tag: "Change Events",
  },
  listEntityChanges: {
    label: "Changed entity details",
    operationId: "list_entity_changes",
    tag: "Change Events",
  },
  getIngestionStatus: {
    label: "Ingestion status",
    operationId: "get_ingestion_status",
    tag: "Change Events",
  },
} as const satisfies Record<string, ApiOperation>;

export function swaggerOperationHref(operation: ApiOperation): string {
  const tag = encodeURIComponent(operation.tag);
  const operationId = encodeURIComponent(operation.operationId);
  return `/api/docs#/${tag}/${operationId}`;
}

export function ApiDocsLink({
  label,
  operation,
  variant,
}: {
  label: string;
  operation: ApiOperation | null;
  variant: "button" | "inline" | "navigation";
}) {
  const href = operation === null ? "/api/docs" : swaggerOperationHref(operation);
  const className =
    variant === "button"
      ? "button api-docs-link"
      : `api-docs-link api-docs-link--${variant}`;
  return (
    <a
      aria-label={`${label} (opens in a new tab)`}
      className={className}
      href={href}
      rel="noreferrer"
      target="_blank"
    >
      {label} <span aria-hidden="true">↗</span>
    </a>
  );
}

export function ApiDocsActions({
  operations,
}: {
  operations: readonly ApiOperation[];
}) {
  if (operations.length === 0) {
    throw new Error("API documentation actions require at least one operation.");
  }
  if (operations.length === 1) {
    return (
      <ApiDocsLink
        label="Explore endpoint"
        operation={operations[0]}
        variant="button"
      />
    );
  }
  return (
    <details className="api-docs-menu">
      <summary className="button">API endpoints</summary>
      <div aria-label="API endpoints">
        {operations.map((operation) => (
          <ApiDocsLink
            key={operation.operationId}
            label={operation.label}
            operation={operation}
            variant="inline"
          />
        ))}
      </div>
    </details>
  );
}
