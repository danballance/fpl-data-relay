"""Stable application errors shared by driving adapters."""


class DatabaseWakingError(RuntimeError):
    """Aurora is resuming from a zero-capacity paused state."""


class DatabaseUnavailableError(RuntimeError):
    """The configured database could not serve a request."""


class SchemaUnavailableError(RuntimeError):
    """The database schema is missing or incompatible."""
