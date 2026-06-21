"""Recursive JSON type alias shared across relay boundaries."""

type JsonValue = (
    dict[str, JsonValue] | list[JsonValue] | str | int | float | bool | None
)
