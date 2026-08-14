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
    XSource,
    YouTubeSource,
)

NOW = datetime(2026, 8, 13, 6, tzinfo=UTC)


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
    gateway = CommunityHttpSourceGateway(credentials=credentials(), client=client)
    result = await gateway.collect(
        source=x_source(),
        window_start=NOW - timedelta(days=7),
        window_end=NOW,
    )
    assert [item.document_id for item in result.documents] == ["x:1", "x:2"]
    assert result.documents[0].engagement_score == 20
    assert requests[0].url.params["exclude"] == "retweets,replies"
    assert requests[0].url.params["start_time"].endswith("Z")
    assert requests[1].url.params["pagination_token"] == "next"
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

    gateway = CommunityHttpSourceGateway(
        credentials=credentials(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = await gateway.collect(
        source=youtube_source(),
        window_start=NOW - timedelta(days=7),
        window_end=NOW,
    )
    assert len(result.documents) == 1
    assert result.documents[0].text == "Native transcript"
    assert result.documents[0].engagement_score == 190
    assert result.excluded_document_count == 1
    assert result.exclusions[0].code == "youtube_native_caption_unavailable"
    assert transcript_modes == ["native", "native"]
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

    gateway = CommunityHttpSourceGateway(
        credentials=credentials(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = await gateway.collect(
        source=youtube_source(),
        window_start=NOW - timedelta(days=7),
        window_end=NOW,
    )
    assert result.documents[0].text == "one two"
    assert polls == 2
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
    )
    with pytest.raises(CommunitySourceError) as raised:
        await gateway.collect(
            source=youtube_source(),
            window_start=NOW - timedelta(days=7),
            window_end=NOW,
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

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "feed.example":
            return httpx.Response(200, text=feed)
        if request.url.path == "/go":
            return httpx.Response(302, headers={"Location": "/article"})
        return httpx.Response(200, text=article)

    gateway = CommunityHttpSourceGateway(
        credentials=credentials(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = await gateway.collect(
        source=blog_source(),
        window_start=NOW - timedelta(days=7),
        window_end=NOW + timedelta(seconds=1),
    )
    assert len(result.documents) == 1
    assert "Paragraph 1" in result.documents[0].text
    assert str(result.documents[0].url).endswith("/article")
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
    )
    with pytest.raises(CommunitySourceError) as raised:
        await gateway.collect(
            source=x_source(),
            window_start=NOW - timedelta(days=7),
            window_end=NOW,
        )
    assert raised.value.fatal is True
    await gateway.close()


@pytest.mark.asyncio
async def test_network_and_blog_policy_failures_are_stable_best_effort() -> None:
    def network_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    gateway = CommunityHttpSourceGateway(
        credentials=credentials(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(network_error)),
    )
    with pytest.raises(CommunitySourceError) as raised:
        await gateway.collect(
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
    )
    with pytest.raises(CommunitySourceError) as raised:
        await gateway.collect(
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
