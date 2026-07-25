from datetime import UTC, date, datetime
from enum import Enum
from typing import cast

import pytest
from botocore.exceptions import ClientError

from fpl_data_relay.adapters.outbound import rds_data
from fpl_data_relay.adapters.outbound.rds_data import (
    RdsDataConnection,
    RdsDataPool,
    data_api_field,
    data_api_statement,
)
from fpl_data_relay.application.errors import (
    DatabaseUnavailableError,
    DatabaseWakingError,
    SchemaUnavailableError,
)


class Value(Enum):
    EXAMPLE = "example"


class FakeDataClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.formatted_records = "[]"
        self.error_code: str | None = None
        self.error_message = "failed"

    def _result(
        self,
        operation: str,
        parameters: dict[str, object],
    ) -> dict[str, object]:
        self.calls.append((operation, parameters))
        if self.error_code is not None:
            raise ClientError(
                {
                    "Error": {
                        "Code": self.error_code,
                        "Message": self.error_message,
                    },
                },
                operation,
            )
        if operation == "begin":
            return {"transactionId": "transaction-1"}
        if operation == "execute":
            return {
                "formattedRecords": self.formatted_records,
                "numberOfRecordsUpdated": 2,
            }
        return {}

    def begin_transaction(self, **parameters: object) -> dict[str, object]:
        return self._result("begin", parameters)

    def commit_transaction(self, **parameters: object) -> dict[str, object]:
        return self._result("commit", parameters)

    def rollback_transaction(self, **parameters: object) -> dict[str, object]:
        return self._result("rollback", parameters)

    def execute_statement(self, **parameters: object) -> dict[str, object]:
        return self._result("execute", parameters)

    def batch_execute_statement(self, **parameters: object) -> dict[str, object]:
        return self._result("batch", parameters)


def connection(*, client: FakeDataClient) -> RdsDataConnection:
    return RdsDataConnection(
        client=client,
        resource_arn="cluster",
        secret_arn="secret",
        database_name="relay",
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, {"isNull": True}),
        (True, {"booleanValue": True}),
        (3, {"longValue": 3}),
        (2.5, {"doubleValue": 2.5}),
        ("text", {"stringValue": "text"}),
        (Value.EXAMPLE, {"stringValue": "example"}),
        (date(2026, 7, 25), {"stringValue": "2026-07-25"}),
        (
            datetime(2026, 7, 25, 12, tzinfo=UTC),
            {"stringValue": "2026-07-25T12:00:00+00:00"},
        ),
    ],
)
def test_data_api_field_encodes_supported_scalars(
    value: object,
    expected: dict[str, object],
) -> None:
    assert data_api_field(value=value) == expected


def test_data_api_field_rejects_unsupported_values() -> None:
    with pytest.raises(TypeError, match="Unsupported Data API"):
        data_api_field(value={"unsupported": True})


def test_data_api_statement_translates_positionals_and_integer_arrays() -> None:
    sql, parameters = data_api_statement(
        query="SELECT * FROM rows WHERE id = $1 AND value = ANY($2)",
        arguments=(7, [1, 2]),
    )
    assert "id = :p1" in sql
    assert "ANY(CAST(:p2 AS integer[]))" in sql
    assert parameters[1]["value"] == {"stringValue": "{1,2}"}


def test_data_api_statement_rejects_invalid_arrays_and_missing_arguments() -> None:
    with pytest.raises(TypeError, match="integer array"):
        data_api_statement(query="SELECT $1", arguments=(["bad"],))
    with pytest.raises(ValueError, match="without a matching"):
        data_api_statement(query="SELECT $2", arguments=(1,))


@pytest.mark.asyncio
async def test_data_api_fetch_decodes_rows_and_values() -> None:
    client = FakeDataClient()
    client.formatted_records = '[{"id":1,"name":"one"}]'
    database = connection(client=client)
    rows = await database.fetch("SELECT id, name FROM rows WHERE id = $1", 1)
    assert rows == [{"id": 1, "name": "one"}]
    assert await database.fetchrow("SELECT id FROM rows") == {
        "id": 1,
        "name": "one",
    }
    assert await database.fetchval("SELECT id FROM rows") == 1
    assert await database.execute("CREATE TABLE example (id integer)") == "OK 2"


@pytest.mark.asyncio
async def test_data_api_transaction_batches_writes_in_hundreds() -> None:
    client = FakeDataClient()
    database = connection(client=client)
    async with database.transaction():
        for index in range(101):
            assert (
                await database.execute(
                    "INSERT INTO rows (id) VALUES ($1)",
                    index,
                )
                == "QUEUED"
            )
    operations = [operation for operation, _ in client.calls]
    assert operations == ["begin", "batch", "batch", "commit"]
    parameter_sets = [
        parameters["parameterSets"]
        for operation, parameters in client.calls
        if operation == "batch"
    ]
    assert [len(cast("list[object]", values)) for values in parameter_sets] == [
        100,
        1,
    ]


@pytest.mark.asyncio
async def test_data_api_transaction_rolls_back_and_rejects_nesting() -> None:
    client = FakeDataClient()
    database = connection(client=client)
    with pytest.raises(RuntimeError, match="Nested"):
        async with database.transaction():
            await database.begin()
    assert [operation for operation, _ in client.calls] == [
        "begin",
        "rollback",
    ]
    with pytest.raises(RuntimeError, match="No Data API transaction"):
        await database.commit()
    with pytest.raises(RuntimeError, match="No Data API transaction"):
        await database.rollback()


@pytest.mark.asyncio
async def test_data_api_classifies_waking_and_other_client_errors() -> None:
    client = FakeDataClient()
    client.error_code = "DatabaseResumingException"
    with pytest.raises(DatabaseWakingError):
        await connection(client=client).fetch("SELECT 1")
    client.error_code = "BadRequestException"
    with pytest.raises(DatabaseUnavailableError, match="BadRequestException"):
        await connection(client=client).fetch("SELECT 1")
    client.error_message = (
        'ERROR: relation "relay_schema_migrations" does not exist'
    )
    with pytest.raises(SchemaUnavailableError):
        await connection(client=client).fetch("SELECT 1")


@pytest.mark.asyncio
async def test_data_api_rejects_invalid_and_oversized_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeDataClient()
    client.formatted_records = "{}"
    with pytest.raises(DatabaseUnavailableError, match="row list"):
        await connection(client=client).fetch("SELECT 1")
    client.formatted_records = '["bad"]'
    with pytest.raises(DatabaseUnavailableError, match="invalid row"):
        await connection(client=client).fetch("SELECT 1")
    client.formatted_records = '[{"large":"value"}]'
    monkeypatch.setattr(rds_data, "MAX_ROW_BYTES", 2)
    with pytest.raises(DatabaseUnavailableError, match="64 KiB"):
        await connection(client=client).fetch("SELECT 1")
    monkeypatch.setattr(rds_data, "MAX_RESULT_BYTES", 2)
    with pytest.raises(DatabaseUnavailableError, match="1 MiB"):
        await connection(client=client).fetch("SELECT 1")


@pytest.mark.asyncio
async def test_data_api_pool_acquires_and_cleans_up_transactions() -> None:
    client = FakeDataClient()
    pool = RdsDataPool(
        client=client,
        resource_arn="cluster",
        secret_arn="secret",
        database_name="relay",
    )
    async with pool.acquire() as database:
        await database.begin()
    assert [operation for operation, _ in client.calls] == ["begin", "rollback"]
    await pool.close()
