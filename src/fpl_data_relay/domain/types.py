"""Shared domain value types and model configuration."""

from pydantic import BaseModel, ConfigDict

type JsonValue = (
    dict[str, JsonValue] | list[JsonValue] | str | int | float | bool | None
)
type ScalarValue = int | float | str | bool | None


class FplModel(BaseModel):
    """Base model for known FPL fields; unknown fields are ignored."""

    model_config = ConfigDict(extra="ignore", frozen=True)
