"""Stable application errors shared by driving adapters."""


class DatabaseWakingError(RuntimeError):
    """Aurora is resuming from a zero-capacity paused state."""


class DatabaseUnavailableError(RuntimeError):
    """The configured database could not serve a request."""


class SchemaUnavailableError(RuntimeError):
    """The database schema is missing or incompatible."""


class AwsConnectionError(RuntimeError):
    """The configured AWS profile cannot provide a usable connection."""


class CommunityConfigurationError(RuntimeError):
    """Community strategy or credential configuration is invalid."""


class CommunitySourceError(RuntimeError):
    """Stable source failure with an explicit abort policy."""

    def __init__(self, *, code: str, fatal: bool, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.fatal = fatal


class CommunityModelError(RuntimeError):
    """The agent response failed provider or integrity validation."""


class CommunityPublicationError(RuntimeError):
    """A completed analysis did not meet publication invariants."""
