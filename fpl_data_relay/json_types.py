type JsonValue = (
    dict[str, JsonValue] | list[JsonValue] | str | int | float | bool | None
)
