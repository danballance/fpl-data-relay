import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { RelayApiError, errorMessage } from "../../api/errors";
import { useRelayApi } from "../../api/provider";
import type { CommunityReport } from "../../api/types";
import { PageHeader } from "../../components/PageHeader";
import { StatusPanel } from "../../components/StatusPanel";

const HISTORY_LIMIT = 100;

function parsedReportId(value: string | null): number | undefined {
  if (value === null) return undefined;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
}

function displayDate(value: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Europe/London",
  }).format(new Date(value));
}

function entityPath(
  entity: CommunityReport["content"]["stories"][number]["entities"][number],
): string {
  const params = new URLSearchParams({
    season: entity.season_id,
    record: String(entity.entity_id),
  });
  if (entity.entity_type === "event") {
    params.set("event", String(entity.entity_id));
    return `/events?${params}`;
  }
  if (entity.entity_type === "fixture") {
    if (entity.snapshot.event_id !== null) {
      params.set("event", String(entity.snapshot.event_id));
    }
    return `/fixtures?${params}`;
  }
  return entity.entity_type === "player"
    ? `/players?${params}`
    : `/teams?${params}`;
}

function EntityCard({
  entity,
}: {
  entity: CommunityReport["content"]["stories"][number]["entities"][number];
}) {
  let details: string;
  if (entity.entity_type === "player") {
    const price =
      entity.snapshot.now_cost === null
        ? "price unavailable"
        : `£${(entity.snapshot.now_cost / 10).toFixed(1)}m`;
    details = `${entity.snapshot.team_name} · ${entity.snapshot.element_type_name} · ${price} · ${entity.snapshot.total_points ?? "—"} points`;
  } else if (entity.entity_type === "team") {
    details = `${entity.snapshot.short_name} · strength ${entity.snapshot.strength ?? "—"}`;
  } else if (entity.entity_type === "event") {
    details = `${entity.snapshot.finished ? "Complete" : "Open"} · average score ${entity.snapshot.average_entry_score ?? "—"}`;
  } else {
    const score =
      entity.snapshot.home_score === null
        ? "not started"
        : `${entity.snapshot.home_score}–${entity.snapshot.away_score}`;
    details = `${entity.snapshot.home_team_name} v ${entity.snapshot.away_team_name} · ${score}`;
  }
  return (
    <Link className="community-entity" to={entityPath(entity)}>
      <span>{entity.entity_type}</span>
      <strong>{entity.display_name}</strong>
      <small>{details}</small>
    </Link>
  );
}

function StoryCard({
  story,
}: {
  story: CommunityReport["content"]["stories"][number];
}) {
  const components = story.momentum_components;
  return (
    <article className="community-story">
      <div className="community-story__rank">{story.rank}</div>
      <div className="community-story__body">
        <div className="community-story__heading">
          <div>
            <span className="topic-chip">{story.category.replaceAll("_", " ")}</span>
            <h2>{story.headline}</h2>
          </div>
          <div className="momentum-score">
            <strong>{story.momentum_score.toFixed(1)}</strong>
            <span>momentum</span>
          </div>
        </div>
        <p>{story.summary}</p>
        <dl className="momentum-components">
          <div><dt>Sources</dt><dd>{components.source_breadth.toFixed(1)} / 35</dd></div>
          <div><dt>Evidence</dt><dd>{components.evidence_volume.toFixed(1)} / 20</dd></div>
          <div><dt>Engagement</dt><dd>{components.engagement.toFixed(1)} / 20</dd></div>
          <div><dt>Recency</dt><dd>{components.recency.toFixed(1)} / 15</dd></div>
          <div><dt>Actionability</dt><dd>{components.actionability.toFixed(1)} / 10</dd></div>
        </dl>
        <div className="community-evidence">
          <h3>Evidence</h3>
          <ul>
            {story.evidence.map((evidence) => (
              <li key={evidence.document_id}>
                <a href={evidence.url} target="_blank" rel="noreferrer">
                  {evidence.publisher}: {evidence.title}
                </a>
                <span>{displayDate(evidence.published_at)}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="community-entities">
          {story.entities.map((entity) => (
            <EntityCard key={`${entity.entity_type}:${entity.entity_id}`} entity={entity} />
          ))}
        </div>
      </div>
    </article>
  );
}

export function CommunityPage() {
  const api = useRelayApi();
  const [searchParams, setSearchParams] = useSearchParams();
  const strategyKey = searchParams.get("strategy") ?? undefined;
  const reportId = parsedReportId(searchParams.get("report"));
  const strategies = useQuery({
    queryKey: ["community-strategies"],
    queryFn: ({ signal }) => api.listCommunityStrategies(signal),
    retry: false,
  });
  const selectedStrategy = strategies.data?.find(
    (strategy) => strategy.key === strategyKey,
  );

  useEffect(() => {
    const firstStrategy = strategies.data?.[0];
    if (strategyKey !== undefined || firstStrategy === undefined) return;
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("strategy", firstStrategy.key);
      return next;
    });
  }, [setSearchParams, strategies.data, strategyKey]);

  const history = useInfiniteQuery({
    queryKey: ["community-reports", strategyKey, "recent"],
    initialPageParam: null as number | null,
    queryFn: ({ signal, pageParam }) =>
      pageParam === null
        ? api.listRecentCommunityReports(strategyKey!, HISTORY_LIMIT, signal)
        : api.listCommunityReportHistory(
            strategyKey!,
            pageParam,
            HISTORY_LIMIT,
            signal,
          ),
    getNextPageParam: (lastPage) => lastPage.next_before_id ?? undefined,
    enabled: selectedStrategy !== undefined,
    retry: false,
  });
  const report = useQuery({
    queryKey: ["community-report", strategyKey, reportId ?? "latest"],
    queryFn: ({ signal }) =>
      reportId === undefined
        ? api.getLatestCommunityReport(strategyKey!, signal)
        : api.getCommunityReport(reportId, signal),
    enabled: selectedStrategy !== undefined,
    retry: false,
  });

  const setSelection = (changes: { strategy?: string; report?: string }) =>
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (changes.strategy !== undefined) {
        next.set("strategy", changes.strategy);
        next.delete("report");
      }
      if (changes.report !== undefined) {
        if (changes.report === "") next.delete("report");
        else next.set("report", changes.report);
      }
      return next;
    });

  let content;
  if (strategies.isPending) {
    content = <StatusPanel state="loading" />;
  } else if (strategies.isError) {
    content = <StatusPanel state="error" error={strategies.error} onRetry={() => void strategies.refetch()} />;
  } else if (strategyKey !== undefined && selectedStrategy === undefined) {
    content = <div className="status-panel status-panel--error" role="alert"><strong>Unknown strategy</strong><p>The selected community strategy is not configured.</p></div>;
  } else if (report.isPending || strategyKey === undefined) {
    content = <StatusPanel state="loading" />;
  } else if (report.isError) {
    const empty = report.error instanceof RelayApiError && report.error.status === 503;
    content = empty ? (
      <div className="status-panel" role="status"><strong>No report generated</strong><p>This strategy is known but has not published a report yet.</p></div>
    ) : (
      <div className="status-panel status-panel--error" role="alert"><strong>Community report unavailable</strong><p>{errorMessage(report.error)}</p><button className="button" type="button" onClick={() => void report.refetch()}>Retry</button></div>
    );
  } else {
    const item = report.data;
    const partial = item.content.coverage.failed_sources.length > 0;
    const short = item.content.stories.length < item.content.target_story_count;
    content = (
      <>
        {(partial || short) ? (
          <div className="community-warning" role="status">
            {partial ? `${item.content.coverage.failed_sources.length} configured source(s) failed collection. ` : ""}
            {short ? `This report contains ${item.content.stories.length} of ${item.content.target_story_count} target stories.` : ""}
          </div>
        ) : null}
        <section className="community-summary">
          <div><span>Report window</span><strong>{displayDate(item.window_start)} – {displayDate(item.window_end)}</strong></div>
          <div><span>Generated</span><strong>{displayDate(item.generated_at)}</strong></div>
          <div><span>Stories</span><strong>{item.content.stories.length}</strong></div>
          <div><span>Coverage</span><strong>{item.content.coverage.successful_source_count} / {item.content.coverage.configured_source_count} sources</strong></div>
        </section>
        <div className="community-stories">
          {item.content.stories.map((story) => <StoryCard key={story.rank} story={story} />)}
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        eyebrow="Community intelligence"
        title="What the FPL community is discussing."
        description="These are automated summaries of public community discussion, not verified recommendations. Review the linked evidence before acting."
        actions={
          <div className="community-selectors">
            <label className="select-field"><span>Strategy</span><select aria-label="Community strategy" value={strategyKey ?? ""} onChange={(event) => setSelection({ strategy: event.target.value })}>{strategies.data?.map((strategy) => <option key={strategy.key} value={strategy.key}>{strategy.name}</option>)}</select></label>
            <label className="select-field"><span>Report</span><select aria-label="Historical report" value={reportId === undefined ? "" : String(reportId)} onChange={(event) => setSelection({ report: event.target.value })}><option value="">Latest</option>{history.data?.pages.flatMap((page) => page.items).map((item) => <option key={item.id} value={item.id}>{item.report_date} · {item.story_count} stories</option>)}</select></label>
            {history.hasNextPage ? <button className="button" type="button" disabled={history.isFetchingNextPage} onClick={() => void history.fetchNextPage()}>{history.isFetchingNextPage ? "Loading older…" : "Load older reports"}</button> : null}
          </div>
        }
      />
      {content}
    </>
  );
}
