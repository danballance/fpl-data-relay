"""Local AWS CLI console-login profile administration."""

import json
import re
import subprocess
from collections.abc import Mapping
from enum import StrEnum
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fpl_data_relay.application.errors import AwsProfileError
from fpl_data_relay.application.ports.administration import AwsProfileStatus
from fpl_data_relay.config import AdminSettings

MINIMUM_AWS_CLI_VERSION = (2, 32, 0)
VERSION_PATTERN = re.compile(r"aws-cli/(\d+)\.(\d+)\.(\d+)")
SIGN_IN_POLICY_ARN = "arn:aws:iam::aws:policy/SignInLocalDevelopmentAccess"
CONFLICTING_AUTHENTICATION_KEYS = (
    "aws_access_key_id",
    "credential_process",
    "credential_source",
    "role_arn",
    "source_profile",
    "sso_account_id",
    "sso_role_name",
    "sso_session",
    "sso_start_url",
    "web_identity_token_file",
)


class AwsCliResult(BaseModel):
    """Captured result from one AWS CLI invocation."""

    model_config = ConfigDict(frozen=True)

    returncode: int
    stdout: str
    stderr: str


class AwsCliIoMode(StrEnum):
    """Terminal/output handling for one AWS CLI command."""

    CAPTURE = "capture"
    INTERACTIVE = "interactive"
    DISCARD_STDOUT = "discard-stdout"


class AwsCliRunner(Protocol):
    """Run AWS CLI commands with explicit interactive behavior."""

    def run(
        self,
        *,
        command: list[str],
        io_mode: AwsCliIoMode,
    ) -> AwsCliResult: ...


class SubprocessAwsCliRunner:
    """Subprocess runner that leaves browser login attached to the terminal."""

    def run(
        self,
        *,
        command: list[str],
        io_mode: AwsCliIoMode,
    ) -> AwsCliResult:
        """Run one command without a shell or credential-bearing environment."""
        try:
            if io_mode is AwsCliIoMode.CAPTURE:
                completed = subprocess.run(
                    command,
                    text=True,
                    capture_output=True,
                    check=False,
                )
            elif io_mode is AwsCliIoMode.INTERACTIVE:
                completed = subprocess.run(
                    command,
                    text=True,
                    check=False,
                )
            else:
                completed = subprocess.run(
                    command,
                    text=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    check=False,
                )
        except FileNotFoundError as error:
            raise AwsProfileError(
                "AWS CLI executable 'aws' was not found; install AWS CLI 2.32 "
                "or newer.",
            ) from error
        return AwsCliResult(
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )


class CallerIdentityPayload(BaseModel):
    """Validated STS caller identity response."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    account_id: str = Field(alias="Account", pattern=r"^\d{12}$")
    arn: str = Field(alias="Arn", min_length=1)


class AwsConsoleProfileAdministration:
    """Create and operate one dedicated AWS console-login profile."""

    def __init__(
        self,
        *,
        settings: AdminSettings,
        runner: AwsCliRunner,
    ) -> None:
        self._settings = settings
        self._runner = runner

    def setup(self) -> AwsProfileStatus:
        """Configure an absent or compatible profile, log in, and verify it."""
        self._require_supported_cli()
        if self._profile_exists():
            self._require_console_login_compatible(require_login_session=False)
        self._require_success(
            result=self._run_captured(
                "configure",
                "set",
                "region",
                self._settings.aws_region,
                "--profile",
                self._settings.aws_profile,
            ),
            operation="configure the AWS profile region",
        )
        self._require_success(
            result=self._run_captured(
                "configure",
                "set",
                "output",
                "json",
                "--profile",
                self._settings.aws_profile,
            ),
            operation="configure the AWS profile output",
        )
        self._interactive_login()
        return self._verified_status()

    def login(self) -> AwsProfileStatus:
        """Renew one already-configured console-login profile."""
        self._require_supported_cli()
        self._require_console_login_compatible(require_login_session=True)
        self._interactive_login()
        return self._verified_status()

    def status(self) -> AwsProfileStatus:
        """Verify the configured profile and its current temporary credentials."""
        self._require_supported_cli()
        self._require_console_login_compatible(require_login_session=True)
        return self._verified_status()

    def logout(self) -> None:
        """Remove cached console credentials for only the configured profile."""
        self._require_supported_cli()
        self._require_console_login_compatible(require_login_session=True)
        self._require_success(
            result=self._run(
                "logout",
                "--profile",
                self._settings.aws_profile,
                io_mode=AwsCliIoMode.INTERACTIVE,
            ),
            operation="log out of the AWS profile",
        )

    def _require_supported_cli(self) -> None:
        result = self._run_captured("--version")
        self._require_success(result=result, operation="read the AWS CLI version")
        match = VERSION_PATTERN.search(f"{result.stdout}\n{result.stderr}")
        if match is None:
            raise AwsProfileError("AWS CLI returned an unrecognised version string.")
        version = tuple(int(value) for value in match.groups())
        if version < MINIMUM_AWS_CLI_VERSION:
            minimum = ".".join(str(value) for value in MINIMUM_AWS_CLI_VERSION)
            actual = ".".join(str(value) for value in version)
            raise AwsProfileError(
                f"AWS CLI {minimum} or newer is required; found {actual}.",
            )

    def _profile_exists(self) -> bool:
        result = self._run_captured("configure", "list-profiles")
        self._require_success(result=result, operation="list AWS profiles")
        return self._settings.aws_profile in {
            line.strip() for line in result.stdout.splitlines() if line.strip()
        }

    def _require_console_login_compatible(
        self,
        *,
        require_login_session: bool,
    ) -> None:
        if not self._profile_exists():
            raise AwsProfileError(
                f"AWS profile {self._settings.aws_profile!r} does not exist; "
                "run make aws-profile-setup.",
            )
        conflicting = [
            key
            for key in CONFLICTING_AUTHENTICATION_KEYS
            if self._config_value_exists(key=key)
        ]
        if conflicting:
            keys = ", ".join(conflicting)
            raise AwsProfileError(
                f"AWS profile {self._settings.aws_profile!r} uses conflicting "
                f"authentication settings: {keys}. Refusing to overwrite it.",
            )
        login_session = self._optional_config_value(key="login_session")
        if require_login_session and login_session is None:
            raise AwsProfileError(
                f"AWS profile {self._settings.aws_profile!r} is not configured "
                "for console login; run make aws-profile-setup.",
            )

    def _optional_config_value(self, *, key: str) -> str | None:
        result = self._run_captured(
            "configure",
            "get",
            key,
            "--profile",
            self._settings.aws_profile,
        )
        if result.returncode == 1:
            return None
        self._require_success(
            result=result,
            operation=f"inspect AWS profile setting {key}",
        )
        value = result.stdout.strip()
        return value if value else None

    def _config_value_exists(self, *, key: str) -> bool:
        result = self._run(
            "configure",
            "get",
            key,
            "--profile",
            self._settings.aws_profile,
            io_mode=AwsCliIoMode.DISCARD_STDOUT,
        )
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        self._require_success(
            result=result,
            operation=f"inspect whether AWS profile setting {key} exists",
        )
        raise AssertionError("AWS profile existence check did not return.")

    def _interactive_login(self) -> None:
        result = self._run(
            "login",
            "--profile",
            self._settings.aws_profile,
            io_mode=AwsCliIoMode.INTERACTIVE,
        )
        if result.returncode != 0:
            detail = failure_detail(result=result)
            prefix = f"AWS console login failed: {detail}. " if detail else ""
            raise AwsProfileError(
                prefix
                + "Ensure the signing-in identity has "
                f"{SIGN_IN_POLICY_ARN}; run make aws-profile-bootstrap, "
                "then retry.",
            )

    def _verified_status(self) -> AwsProfileStatus:
        self._require_console_login_compatible(require_login_session=True)
        region = self._optional_config_value(key="region")
        if region is None:
            raise AwsProfileError(
                "AWS profile has no region; run make aws-profile-setup.",
            )
        if region != self._settings.aws_region:
            raise AwsProfileError(
                f"AWS profile region mismatch: expected "
                f"{self._settings.aws_region!r}, found {region!r}; "
                "run make aws-profile-setup.",
            )
        result = self._run_captured(
            "sts",
            "get-caller-identity",
            "--profile",
            self._settings.aws_profile,
            "--output",
            "json",
        )
        if result.returncode != 0:
            detail = failure_detail(result=result)
            suffix = f" AWS CLI reported: {detail}" if detail else ""
            raise AwsProfileError(
                "AWS console-login credentials are unavailable or expired; "
                f"run make aws-profile-login.{suffix}",
            )
        try:
            raw_payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise AwsProfileError("STS returned invalid JSON.") from error
        if not isinstance(raw_payload, Mapping):
            raise AwsProfileError("STS caller identity must be a JSON object.")
        try:
            identity = CallerIdentityPayload.model_validate(
                cast("Mapping[str, object]", raw_payload),
            )
        except ValidationError as error:
            raise AwsProfileError("STS returned an invalid caller identity.") from error
        if identity.account_id != self._settings.aws_account_id:
            raise AwsProfileError(
                "AWS account mismatch: "
                f"expected {self._settings.aws_account_id}, "
                f"found {identity.account_id}.",
            )
        return AwsProfileStatus(
            profile_name=self._settings.aws_profile,
            region=region,
            authentication="console-login",
            authenticated=True,
            account_id=identity.account_id,
            arn=identity.arn,
        )

    def _run_captured(self, *arguments: str) -> AwsCliResult:
        return self._run(
            *arguments,
            io_mode=AwsCliIoMode.CAPTURE,
        )

    def _run(
        self,
        *arguments: str,
        io_mode: AwsCliIoMode,
    ) -> AwsCliResult:
        return self._runner.run(
            command=["aws", *arguments],
            io_mode=io_mode,
        )

    @staticmethod
    def _require_success(*, result: AwsCliResult, operation: str) -> None:
        if result.returncode == 0:
            return
        detail = failure_detail(result=result)
        suffix = f": {detail}" if detail else ""
        raise AwsProfileError(f"Failed to {operation}{suffix}")


def failure_detail(*, result: AwsCliResult) -> str:
    """Return bounded human-readable AWS CLI failure output."""
    detail = " ".join(
        part.strip()
        for part in (result.stderr, result.stdout)
        if part.strip()
    )
    return detail if len(detail) <= 2_000 else detail[:2_000] + "..."
