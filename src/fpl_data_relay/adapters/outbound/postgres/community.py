"""Insert-only PostgreSQL persistence for community reports."""

import json
from collections.abc import Mapping
from datetime import date, datetime
from typing import cast

from fpl_data_relay.adapters.outbound.postgres.database import PostgresDatabase
from fpl_data_relay.domain.community import (
    CommunityReport,
    CommunityReportContent,
    CommunityReportDraft,
    CommunityReportSummary,
)

REPORT_COLUMNS = """
    id,
    strategy_key,
    strategy_version,
    report_date,
    season_id,
    as_of_event_id,
    window_start,
    window_end,
    generated_at,
    content
"""

SUMMARY_COLUMNS = """
    id,
    strategy_key,
    strategy_version,
    report_date,
    season_id,
    as_of_event_id,
    window_start,
    window_end,
    generated_at,
    jsonb_array_length(content -> 'stories') AS story_count,
    CAST(content #>> '{coverage,successful_source_count}' AS integer)
        AS successful_source_count,
    jsonb_array_length(content #> '{coverage,failed_sources}')
        AS failed_source_count
"""


class PostgresCommunityReportRepository:
    """Persist immutable report aggregates and expose bounded history reads."""

    def __init__(self, *, database: PostgresDatabase) -> None:
        self._database = database

    async def insert_report(
        self,
        *,
        report: CommunityReportDraft,
    ) -> CommunityReport:
        query = f"""
            INSERT INTO relay_community_reports (
                strategy_key,
                strategy_version,
                report_date,
                season_id,
                as_of_event_id,
                window_start,
                window_end,
                generated_at,
                content
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
            ON CONFLICT (strategy_key, report_date) DO NOTHING
            RETURNING {REPORT_COLUMNS}
        """
        async with self._database.pool.acquire() as connection:
            row = await connection.fetchrow(
                query,
                report.strategy_key,
                report.strategy_version,
                report.report_date,
                report.season_id,
                report.as_of_event_id,
                report.window_start,
                report.window_end,
                report.generated_at,
                report.content.model_dump_json(),
            )
        if row is not None:
            return _report_from_row(row)
        existing = await self.get_report_for_date(
            strategy_key=report.strategy_key,
            report_date=report.report_date,
        )
        if existing is None:
            raise RuntimeError(
                "Community report insert conflicted but no existing report was found.",
            )
        return existing

    async def get_report(self, *, report_id: int) -> CommunityReport | None:
        return await self._fetch_report(
            query=f"""
                SELECT {REPORT_COLUMNS}
                FROM relay_community_reports
                WHERE id = $1
            """,
            arguments=(report_id,),
        )

    async def get_latest_report(
        self,
        *,
        strategy_key: str,
    ) -> CommunityReport | None:
        return await self._fetch_report(
            query=f"""
                SELECT {REPORT_COLUMNS}
                FROM relay_community_reports
                WHERE strategy_key = $1
                ORDER BY report_date DESC, id DESC
                LIMIT 1
            """,
            arguments=(strategy_key,),
        )

    async def get_report_for_date(
        self,
        *,
        strategy_key: str,
        report_date: date,
    ) -> CommunityReport | None:
        return await self._fetch_report(
            query=f"""
                SELECT {REPORT_COLUMNS}
                FROM relay_community_reports
                WHERE strategy_key = $1 AND report_date = $2
            """,
            arguments=(strategy_key, report_date),
        )

    async def list_recent_reports(
        self,
        *,
        strategy_key: str,
        limit: int,
    ) -> list[CommunityReportSummary]:
        return await self._fetch_summaries(
            query=f"""
                SELECT {SUMMARY_COLUMNS}
                FROM relay_community_reports
                WHERE strategy_key = $1
                ORDER BY id DESC
                LIMIT $2
            """,
            arguments=(strategy_key, limit),
        )

    async def list_reports_before(
        self,
        *,
        strategy_key: str,
        before_id: int,
        limit: int,
    ) -> list[CommunityReportSummary]:
        return await self._fetch_summaries(
            query=f"""
                SELECT {SUMMARY_COLUMNS}
                FROM relay_community_reports
                WHERE strategy_key = $1 AND id < $2
                ORDER BY id DESC
                LIMIT $3
            """,
            arguments=(strategy_key, before_id, limit),
        )

    async def _fetch_report(
        self,
        *,
        query: str,
        arguments: tuple[object, ...],
    ) -> CommunityReport | None:
        async with self._database.pool.acquire() as connection:
            row = await connection.fetchrow(query, *arguments)
        return None if row is None else _report_from_row(row)

    async def _fetch_summaries(
        self,
        *,
        query: str,
        arguments: tuple[object, ...],
    ) -> list[CommunityReportSummary]:
        async with self._database.pool.acquire() as connection:
            rows = await connection.fetch(query, *arguments)
        return [_summary_from_row(row) for row in rows]


def _mapping(row: object) -> Mapping[str, object]:
    return cast("Mapping[str, object]", row)


def _date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError(f"Expected a date value, received {type(value).__name__}.")


def _datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError(f"Expected a datetime value, received {type(value).__name__}.")


def _content(value: object) -> CommunityReportContent:
    if isinstance(value, str):
        return CommunityReportContent.model_validate_json(value)
    return CommunityReportContent.model_validate_json(json.dumps(value))


def _report_from_row(row: object) -> CommunityReport:
    item = _mapping(row)
    event_id = item["as_of_event_id"]
    return CommunityReport(
        id=int(cast("int", item["id"])),
        strategy_key=str(item["strategy_key"]),
        strategy_version=int(cast("int", item["strategy_version"])),
        report_date=_date(item["report_date"]),
        season_id=str(item["season_id"]),
        as_of_event_id=None if event_id is None else int(cast("int", event_id)),
        window_start=_datetime(item["window_start"]),
        window_end=_datetime(item["window_end"]),
        generated_at=_datetime(item["generated_at"]),
        content=_content(item["content"]),
    )


def _summary_from_row(row: object) -> CommunityReportSummary:
    item = _mapping(row)
    event_id = item["as_of_event_id"]
    return CommunityReportSummary(
        id=int(cast("int", item["id"])),
        strategy_key=str(item["strategy_key"]),
        strategy_version=int(cast("int", item["strategy_version"])),
        report_date=_date(item["report_date"]),
        season_id=str(item["season_id"]),
        as_of_event_id=None if event_id is None else int(cast("int", event_id)),
        window_start=_datetime(item["window_start"]),
        window_end=_datetime(item["window_end"]),
        generated_at=_datetime(item["generated_at"]),
        story_count=int(cast("int", item["story_count"])),
        successful_source_count=int(
            cast("int", item["successful_source_count"]),
        ),
        failed_source_count=int(cast("int", item["failed_source_count"])),
    )
