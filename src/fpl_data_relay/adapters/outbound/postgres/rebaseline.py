"""PostgreSQL change-feed rebaseline adapter."""

from fpl_data_relay.adapters.outbound.postgres.database import PostgresDatabase
from fpl_data_relay.application.ports.administration import (
    ChangeFeedRebaselineResult,
)


class PostgresChangeFeedRebaseliner:
    """Expose the database's atomic rebaseline operation through a narrow port."""

    def __init__(self, *, database: PostgresDatabase) -> None:
        self._database = database

    async def rebaseline_current(
        self,
        *,
        reason: str,
    ) -> ChangeFeedRebaselineResult:
        return await self._database.rebaseline_current_change_feed(reason=reason)
