"""Typed asyncpg connection and pool boundaries."""

from contextlib import AbstractAsyncContextManager
from typing import Protocol


class ConnectionProtocol(Protocol):
    """Subset of asyncpg connection behavior used by the adapters."""

    def transaction(self) -> AbstractAsyncContextManager[object]: ...

    async def execute(self, query: str, *arguments: object) -> str: ...

    async def fetchrow(self, query: str, *arguments: object) -> object: ...

    async def fetch(self, query: str, *arguments: object) -> list[object]: ...

    async def fetchval(self, query: str, *arguments: object) -> object: ...

    async def add_listener(self, channel: str, callback: object) -> None: ...

    async def remove_listener(self, channel: str, callback: object) -> None: ...


class ConnectionManagerProtocol(Protocol):
    """Async context manager returned by pool acquisition."""

    async def __aenter__(self) -> ConnectionProtocol: ...

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None: ...


class PoolProtocol(Protocol):
    """Subset of asyncpg pool behavior required by the adapters."""

    def acquire(self) -> ConnectionManagerProtocol: ...

    async def close(self) -> None: ...
