"""Explicit launch settings for the local terminal user interface."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TuiSettings(BaseModel):
    """Validated paths and retention limits supplied by the launcher."""

    model_config = ConfigDict(frozen=True)

    project_root: Path
    admin_config: Path
    log_path: Path
    log_max_bytes: int = Field(gt=0)
    log_file_count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_paths(self) -> TuiSettings:
        """Require explicit absolute paths and a recognisable checkout."""
        paths = {
            "project root": self.project_root,
            "admin config": self.admin_config,
            "log path": self.log_path,
        }
        relative = [name for name, path in paths.items() if not path.is_absolute()]
        if relative:
            raise ValueError("TUI paths must be absolute: " + ", ".join(relative))
        if not self.project_root.is_dir():
            raise ValueError(f"Project root is not a directory: {self.project_root}")
        for required_name in ("Makefile", "pyproject.toml"):
            required_path = self.project_root / required_name
            if not required_path.is_file():
                raise ValueError(f"Project root is missing {required_name}.")
        if self.admin_config.parent != self.project_root:
            raise ValueError("Admin config must be directly inside the project root.")
        if self.log_path.parent.parent != self.project_root / ".admin-state":
            raise ValueError("TUI log must be under .admin-state/<directory>.")
        return self
