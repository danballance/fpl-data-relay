"""Async connection façade for the Amazon RDS Data API."""

import asyncio
import json
import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import date, datetime
from enum import Enum
from typing import Protocol, TypedDict, cast

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from fpl_data_relay.application.errors import (
    DatabaseUnavailableError,
    DatabaseWakingError,
    SchemaUnavailableError,
)

MAX_RESULT_BYTES = 1_048_576
MAX_ROW_BYTES = 65_536
BATCH_SIZE = 100
PARAMETER_PATTERN = re.compile(r"\$(\d+)")


class DataApiField(TypedDict, total=False):
    """One RDS Data API parameter field."""

    isNull: bool
    booleanValue: bool
    longValue: int
    doubleValue: float
    stringValue: str


class DataApiParameter(TypedDict, total=False):
    """Named parameter supplied to one Data API statement."""

    name: str
    value: DataApiField


class RdsDataClient(Protocol):
    """Boto3 RDS Data client operations used by the façade."""

    def begin_transaction(self, **parameters: object) -> dict[str, object]: ...

    def commit_transaction(self, **parameters: object) -> dict[str, object]: ...

    def rollback_transaction(self, **parameters: object) -> dict[str, object]: ...

    def execute_statement(self, **parameters: object) -> dict[str, object]: ...

    def batch_execute_statement(self, **parameters: object) -> dict[str, object]: ...


class RdsDataTransaction(AbstractAsyncContextManager[object]):
    """Data API transaction bound to one logical connection."""

    def __init__(self, *, connection: RdsDataConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> object:
        await self._connection.begin()
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del traceback
        if exception_type is None:
            await self._connection.commit()
        else:
            await self._connection.rollback()


class RdsDataConnection:
    """Subset of asyncpg-style operations backed by Data API calls."""

    def __init__(
        self,
        *,
        client: RdsDataClient,
        resource_arn: str,
        secret_arn: str,
        database_name: str,
    ) -> None:
        self._client = client
        self._resource_arn = resource_arn
        self._secret_arn = secret_arn
        self._database_name = database_name
        self._transaction_id: str | None = None
        self._queued_sql: str | None = None
        self._queued_parameters: list[list[DataApiParameter]] = []

    def transaction(self) -> AbstractAsyncContextManager[object]:
        return RdsDataTransaction(connection=self)

    async def begin(self) -> None:
        if self._transaction_id is not None:
            raise RuntimeError("Nested Data API transactions are not supported.")
        response = await self._call(
            operation=self._client.begin_transaction,
            parameters=self._base_parameters(),
        )
        transaction_id = response.get("transactionId")
        if not isinstance(transaction_id, str):
            raise DatabaseUnavailableError(
                "RDS Data API did not return a transaction identifier.",
            )
        self._transaction_id = transaction_id

    async def commit(self) -> None:
        if self._transaction_id is None:
            raise RuntimeError("No Data API transaction is active.")
        await self._flush()
        transaction_id = self._transaction_id
        self._transaction_id = None
        await self._call(
            operation=self._client.commit_transaction,
            parameters={
                "resourceArn": self._resource_arn,
                "secretArn": self._secret_arn,
                "transactionId": transaction_id,
            },
        )

    async def rollback(self) -> None:
        if self._transaction_id is None:
            raise RuntimeError("No Data API transaction is active.")
        self._queued_sql = None
        self._queued_parameters = []
        transaction_id = self._transaction_id
        self._transaction_id = None
        await self._call(
            operation=self._client.rollback_transaction,
            parameters={
                "resourceArn": self._resource_arn,
                "secretArn": self._secret_arn,
                "transactionId": transaction_id,
            },
        )

    async def execute(self, query: str, *arguments: object) -> str:
        sql, parameters = data_api_statement(query=query, arguments=arguments)
        should_batch = (
            self._transaction_id is not None
            and "RETURNING" not in sql.upper()
            and sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        )
        if should_batch:
            if self._queued_sql is not None and self._queued_sql != sql:
                await self._flush()
            self._queued_sql = sql
            self._queued_parameters.append(parameters)
            if len(self._queued_parameters) == BATCH_SIZE:
                await self._flush()
            return "QUEUED"
        await self._flush()
        response = await self._execute(sql=sql, parameters=parameters)
        updated = response.get("numberOfRecordsUpdated", 0)
        return f"OK {updated}"

    async def fetchrow(self, query: str, *arguments: object) -> object:
        rows = await self.fetch(query, *arguments)
        return None if not rows else rows[0]

    async def fetch(self, query: str, *arguments: object) -> list[object]:
        await self._flush()
        sql, parameters = data_api_statement(query=query, arguments=arguments)
        response = await self._execute(sql=sql, parameters=parameters)
        formatted = response.get("formattedRecords", "[]")
        if not isinstance(formatted, str):
            raise DatabaseUnavailableError(
                "RDS Data API returned an invalid formattedRecords value.",
            )
        encoded = formatted.encode("utf-8")
        if len(encoded) > MAX_RESULT_BYTES:
            raise DatabaseUnavailableError("RDS Data API result exceeded 1 MiB.")
        decoded = json.loads(formatted)
        if not isinstance(decoded, list):
            raise DatabaseUnavailableError("RDS Data API result was not a row list.")
        rows: list[object] = []
        for raw_row in decoded:
            if not isinstance(raw_row, dict):
                raise DatabaseUnavailableError("RDS Data API returned an invalid row.")
            if len(json.dumps(raw_row).encode("utf-8")) > MAX_ROW_BYTES:
                raise DatabaseUnavailableError("RDS Data API row exceeded 64 KiB.")
            rows.append(raw_row)
        return rows

    async def fetchval(self, query: str, *arguments: object) -> object:
        row = await self.fetchrow(query, *arguments)
        if row is None:
            return None
        mapping = cast("dict[str, object]", row)
        return next(iter(mapping.values()), None)

    @property
    def transaction_active(self) -> bool:
        """Return whether the logical connection owns a transaction."""
        return self._transaction_id is not None

    async def _execute(
        self,
        *,
        sql: str,
        parameters: list[DataApiParameter],
    ) -> dict[str, object]:
        request = self._base_parameters()
        request.update(
            {
                "sql": sql,
                "parameters": parameters,
                "formatRecordsAs": "JSON",
            },
        )
        if self._transaction_id is not None:
            request["transactionId"] = self._transaction_id
        return await self._call(
            operation=self._client.execute_statement,
            parameters=request,
        )

    async def _flush(self) -> None:
        if self._queued_sql is None:
            return
        sql = self._queued_sql
        parameter_sets = self._queued_parameters
        self._queued_sql = None
        self._queued_parameters = []
        for offset in range(0, len(parameter_sets), BATCH_SIZE):
            request = self._base_parameters()
            request.update(
                {
                    "sql": sql,
                    "parameterSets": parameter_sets[offset : offset + BATCH_SIZE],
                },
            )
            if self._transaction_id is not None:
                request["transactionId"] = self._transaction_id
            await self._call(
                operation=self._client.batch_execute_statement,
                parameters=request,
            )

    def _base_parameters(self) -> dict[str, object]:
        return {
            "resourceArn": self._resource_arn,
            "secretArn": self._secret_arn,
            "database": self._database_name,
        }

    async def _call(
        self,
        *,
        operation: Callable[..., dict[str, object]],
        parameters: dict[str, object],
    ) -> dict[str, object]:
        try:
            return await asyncio.to_thread(operation, **parameters)
        except ClientError as error:
            error_details = error.response.get("Error", {})
            code = error_details.get("Code")
            message = str(error_details.get("Message", ""))
            if code == "DatabaseResumingException":
                raise DatabaseWakingError(
                    "Aurora is resuming from its paused state.",
                ) from error
            if (
                code in {"BadRequestException", "DatabaseErrorException"}
                and "relay_schema_migrations" in message
                and "does not exist" in message
            ):
                raise SchemaUnavailableError(
                    "Database migration history is not available.",
                ) from error
            raise DatabaseUnavailableError(
                f"RDS Data API request failed with {code or 'an unknown error'}.",
            ) from error


class RdsDataAcquire(AbstractAsyncContextManager[RdsDataConnection]):
    """Create one logical Data API connection."""

    def __init__(self, *, pool: RdsDataPool) -> None:
        self._pool = pool
        self._connection: RdsDataConnection | None = None

    async def __aenter__(self) -> RdsDataConnection:
        self._connection = RdsDataConnection(
            client=self._pool.client,
            resource_arn=self._pool.resource_arn,
            secret_arn=self._pool.secret_arn,
            database_name=self._pool.database_name,
        )
        return self._connection

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback
        if (
            self._connection is not None
            and self._connection.transaction_active
        ):
            await self._connection.rollback()


class RdsDataPool:
    """Pool-shaped owner for the stateless Data API client."""

    def __init__(
        self,
        *,
        client: RdsDataClient,
        resource_arn: str,
        secret_arn: str,
        database_name: str,
    ) -> None:
        self.client = client
        self.resource_arn = resource_arn
        self.secret_arn = secret_arn
        self.database_name = database_name

    def acquire(self) -> RdsDataAcquire:
        return RdsDataAcquire(pool=self)

    async def close(self) -> None:
        """Data API clients do not own persistent database connections."""


def create_rds_data_pool(
    *,
    resource_arn: str,
    secret_arn: str,
    database_name: str,
) -> RdsDataPool:
    """Create a Data API pool with bounded standard SDK retries."""
    client = cast(
        "RdsDataClient",
        boto3.client(
            "rds-data",
            config=Config(
                retries={"mode": "standard", "total_max_attempts": 3},
            ),
        ),
    )
    return RdsDataPool(
        client=client,
        resource_arn=resource_arn,
        secret_arn=secret_arn,
        database_name=database_name,
    )


def data_api_statement(
    *,
    query: str,
    arguments: tuple[object, ...],
) -> tuple[str, list[DataApiParameter]]:
    """Translate asyncpg positional placeholders and values to Data API form."""
    parameters: list[DataApiParameter] = []
    sql = query
    for index, argument in enumerate(arguments, start=1):
        name = f"p{index}"
        placeholder = f":{name}"
        if isinstance(argument, list):
            if not all(isinstance(value, int) for value in argument):
                raise TypeError("Only integer array parameters are supported.")
            encoded_array = "{" + ",".join(str(value) for value in argument) + "}"
            sql = sql.replace(f"${index}", f"CAST({placeholder} AS integer[])")
            field: DataApiField = {"stringValue": encoded_array}
        else:
            sql = sql.replace(f"${index}", placeholder)
            field = data_api_field(value=argument)
        parameters.append({"name": name, "value": field})
    if PARAMETER_PATTERN.search(sql):
        raise ValueError("SQL contains a placeholder without a matching argument.")
    return sql, parameters


def data_api_field(*, value: object) -> DataApiField:
    """Encode one supported Python scalar as a Data API field."""
    if value is None:
        return {"isNull": True}
    if isinstance(value, Enum):
        return {"stringValue": str(value.value)}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"longValue": value}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, datetime | date):
        return {"stringValue": value.isoformat()}
    if isinstance(value, str):
        return {"stringValue": value}
    raise TypeError(f"Unsupported Data API parameter type: {type(value).__name__}.")
