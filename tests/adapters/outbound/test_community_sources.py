import asyncio
import logging
from datetime import UTC, datetime, timedelta

import feedparser
import httpx
import pytest
from pydantic import HttpUrl

from fpl_data_relay.adapters.outbound.community_sources import (
    CommunityHttpSourceGateway,
    TranscriptResult,
    _entry_published,
    _transcript_text,
    _utc_timestamp,
)
from fpl_data_relay.application.errors import CommunitySourceError
from fpl_data_relay.config import CommunityCredentials
from fpl_data_relay.domain.community import (
    BlogSource,
    SourceType,
    XDiscoveredDocument,
    XSource,
    YouTubeSource,
)

NOW = datetime(2026, 8, 13, 6, tzinfo=UTC)


class RecordingRequestPacer:
    def __init__(self) -> None:
        self.wait_count = 0

    async def wait(self) -> None:
        self.wait_count += 1
        await asyncio.sleep(0)


def credentials() -> CommunityCredentials:
    return CommunityCredentials(
        openai_api_key="openai",
        x_bearer_token="x-token",
        youtube_api_key="youtube",
        supadata_api_key="supadata",
    )


def x_source() -> XSource:
    return XSource(
        type=SourceType.X,
        key="x-source",
        label="X Expert",
        user_id="123",
        username="expert",
        include_replies=False,
        include_reposts=False,
        max_documents=150,
        timeout_seconds=5.0,
    )


def youtube_source() -> YouTubeSource:
    return YouTubeSource(
        type=SourceType.YOUTUBE,
        key="youtube-source",
        label="Video Expert",
        channel_id="UC123",
        max_videos=10,
        timeout_seconds=5.0,
        transcript_language="en",
        transcript_mode="native",
        transcript_poll_seconds=0.001,
        transcript_timeout_seconds=1.0,
    )


def blog_source() -> BlogSource:
    return BlogSource(
        type=SourceType.BLOG,
        key="blog-source",
        label="FPL Blog",
        feed_url=HttpUrl("https://feed.example/rss"),
        allowed_article_hosts=["blog.example"],
        max_articles=10,
        timeout_seconds=5.0,
        max_response_bytes=100_000,
    )


@pytest.mark.asyncio
async def test_x_collector_pages_with_window_metrics_and_exclusions() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = request.url.params.get("pagination_token")
        post_id = "2" if page else "1"
        next_token = None if page else "next"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": post_id,
                        "text": f"Post {post_id}",
                        "created_at": (NOW - timedelta(hours=1)).isoformat(),
                        "public_metrics": {
                            "like_count": 10,
                            "reply_count": 2,
                            "retweet_count": 3,
                            "quote_count": 1,
                        },
                    },
                ],
                "meta": {"next_token": next_token},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    pacer = RecordingRequestPacer()
    gateway = CommunityHttpSourceGateway(
        credentials=credentials(),
        client=client,
        supadata_pacer=pacer,
    )
    discovered = await gateway.discover(
        source=x_source(),
        window_start=NOW - timedelta(days=7),
        window_end=NOW,
    )
    assert discovered.documents[0].content_revision != (
        discovered.documents[1].content_revision
    )
    result = await gateway.materialize(
        source=x_source(),
        documents=discovered.documents,
    )
    assert [item.document_id for item in result.documents] == ["x:1", "x:2"]
    assert result.documents[0].engagement_score == 20
    assert requests[0].url.params["exclude"] == "retweets,replies"
    assert requests[0].url.params["start_time"].endswith("Z")
    assert requests[1].url.params["pagination_token"] == "next"
    assert pacer.wait_count == 0
    await gateway.close()


@pytest.mark.asyncio
async def test_x_revision_tracks_text_while_engagement_stays_fresh() -> None:
    text = ["First version"]
    likes = [1]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "1",
                        "text": text[0],
                        "created_at": (NOW - timedelta(hours=1)).isoformat(),
                        "public_metrics": {
                            "like_count": likes[0],
                            "reply_count": 0,
                            "retweet_count": 0,
                            "quote_count": 0,
                        },
                    },
                ],
                "meta": {},
            },
            request=request,
        )

    gateway = CommunityHttpSourceGateway(
        credentials=credentials(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        supadata_pacer=RecordingRequestPacer(),
    )
    first = await gateway.discover(
        source=x_source(),
        window_start=NOW - timedelta(days=7),
        window_end=NOW,
    )
    text[0] = "Edited version"
    likes[0] = 9
    second = await gateway.discover(
        source=x_source(),
        window_start=NOW - timedelta(days=7),
        window_end=NOW,
    )

    assert first.documents[0].document_id == second.documents[0].document_id
    assert first.documents[0].content_revision != (
        second.documents[0].content_revision
    )
    assert isinstance(second.documents[0], XDiscoveredDocument)
    assert second.documents[0].engagement.likes == 9
    await gateway.close()


@pytest.mark.asyncio
async def test_youtube_uses_native_transcripts_and_excludes_unavailable() -> None:
    transcript_modes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.googleapis.com" and request.url.path.endswith(
            "/search",
        ):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"id": {"videoId": "available"}},
                        {"id": {"videoId": "missing"}},
                    ],
                },
            )
        if request.url.host == "www.googleapis.com":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": video_id,
                            "snippet": {
                                "channelTitle": "Video Expert",
                                "title": video_id,
                                "publishedAt": (NOW - timedelta(hours=2)).isoformat(),
                            },
                            "statistics": {
                                "viewCount": "100",
                                "likeCount": "5",
                                "commentCount": "2",
                            },
                        }
                        for video_id in ("available", "missing")
                    ],
                },
            )
        transcript_modes.append(request.url.params["mode"])
        if "missing" in request.url.params["url"]:
            return httpx.Response(404, json={"error": "not-found"})
        return httpx.Response(
            200,
            json={"content": "Native transcript", "lang": "en"},
        )

    pacer = RecordingRequestPacer()
    gateway = CommunityHttpSourceGateway(
        credentials=credentials(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        supadata_pacer=pacer,
    )
    discovered = await gateway.discover(
        source=youtube_source(),
        window_start=NOW - timedelta(days=7),
        window_end=NOW,
    )
    assert transcript_modes == []
    assert pacer.wait_count == 0
    result = await gateway.materialize(
        source=youtube_source(),
        documents=discovered.documents,
    )
    assert len(result.documents) == 1
    assert result.documents[0].text == "Native transcript"
    assert result.documents[0].engagement_score == 190
    assert result.excluded_document_count == 1
    assert result.exclusions[0].code == "youtube_native_caption_unavailable"
    assert transcript_modes == ["native", "native"]
    retried = await gateway.materialize(
        source=youtube_source(),
        documents=discovered.documents,
    )
    assert retried.excluded_document_count == 1
    assert transcript_modes == ["native", "native", "native", "native"]
    assert pacer.wait_count == 4
    await gateway.close()


@pytest.mark.asyncio
async def test_supadata_async_job_is_polled_to_completion() -> None:
    polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal polls
        if request.url.host == "www.googleapis.com" and request.url.path.endswith(
            "/search",
        ):
            return httpx.Response(200, json={"items": [{"id": {"videoId": "v1"}}]})
        if request.url.host == "www.googleapis.com":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "v1",
                            "snippet": {
                                "channelTitle": "Channel",
                                "title": "Video",
                                "publishedAt": (NOW - timedelta(hours=1)).isoformat(),
                            },
                            "statistics": {"viewCount": "1"},
                        },
                    ],
                },
            )
        if request.url.path.endswith("/transcript"):
            return httpx.Response(202, json={"jobId": "job-1"})
        polls += 1
        if polls == 1:
            return httpx.Response(200, json={"status": "active"})
        return httpx.Response(
            200,
            json={
                "content": [{"text": "one"}, {"text": "two"}],
                "lang": "en",
            },
        )

    pacer = RecordingRequestPacer()
    gateway = CommunityHttpSourceGateway(
        credentials=credentials(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        supadata_pacer=pacer,
    )
    discovered = await gateway.discover(
        source=youtube_source(),
        window_start=NOW - timedelta(days=7),
        window_end=NOW,
    )
    result = await gateway.materialize(
        source=youtube_source(),
        documents=discovered.documents,
    )
    assert result.documents[0].text == "one two"
    assert polls == 2
    assert pacer.wait_count == 3
    await gateway.close()


@pytest.mark.asyncio
async def test_shared_supadata_pacer_covers_concurrent_youtube_sources() -> None:
    transcript_video_ids: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        channel_id = request.url.params.get("channelId", "")
        video_id = f"video-{channel_id}"
        if request.url.host == "www.googleapis.com" and request.url.path.endswith(
            "/search",
        ):
            return httpx.Response(
                200,
                json={"items": [{"id": {"videoId": video_id}}]},
            )
        if request.url.host == "www.googleapis.com":
            requested_id = request.url.params["id"]
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": requested_id,
                            "snippet": {
                                "channelTitle": requested_id,
                                "title": requested_id,
                                "publishedAt": (NOW - timedelta(hours=1)).isoformat(),
                            },
                            "statistics": {"viewCount": "1"},
                        },
                    ],
                },
            )
        transcript_video_ids.append(request.url.params["url"])
        return httpx.Response(200, json={"content": "Transcript", "lang": "en"})

    first_source = youtube_source()
    second_source = YouTubeSource.model_validate(
        {
            **first_source.model_dump(),
            "key": "youtube-source-two",
            "channel_id": "UC456",
        },
    )
    pacer = RecordingRequestPacer()
    gateway = CommunityHttpSourceGateway(
        credentials=credentials(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        supadata_pacer=pacer,
    )
    first_discovery, second_discovery = await asyncio.gather(
        gateway.discover(
            source=first_source,
            window_start=NOW - timedelta(days=7),
            window_end=NOW,
        ),
        gateway.discover(
            source=second_source,
            window_start=NOW - timedelta(days=7),
            window_end=NOW,
        ),
    )

    await asyncio.gather(
        gateway.materialize(
            source=first_source,
            documents=first_discovery.documents,
        ),
        gateway.materialize(
            source=second_source,
            documents=second_discovery.documents,
        ),
    )

    assert pacer.wait_count == 2
    assert len(transcript_video_ids) == 2
    await gateway.close()


@pytest.mark.asyncio
async def test_fatal_transcript_failure_cancels_the_video_batch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.googleapis.com" and request.url.path.endswith(
            "/search",
        ):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"id": {"videoId": "one"}},
                        {"id": {"videoId": "two"}},
                    ],
                },
            )
        if request.url.host == "www.googleapis.com":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": video_id,
                            "snippet": {
                                "channelTitle": "Channel",
                                "title": video_id,
                                "publishedAt": (NOW - timedelta(hours=1)).isoformat(),
                            },
                            "statistics": {"viewCount": "1"},
                        }
                        for video_id in ("one", "two")
                    ],
                },
            )
        return httpx.Response(401, json={"error": "invalid key"})

    gateway = CommunityHttpSourceGateway(
        credentials=credentials(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        supadata_pacer=RecordingRequestPacer(),
    )
    with pytest.raises(CommunitySourceError) as raised:
        discovered = await gateway.discover(
            source=youtube_source(),
            window_start=NOW - timedelta(days=7),
            window_end=NOW,
        )
        await gateway.materialize(
            source=youtube_source(),
            documents=discovered.documents,
        )
    assert raised.value.code == "supadata_authentication"
    assert raised.value.fatal is True
    await gateway.close()


@pytest.mark.asyncio
async def test_blog_collector_parses_feed_redirect_and_main_text() -> None:
    feed = f"""<?xml version="1.0"?><rss version="2.0"><channel>
      <title>FPL</title><link>https://blog.example</link><description>FPL</description>
      <item><title>Transfer article</title><link>https://blog.example/go</link>
      <guid>article-1</guid>
      <pubDate>{NOW.strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate>
      </item></channel></rss>"""
    article = "<html><body><article><h1>Transfer article</h1>" + "".join(
        f"<p>Paragraph {index} discusses an FPL player and the coming "
        "gameweek in detail.</p>"
        for index in range(20)
    ) + "</article></body></html>"

    article_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal article_requests
        if request.url.host == "feed.example":
            return httpx.Response(200, text=feed)
        article_requests += 1
        if request.url.path == "/go":
            return httpx.Response(302, headers={"Location": "/article"})
        return httpx.Response(200, text=article)

    pacer = RecordingRequestPacer()
    gateway = CommunityHttpSourceGateway(
        credentials=credentials(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        supadata_pacer=pacer,
    )
    discovered = await gateway.discover(
        source=blog_source(),
        window_start=NOW - timedelta(days=7),
        window_end=NOW + timedelta(seconds=1),
    )
    assert article_requests == 0
    result = await gateway.materialize(
        source=blog_source(),
        documents=discovered.documents,
    )
    assert len(result.documents) == 1
    assert "Paragraph 1" in result.documents[0].text
    assert str(result.documents[0].url).endswith("/article")
    assert pacer.wait_count == 0
    await gateway.close()


@pytest.mark.asyncio
async def test_blog_revision_tracks_feed_entry_updated_time() -> None:
    updated = [NOW - timedelta(hours=1)]

    def feed() -> str:
        return f"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
          <title>FPL</title><id>https://feed.example/</id>
          <updated>{updated[0].isoformat()}</updated>
          <entry><title>Transfer article</title><id>article-1</id>
          <link href="https://blog.example/article" />
          <published>{(NOW - timedelta(hours=2)).isoformat()}</published>
          <updated>{updated[0].isoformat()}</updated>
          <summary>Discussion</summary></entry></feed>"""

    gateway = CommunityHttpSourceGateway(
        credentials=credentials(),
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    text=feed(),
                    request=request,
                ),
            ),
        ),
        supadata_pacer=RecordingRequestPacer(),
    )
    first = await gateway.discover(
        source=blog_source(),
        window_start=NOW - timedelta(days=7),
        window_end=NOW + timedelta(seconds=1),
    )
    updated[0] = NOW
    second = await gateway.discover(
        source=blog_source(),
        window_start=NOW - timedelta(days=7),
        window_end=NOW + timedelta(seconds=1),
    )

    assert first.documents[0].document_id == second.documents[0].document_id
    assert first.documents[0].content_revision != (
        second.documents[0].content_revision
    )
    await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429])
async def test_systemic_provider_statuses_are_fatal(status: int) -> None:
    gateway = CommunityHttpSourceGateway(
        credentials=credentials(),
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status, request=request),
            ),
        ),
        supadata_pacer=RecordingRequestPacer(),
    )
    with pytest.raises(CommunitySourceError) as raised:
        await gateway.discover(
            source=x_source(),
            window_start=NOW - timedelta(days=7),
            window_end=NOW,
        )
    assert raised.value.fatal is True
    await gateway.close()


@pytest.mark.asyncio
async def test_supadata_rate_limit_logs_only_allowlisted_diagnostics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.googleapis.com" and request.url.path.endswith(
            "/search",
        ):
            return httpx.Response(200, json={"items": [{"id": {"videoId": "v1"}}]})
        if request.url.host == "www.googleapis.com":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "v1",
                            "snippet": {
                                "channelTitle": "Channel",
                                "title": "Video",
                                "publishedAt": (NOW - timedelta(hours=1)).isoformat(),
                            },
                            "statistics": {"viewCount": "1"},
                        },
                    ],
                },
            )
        return httpx.Response(
            429,
            headers={
                "Retry-After": "2",
                "X-RateLimit-Limit": "10",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "12345",
                "X-Private-Diagnostic": "do-not-log-header",
            },
            text="do-not-log-body",
        )

    caplog.set_level(
        logging.ERROR,
        logger="fpl_data_relay.adapters.outbound.community_sources",
    )
    gateway = CommunityHttpSourceGateway(
        credentials=credentials(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        supadata_pacer=RecordingRequestPacer(),
    )
    discovered = await gateway.discover(
        source=youtube_source(),
        window_start=NOW - timedelta(days=7),
        window_end=NOW,
    )

    with pytest.raises(CommunitySourceError) as raised:
        await gateway.materialize(
            source=youtube_source(),
            documents=discovered.documents,
        )

    assert raised.value.code == "supadata_rate_limit"
    record = next(
        item
        for item in caplog.records
        if item.message == "community_provider_rate_limited"
    )
    assert record.__dict__["provider"] == "supadata"
    assert record.__dict__["retry_after"] == "2"
    assert record.__dict__["rate_limit_limit"] == "10"
    assert record.__dict__["rate_limit_remaining"] == "0"
    assert record.__dict__["rate_limit_reset"] == "12345"
    serialized_record = str(record.__dict__)
    assert "do-not-log-header" not in serialized_record
    assert "do-not-log-body" not in serialized_record
    assert "x-api-key" not in serialized_record.lower()
    await gateway.close()


@pytest.mark.asyncio
async def test_network_and_blog_policy_failures_are_stable_best_effort() -> None:
    def network_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    gateway = CommunityHttpSourceGateway(
        credentials=credentials(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(network_error)),
        supadata_pacer=RecordingRequestPacer(),
    )
    with pytest.raises(CommunitySourceError) as raised:
        await gateway.discover(
            source=x_source(),
            window_start=NOW - timedelta(days=7),
            window_end=NOW,
        )
    assert raised.value.code == "x_fetch"
    assert raised.value.fatal is False
    await gateway.close()


@pytest.mark.asyncio
async def test_malformed_provider_content_has_a_stable_parse_code() -> None:
    gateway = CommunityHttpSourceGateway(
        credentials=credentials(),
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"data": [{"id": "missing-required-fields"}]},
                    request=request,
                ),
            ),
        ),
        supadata_pacer=RecordingRequestPacer(),
    )
    with pytest.raises(CommunitySourceError) as raised:
        await gateway.discover(
            source=x_source(),
            window_start=NOW - timedelta(days=7),
            window_end=NOW,
        )
    assert raised.value.code == "x_parse"
    assert raised.value.fatal is False
    await gateway.close()


def test_source_helpers_are_explicit_and_timezone_aware() -> None:
    assert _utc_timestamp(NOW) == "2026-08-13T06:00:00Z"
    assert _entry_published(entry=feedparser.FeedParserDict()) is None
    assert _transcript_text(
        result=TranscriptResult(content="text", lang="en"),
    ) == "text"
