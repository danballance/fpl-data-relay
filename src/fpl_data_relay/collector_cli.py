"""Typer entrypoint for the NAS collection worker."""

import asyncio
import logging
import time
from pathlib import Path
from typing import Annotated, cast

import boto3
import typer

from fpl_data_relay.adapters.outbound.fpl.client import RawFplClient
from fpl_data_relay.collector import CollectorWorker, S3Client, SqsClient, StsClient
from fpl_data_relay.config import load_collector_settings_from_environment

app = typer.Typer(no_args_is_help=True)


@app.command("run")
def run() -> None:
    """Run the long-polling NAS collector until the container stops."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = load_collector_settings_from_environment()
    session = boto3.Session(region_name=settings.aws_region)
    worker = CollectorWorker(
        settings=settings,
        client=RawFplClient(
            base_url=str(settings.fpl.base_url),
            user_agent=settings.fpl.user_agent,
            timeout_seconds=settings.fpl.timeout_seconds,
        ),
        sqs=cast("SqsClient", session.client("sqs")),
        s3=cast("S3Client", session.client("s3")),
        sts=cast("StsClient", session.client("sts")),
    )
    asyncio.run(run_worker(worker=worker))


async def run_worker(*, worker: CollectorWorker) -> None:
    """Run and always close the collector's HTTP resources."""
    try:
        await worker.run()
    finally:
        await worker.close()


@app.command("healthcheck")
def healthcheck(
    *,
    heartbeat_path: Annotated[
        Path,
        typer.Option("--heartbeat-path"),
    ],
    max_age_seconds: Annotated[
        int,
        typer.Option("--max-age-seconds", min=1),
    ],
) -> None:
    """Fail when the collector heartbeat is absent or stale."""
    if not heartbeat_path.is_file():
        raise typer.Exit(code=1)
    age_seconds = time.time() - heartbeat_path.stat().st_mtime
    if age_seconds > max_age_seconds:
        raise typer.Exit(code=1)
