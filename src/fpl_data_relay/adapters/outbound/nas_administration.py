"""SSH-backed administration adapter for the Synology collector."""

import re
import subprocess
from importlib.resources import files

from fpl_data_relay.application.ports.administration import NasCollectorStatus
from fpl_data_relay.config import AdminSettings

IMAGE_TAG_PATTERN = re.compile(r"^sha-[0-9a-f]{40}$")


class NasSshAdministration:
    """Run a bounded versioned helper over the configured SSH connection."""

    def __init__(self, *, settings: AdminSettings) -> None:
        self._settings = settings
        self._script = (
            files("fpl_data_relay.adapters.outbound")
            .joinpath("nas_admin.sh")
            .read_text(encoding="utf-8")
        )

    def doctor(self) -> None:
        """Validate SSH, Compose configuration, and Docker access."""
        output = self._run(action="doctor", trailing_arguments=[])
        if "doctor=ok" not in output.splitlines():
            raise RuntimeError("NAS doctor did not report success.")

    def status(self) -> NasCollectorStatus:
        """Return the collector's container state."""
        return parse_status(output=self._run(action="status", trailing_arguments=[]))

    def start(self) -> NasCollectorStatus:
        """Start the collector and wait for its health check."""
        return parse_status(output=self._run(action="start", trailing_arguments=[]))

    def stop(self) -> NasCollectorStatus:
        """Stop the collector without removing its service."""
        return parse_status(output=self._run(action="stop", trailing_arguments=[]))

    def logs(self, *, tail_lines: int) -> str:
        """Return a bounded collector log tail."""
        if tail_lines < 1:
            raise ValueError("NAS log tail must be positive.")
        return self._run(action="logs", trailing_arguments=[str(tail_lines)])

    def update(self, *, image_tag: str) -> NasCollectorStatus:
        """Activate one already-published immutable collector image."""
        if IMAGE_TAG_PATTERN.fullmatch(image_tag) is None:
            raise ValueError("Collector image tag must be sha- plus 40 lowercase hex.")
        return parse_status(
            output=self._run(action="update", trailing_arguments=[image_tag]),
        )

    def _run(self, *, action: str, trailing_arguments: list[str]) -> str:
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            (
                "ConnectTimeout="
                f"{self._settings.nas_ssh_connect_timeout_seconds}"
            ),
            self._settings.nas_ssh_target,
            "sh",
            "-s",
            "--",
            action,
            self._settings.nas_stack_directory,
            self._settings.nas_compose_executable,
            self._settings.nas_docker_executable,
            str(self._settings.nas_health_attempts),
            str(self._settings.nas_health_interval_seconds),
            *trailing_arguments,
        ]
        result = subprocess.run(
            command,
            input=self._script,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(
                f"NAS {action} failed with exit code {result.returncode}: {detail}",
            )
        return "\n".join(
            part for part in (result.stdout.strip(), result.stderr.strip()) if part
        )


def parse_status(*, output: str) -> NasCollectorStatus:
    """Parse the final key/value status emitted by the remote helper."""
    values: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"running", "health", "image"}:
            values[key] = value
    missing = {"running", "health", "image"} - values.keys()
    if missing:
        raise RuntimeError(
            "NAS status omitted fields: " + ", ".join(sorted(missing)),
        )
    if values["running"] not in {"true", "false"}:
        raise RuntimeError("NAS running status must be true or false.")
    return NasCollectorStatus(
        running=values["running"] == "true",
        health=values["health"],
        image=values["image"],
    )
