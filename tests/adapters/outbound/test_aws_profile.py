import subprocess
from collections.abc import Sequence

import pytest

from fpl_data_relay.adapters.outbound.aws_profile import (
    SIGN_IN_POLICY_ARN,
    AwsCliIoMode,
    AwsCliResult,
    AwsConsoleProfileAdministration,
    SubprocessAwsCliRunner,
)
from fpl_data_relay.application.errors import AwsProfileError
from fpl_data_relay.config import AdminSettings
from tests.adapters.inbound.test_admin_cli import admin_settings


class FakeAwsCliRunner:
    def __init__(self, *, settings: AdminSettings) -> None:
        self.settings = settings
        self.version = "aws-cli/2.34.24 Python/3.13 Linux/6 source/x86_64"
        self.profiles: dict[str, dict[str, str]] = {}
        self.authenticated_profiles: set[str] = set()
        self.account_id = settings.aws_account_id
        self.arn = "arn:aws:iam::123456789012:user/admin"
        self.login_failure: AwsCliResult | None = None
        self.commands: list[tuple[list[str], AwsCliIoMode]] = []

    def run(
        self,
        *,
        command: list[str],
        io_mode: AwsCliIoMode,
    ) -> AwsCliResult:
        self.commands.append((command, io_mode))
        arguments = command[1:]
        if arguments == ["--version"]:
            return success(stdout=self.version)
        if arguments == ["configure", "list-profiles"]:
            return success(stdout="\n".join(self.profiles))
        if arguments[:2] == ["configure", "get"]:
            key = arguments[2]
            profile = arguments[4]
            value = self.profiles.get(profile, {}).get(key)
            result = (
                AwsCliResult(returncode=1, stdout="", stderr="")
                if value is None
                else success(stdout=value)
            )
            return (
                result.model_copy(update={"stdout": ""})
                if io_mode is AwsCliIoMode.DISCARD_STDOUT
                else result
            )
        if arguments[:2] == ["configure", "set"]:
            key = arguments[2]
            value = arguments[3]
            profile = arguments[5]
            self.profiles.setdefault(profile, {})[key] = value
            return success()
        if arguments[0] == "login":
            assert io_mode is AwsCliIoMode.INTERACTIVE
            if self.login_failure is not None:
                return self.login_failure
            profile = arguments[2]
            self.profiles.setdefault(profile, {})["login_session"] = (
                "arn:aws:iam::123456789012:user/admin"
            )
            self.authenticated_profiles.add(profile)
            return success()
        if arguments[0] == "logout":
            assert io_mode is AwsCliIoMode.INTERACTIVE
            self.authenticated_profiles.discard(arguments[2])
            return success()
        if arguments[:2] == ["sts", "get-caller-identity"]:
            profile = arguments[3]
            if profile not in self.authenticated_profiles:
                return AwsCliResult(
                    returncode=255,
                    stdout="",
                    stderr="cached login expired",
                )
            return success(
                stdout=(
                    '{"Account":"'
                    + self.account_id
                    + '","Arn":"'
                    + self.arn
                    + '"}'
                ),
            )
        raise AssertionError(f"Unexpected command: {command}")


def success(*, stdout: str = "") -> AwsCliResult:
    return AwsCliResult(returncode=0, stdout=stdout, stderr="")


def adapter(
    *,
    settings: AdminSettings,
    runner: FakeAwsCliRunner,
) -> AwsConsoleProfileAdministration:
    return AwsConsoleProfileAdministration(settings=settings, runner=runner)


def test_profile_setup_creates_logs_in_and_verifies_new_profile() -> None:
    settings = admin_settings()
    runner = FakeAwsCliRunner(settings=settings)

    status = adapter(settings=settings, runner=runner).setup()

    assert status.profile_name == settings.aws_profile
    assert status.region == settings.aws_region
    assert status.authenticated is True
    assert runner.profiles[settings.aws_profile]["output"] == "json"
    login_command = next(
        command for command in runner.commands if command[0][1] == "login"
    )
    assert login_command == (
        ["aws", "login", "--profile", settings.aws_profile],
        AwsCliIoMode.INTERACTIVE,
    )


def test_profile_setup_recovers_partial_state_and_repeat_login() -> None:
    settings = admin_settings()
    runner = FakeAwsCliRunner(settings=settings)
    runner.profiles[settings.aws_profile] = {"region": settings.aws_region}
    profile = adapter(settings=settings, runner=runner)

    assert profile.setup().authenticated is True
    assert profile.login().authenticated is True
    assert profile.status().account_id == settings.aws_account_id


@pytest.mark.parametrize(
    "key",
    [
        "aws_access_key_id",
        "credential_process",
        "credential_source",
        "role_arn",
        "source_profile",
        "sso_session",
        "sso_start_url",
        "web_identity_token_file",
    ],
)
def test_profile_setup_refuses_conflicting_authentication(key: str) -> None:
    settings = admin_settings()
    runner = FakeAwsCliRunner(settings=settings)
    runner.profiles[settings.aws_profile] = {key: "configured"}

    with pytest.raises(AwsProfileError, match=key):
        adapter(settings=settings, runner=runner).setup()

    assert not any(command[0][1] == "login" for command in runner.commands)


def test_profile_operations_require_supported_cli_and_configured_login() -> None:
    settings = admin_settings()
    runner = FakeAwsCliRunner(settings=settings)
    runner.version = "aws-cli/2.31.99 Python/3.13 Linux/6 source/x86_64"
    profile = adapter(settings=settings, runner=runner)
    with pytest.raises(AwsProfileError, match=r"2\.32\.0"):
        profile.setup()

    runner.version = "aws-cli/2.34.24 Python/3.13 Linux/6 source/x86_64"
    with pytest.raises(AwsProfileError, match="does not exist"):
        profile.login()
    runner.profiles[settings.aws_profile] = {"region": settings.aws_region}
    with pytest.raises(AwsProfileError, match="not configured"):
        profile.status()


def test_profile_login_failure_preserves_detail_and_permission_guidance() -> None:
    settings = admin_settings()
    runner = FakeAwsCliRunner(settings=settings)
    runner.profiles[settings.aws_profile] = {"region": settings.aws_region}
    runner.login_failure = AwsCliResult(
        returncode=255,
        stdout="",
        stderr="HTTP 400 Bad Request",
    )

    with pytest.raises(AwsProfileError) as captured:
        adapter(settings=settings, runner=runner).setup()

    assert "HTTP 400 Bad Request" in str(captured.value)
    assert SIGN_IN_POLICY_ARN in str(captured.value)


def test_profile_status_rejects_expired_credentials_and_wrong_account() -> None:
    settings = admin_settings()
    runner = FakeAwsCliRunner(settings=settings)
    runner.profiles[settings.aws_profile] = {
        "region": settings.aws_region,
        "login_session": "arn:login",
    }
    profile = adapter(settings=settings, runner=runner)
    with pytest.raises(AwsProfileError, match="aws-profile-login"):
        profile.status()

    runner.authenticated_profiles.add(settings.aws_profile)
    runner.account_id = "999999999999"
    with pytest.raises(AwsProfileError, match="account mismatch"):
        profile.status()


def test_profile_logout_only_removes_configured_profile_session() -> None:
    settings = admin_settings()
    runner = FakeAwsCliRunner(settings=settings)
    runner.profiles = {
        settings.aws_profile: {
            "region": settings.aws_region,
            "login_session": "arn:login",
        },
        "default": {"aws_access_key_id": "not-touched"},
    }
    runner.authenticated_profiles = {settings.aws_profile, "default"}

    adapter(settings=settings, runner=runner).logout()

    assert runner.authenticated_profiles == {"default"}
    assert runner.profiles["default"] == {"aws_access_key_id": "not-touched"}


def test_subprocess_runner_preserves_interactive_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subprocess_parameters: list[tuple[object, object]] = []

    def fake_run(
        command: Sequence[str],
        *,
        text: bool,
        check: bool,
        capture_output: bool | None = None,
        stdout: int | None = None,
        stderr: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del command, text, check
        subprocess_parameters.append((capture_output, stdout))
        del stderr
        return subprocess.CompletedProcess(args=["aws"], returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = SubprocessAwsCliRunner()
    runner.run(
        command=["aws", "--version"],
        io_mode=AwsCliIoMode.CAPTURE,
    )
    runner.run(
        command=["aws", "login"],
        io_mode=AwsCliIoMode.INTERACTIVE,
    )
    runner.run(
        command=["aws", "configure", "get", "aws_access_key_id"],
        io_mode=AwsCliIoMode.DISCARD_STDOUT,
    )

    assert subprocess_parameters == [
        (True, None),
        (None, None),
        (None, subprocess.DEVNULL),
    ]


def test_subprocess_runner_reports_missing_aws_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_run(
        command: Sequence[str],
        *,
        text: bool,
        capture_output: bool,
        check: bool,
    ) -> None:
        del command, text, capture_output, check
        raise FileNotFoundError("aws")

    monkeypatch.setattr(subprocess, "run", missing_run)
    with pytest.raises(AwsProfileError, match="was not found"):
        SubprocessAwsCliRunner().run(
            command=["aws", "--version"],
            io_mode=AwsCliIoMode.CAPTURE,
        )
