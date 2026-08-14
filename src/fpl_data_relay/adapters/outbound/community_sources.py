"""Official API and feed-backed community source collectors."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from time import struct_time
from typing import Annotated, Literal
from urllib.parse import urljoin, urlparse

import feedparser
import httpx
import trafilatura
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    TypeAdapter,
    ValidationError,
)

from fpl_data_relay.application.errors import CommunitySourceError
from fpl_data_relay.config import CommunityCredentials
from fpl_data_relay.domain.community import (
    BlogEngagement,
    BlogSource,
    CommunitySource,
    SourceCollectionResult,
    SourceDocument,
    SourceExclusion,
    XEngagement,
    XSource,
    YouTubeEngagement,
    YouTubeSource,
)

X_API_BASE_URL = "https://api.x.com/2"
YOUTUBE_API_BASE_URL = "https://www.googleapis.com/youtube/v3"
SUPADATA_API_BASE_URL = "https://api.supadata.ai/v1"
MAX_REDIRECTS = 5


class ExternalModel(BaseModel):
    """Typed provider payload that ignores documented additive fields."""

    model_config = ConfigDict(extra="ignore")


class XMetrics(ExternalModel):
    like_count: int = Field(ge=0)
    reply_count: int = Field(ge=0)
    retweet_count: int = Field(ge=0)
    quote_count: int = Field(ge=0)


class XPost(ExternalModel):
    id: str
    text: str
    created_at: datetime
    public_metrics: XMetrics


class XMeta(ExternalModel):
    next_token: str | None = None


class XTimelinePage(ExternalModel):
    data: list[XPost] = Field(default_factory=list)
    meta: XMeta


class YouTubeVideoId(ExternalModel):
    videoId: str


class YouTubeSearchItem(ExternalModel):
    id: YouTubeVideoId


class YouTubeSearchPage(ExternalModel):
    items: list[YouTubeSearchItem]
    nextPageToken: str | None = None


class YouTubeSnippet(ExternalModel):
    channelTitle: str
    title: str
    publishedAt: datetime


class YouTubeStatistics(ExternalModel):
    viewCount: str
    likeCount: str | None = None
    commentCount: str | None = None


class YouTubeVideo(ExternalModel):
    id: str
    snippet: YouTubeSnippet
    statistics: YouTubeStatistics


class YouTubeVideoPage(ExternalModel):
    items: list[YouTubeVideo]


class TranscriptSegment(ExternalModel):
    text: str


class TranscriptResult(ExternalModel):
    content: str | list[TranscriptSegment]
    lang: str


class TranscriptJob(ExternalModel):
    jobId: str


class TranscriptPending(ExternalModel):
    status: Literal["queued", "active"]


TRANSCRIPT_RESPONSE = TypeAdapter(
    Annotated[
        TranscriptResult | TranscriptJob | TranscriptPending,
        Field(union_mode="left_to_right"),
    ],
)


class CommunityHttpSourceGateway:
    """Dispatch configured X, YouTube, and blog sources to strict collectors."""

    def __init__(
        self,
        *,
        credentials: CommunityCredentials,
        client: httpx.AsyncClient,
    ) -> None:
        self._credentials = credentials
        self._client = client

    async def close(self) -> None:
        await self._client.aclose()

    async def collect(
        self,
        *,
        source: CommunitySource,
        window_start: datetime,
        window_end: datetime,
    ) -> SourceCollectionResult:
        try:
            if isinstance(source, XSource):
                return await self._collect_x(
                    source=source,
                    window_start=window_start,
                    window_end=window_end,
                )
            if isinstance(source, YouTubeSource):
                return await self._collect_youtube(
                    source=source,
                    window_start=window_start,
                    window_end=window_end,
                )
            return await self._collect_blog(
                source=source,
                window_start=window_start,
                window_end=window_end,
            )
        except CommunitySourceError:
            raise
        except (ValidationError, ValueError, TypeError) as exception:
            raise CommunitySourceError(
                code=f"{source.type.value}_parse",
                fatal=False,
                detail=f"{source.type.value} returned malformed content.",
            ) from exception

    async def _collect_x(
        self,
        *,
        source: XSource,
        window_start: datetime,
        window_end: datetime,
    ) -> SourceCollectionResult:
        documents: list[SourceDocument] = []
        pagination_token: str | None = None
        while len(documents) < source.max_documents:
            exclusions: list[str] = []
            if not source.include_reposts:
                exclusions.append("retweets")
            if not source.include_replies:
                exclusions.append("replies")
            params = {
                "start_time": _utc_timestamp(window_start),
                "end_time": _utc_timestamp(window_end),
                "max_results": str(
                    max(5, min(100, source.max_documents - len(documents)))
                ),
                "tweet.fields": "created_at,public_metrics",
            }
            if exclusions:
                params["exclude"] = ",".join(exclusions)
            if pagination_token is not None:
                params["pagination_token"] = pagination_token
            response = await self._request(
                method="GET",
                url=f"{X_API_BASE_URL}/users/{source.user_id}/tweets",
                params=params,
                headers={
                    "Authorization": f"Bearer {self._credentials.x_bearer_token}",
                },
                timeout_seconds=source.timeout_seconds,
                provider="x",
                allow_redirect=False,
            )
            page = XTimelinePage.model_validate(response.json())
            remaining = source.max_documents - len(documents)
            documents.extend(
                SourceDocument(
                    document_id=f"x:{post.id}",
                    source_key=source.key,
                    source_type=source.type,
                    external_id=post.id,
                    publisher=source.label,
                    title=f"Post by @{source.username}",
                    url=HttpUrl(
                        f"https://x.com/{source.username}/status/{post.id}",
                    ),
                    published_at=post.created_at,
                    text=post.text,
                    engagement=XEngagement(
                        type=source.type,
                        likes=post.public_metrics.like_count,
                        replies=post.public_metrics.reply_count,
                        reposts=post.public_metrics.retweet_count,
                        quotes=post.public_metrics.quote_count,
                    ),
                )
                for post in page.data[:remaining]
            )
            pagination_token = page.meta.next_token
            if pagination_token is None:
                break
        return SourceCollectionResult(
            source_key=source.key,
            documents=documents[: source.max_documents],
            excluded_document_count=0,
            exclusions=[],
        )

    async def _collect_youtube(
        self,
        *,
        source: YouTubeSource,
        window_start: datetime,
        window_end: datetime,
    ) -> SourceCollectionResult:
        video_ids: list[str] = []
        page_token: str | None = None
        while len(video_ids) < source.max_videos:
            params = {
                "part": "snippet",
                "channelId": source.channel_id,
                "type": "video",
                "order": "date",
                "publishedAfter": _utc_timestamp(
                    window_start - timedelta(seconds=1),
                ),
                "publishedBefore": _utc_timestamp(window_end),
                "maxResults": str(min(50, source.max_videos - len(video_ids))),
                "key": self._credentials.youtube_api_key,
            }
            if page_token is not None:
                params["pageToken"] = page_token
            response = await self._request(
                method="GET",
                url=f"{YOUTUBE_API_BASE_URL}/search",
                params=params,
                headers={},
                timeout_seconds=source.timeout_seconds,
                provider="youtube",
                allow_redirect=False,
            )
            page = YouTubeSearchPage.model_validate(response.json())
            video_ids.extend(item.id.videoId for item in page.items)
            page_token = page.nextPageToken
            if page_token is None:
                break
        if not video_ids:
            return SourceCollectionResult(
                source_key=source.key,
                documents=[],
                excluded_document_count=0,
                exclusions=[],
            )
        metadata_response = await self._request(
            method="GET",
            url=f"{YOUTUBE_API_BASE_URL}/videos",
            params={
                "part": "snippet,statistics",
                "id": ",".join(video_ids),
                "key": self._credentials.youtube_api_key,
            },
            headers={},
            timeout_seconds=source.timeout_seconds,
            provider="youtube",
            allow_redirect=False,
        )
        metadata = YouTubeVideoPage.model_validate(metadata_response.json())
        videos = [
            item
            for item in metadata.items
            if window_start <= item.snippet.publishedAt < window_end
        ]
        outside_window = len(metadata.items) - len(videos)
        transcript_tasks = [
            asyncio.create_task(
                self._transcript(source=source, video_id=item.id),
            )
            for item in videos
        ]
        try:
            transcript_results = await asyncio.gather(*transcript_tasks)
        except BaseException:
            for task in transcript_tasks:
                task.cancel()
            await asyncio.gather(*transcript_tasks, return_exceptions=True)
            raise
        documents: list[SourceDocument] = []
        unavailable_captions = 0
        for video, transcript in zip(videos, transcript_results, strict=True):
            if transcript is None:
                unavailable_captions += 1
                continue
            documents.append(
                SourceDocument(
                    document_id=f"youtube:{video.id}",
                    source_key=source.key,
                    source_type=source.type,
                    external_id=video.id,
                    publisher=video.snippet.channelTitle,
                    title=video.snippet.title,
                    url=HttpUrl(
                        f"https://www.youtube.com/watch?v={video.id}",
                    ),
                    published_at=video.snippet.publishedAt,
                    text=transcript,
                    engagement=YouTubeEngagement(
                        type=source.type,
                        views=int(video.statistics.viewCount),
                        likes=int(video.statistics.likeCount or "0"),
                        comments=int(video.statistics.commentCount or "0"),
                    ),
                ),
            )
        return SourceCollectionResult(
            source_key=source.key,
            documents=documents,
            excluded_document_count=outside_window + unavailable_captions,
            exclusions=[
                SourceExclusion(
                    source_key=source.key,
                    source_type=source.type,
                    code=code,
                    count=count,
                )
                for code, count in (
                    ("youtube_outside_window", outside_window),
                    (
                        "youtube_native_caption_unavailable",
                        unavailable_captions,
                    ),
                )
                if count > 0
            ],
        )

    async def _transcript(
        self,
        *,
        source: YouTubeSource,
        video_id: str,
    ) -> str | None:
        response = await self._supadata_request(
            url=f"{SUPADATA_API_BASE_URL}/transcript",
            params={
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "lang": source.transcript_language,
                "text": "true",
                "mode": source.transcript_mode,
            },
            timeout_seconds=source.timeout_seconds,
        )
        if response.status_code == 404:
            return None
        _raise_provider_status(response=response, provider="supadata")
        payload = TRANSCRIPT_RESPONSE.validate_python(response.json())
        if isinstance(payload, TranscriptResult):
            return _transcript_text(result=payload)
        if not isinstance(payload, TranscriptJob):
            raise CommunitySourceError(
                code="supadata_invalid_response",
                fatal=False,
                detail="Supadata returned a pending response without a job ID.",
            )
        deadline = asyncio.get_running_loop().time() + source.transcript_timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(source.transcript_poll_seconds)
            result_response = await self._supadata_request(
                url=f"{SUPADATA_API_BASE_URL}/transcript/{payload.jobId}",
                params={},
                timeout_seconds=source.timeout_seconds,
            )
            if result_response.status_code == 404:
                return None
            _raise_provider_status(response=result_response, provider="supadata")
            result = TRANSCRIPT_RESPONSE.validate_python(result_response.json())
            if isinstance(result, TranscriptResult):
                return _transcript_text(result=result)
        raise CommunitySourceError(
            code="supadata_timeout",
            fatal=False,
            detail="Supadata transcript job exceeded its configured timeout.",
        )

    async def _collect_blog(
        self,
        *,
        source: BlogSource,
        window_start: datetime,
        window_end: datetime,
    ) -> SourceCollectionResult:
        response = await self._request(
            method="GET",
            url=str(source.feed_url),
            params={},
            headers={},
            timeout_seconds=source.timeout_seconds,
            provider="blog",
            allow_redirect=False,
        )
        _ensure_size(response=response, maximum=source.max_response_bytes)
        feed = feedparser.parse(response.content)
        if feed.bozo:
            raise CommunitySourceError(
                code="blog_feed_parse",
                fatal=False,
                detail="The configured feed could not be parsed.",
            )
        documents: list[SourceDocument] = []
        exclusion_counts: dict[str, int] = {}

        def exclude(*, code: str) -> None:
            exclusion_counts[code] = exclusion_counts.get(code, 0) + 1

        for entry in feed.entries:
            published = _entry_published(entry=entry)
            if published is None or not window_start <= published < window_end:
                exclude(code="blog_outside_window_or_undated")
                continue
            if len(documents) >= source.max_articles:
                exclude(code="blog_source_limit")
                continue
            link = str(entry.get("link", ""))
            title = str(entry.get("title", ""))
            if not link or not title:
                exclude(code="blog_entry_incomplete")
                continue
            try:
                article_response = await self._get_allowed_article(
                    url=link,
                    allowed_hosts=set(source.allowed_article_hosts),
                    timeout_seconds=source.timeout_seconds,
                    maximum_bytes=source.max_response_bytes,
                )
                extracted = trafilatura.extract(
                    article_response.text,
                    include_comments=False,
                    include_tables=False,
                    fast=True,
                    url=str(article_response.url),
                )
            except (CommunitySourceError, TypeError, ValueError) as exception:
                if not isinstance(exception, CommunitySourceError):
                    exclude(code="blog_content_parse")
                    continue
                if exception.fatal:
                    raise
                exclude(code=exception.code)
                continue
            if extracted is None or not extracted.strip():
                exclude(code="blog_content_empty")
                continue
            external_id = str(entry.get("id") or link)
            digest = hashlib.sha256(external_id.encode("utf-8")).hexdigest()[:24]
            documents.append(
                SourceDocument(
                    document_id=f"blog:{digest}",
                    source_key=source.key,
                    source_type=source.type,
                    external_id=external_id,
                    publisher=source.label,
                    title=title,
                    url=HttpUrl(str(article_response.url)),
                    published_at=published,
                    text=extracted.strip(),
                    engagement=BlogEngagement(type=source.type),
                ),
            )
        return SourceCollectionResult(
            source_key=source.key,
            documents=documents,
            excluded_document_count=sum(exclusion_counts.values()),
            exclusions=[
                SourceExclusion(
                    source_key=source.key,
                    source_type=source.type,
                    code=code,
                    count=count,
                )
                for code, count in sorted(exclusion_counts.items())
            ],
        )

    async def _get_allowed_article(
        self,
        *,
        url: str,
        allowed_hosts: set[str],
        timeout_seconds: float,
        maximum_bytes: int,
    ) -> httpx.Response:
        current_url = url
        for _ in range(MAX_REDIRECTS + 1):
            host = (urlparse(current_url).hostname or "").lower()
            if host not in allowed_hosts:
                raise CommunitySourceError(
                    code="blog_host_not_allowed",
                    fatal=False,
                    detail=f"Article host {host!r} is not allow-listed.",
                )
            response = await self._request(
                method="GET",
                url=current_url,
                params={},
                headers={},
                timeout_seconds=timeout_seconds,
                provider="blog",
                allow_redirect=True,
            )
            if response.is_redirect:
                location = response.headers.get("location")
                if location is None:
                    raise CommunitySourceError(
                        code="blog_redirect_invalid",
                        fatal=False,
                        detail="Article redirect omitted Location.",
                    )
                current_url = urljoin(current_url, location)
                continue
            _ensure_size(response=response, maximum=maximum_bytes)
            return response
        raise CommunitySourceError(
            code="blog_redirect_limit",
            fatal=False,
            detail="Article exceeded the configured redirect limit.",
        )

    async def _supadata_request(
        self,
        *,
        url: str,
        params: dict[str, str],
        timeout_seconds: float,
    ) -> httpx.Response:
        try:
            return await self._client.get(
                url,
                params=params,
                headers={"x-api-key": self._credentials.supadata_api_key},
                timeout=timeout_seconds,
            )
        except httpx.HTTPError as exception:
            raise CommunitySourceError(
                code="supadata_fetch",
                fatal=False,
                detail="supadata request failed.",
            ) from exception

    async def _request(
        self,
        *,
        method: Literal["GET"],
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
        timeout_seconds: float,
        provider: str,
        allow_redirect: bool,
    ) -> httpx.Response:
        try:
            response = await self._client.request(
                method,
                url,
                params=params,
                headers=headers,
                timeout=timeout_seconds,
                follow_redirects=False,
            )
        except httpx.HTTPError as exception:
            raise CommunitySourceError(
                code=f"{provider}_fetch",
                fatal=False,
                detail=f"{provider} request failed.",
            ) from exception
        if allow_redirect and response.is_redirect:
            return response
        _raise_provider_status(response=response, provider=provider)
        return response


def _raise_provider_status(*, response: httpx.Response, provider: str) -> None:
    if response.status_code in {401, 403}:
        raise CommunitySourceError(
            code=f"{provider}_authentication",
            fatal=True,
            detail=f"{provider} authentication failed.",
        )
    if response.status_code == 429:
        raise CommunitySourceError(
            code=f"{provider}_rate_limit",
            fatal=True,
            detail=f"{provider} rate limit was reached.",
        )
    if response.is_error:
        raise CommunitySourceError(
            code=f"{provider}_response",
            fatal=False,
            detail=f"{provider} returned HTTP {response.status_code}.",
        )


def _ensure_size(*, response: httpx.Response, maximum: int) -> None:
    if len(response.content) > maximum:
        raise CommunitySourceError(
            code="blog_response_too_large",
            fatal=False,
            detail="Feed or article exceeded the configured response-size limit.",
        )


def _entry_published(*, entry: feedparser.FeedParserDict) -> datetime | None:
    value = entry.get("published_parsed") or entry.get("updated_parsed")
    if not isinstance(value, struct_time):
        return None
    return datetime(*value[:6], tzinfo=UTC)


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _transcript_text(*, result: TranscriptResult) -> str:
    if isinstance(result.content, str):
        return result.content
    return " ".join(segment.text for segment in result.content)
