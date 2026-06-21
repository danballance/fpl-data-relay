"""Validation helpers that log and ignore unknown upstream FPL fields."""

import logging
from collections.abc import Mapping, Sequence
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from fpl_data_relay.json_types import JsonValue

LOGGER = logging.getLogger(__name__)

class ValidationContext(BaseModel):
    """Context used when logging unexpected upstream fields."""

    endpoint: str
    model_name: str
    path: str
    entity_id: int | str | None


def validate_fpl_model[ModelT: BaseModel](
    *,
    model: type[ModelT],
    payload: JsonValue,
    endpoint: str,
) -> ModelT:
    """Log unknown fields, then validate a JSON object into an FPL model."""
    log_unknown_fields(
        model=model,
        payload=payload,
        context=ValidationContext(
            endpoint=endpoint,
            model_name=model.__name__,
            path=model.__name__,
            entity_id=entity_identifier(payload=payload),
        ),
    )
    return model.model_validate(payload)


def validate_fpl_model_list[ModelT: BaseModel](
    *,
    model: type[ModelT],
    payload: JsonValue,
    endpoint: str,
) -> list[ModelT]:
    """Log unknown fields, then validate a JSON array into FPL models."""
    if not isinstance(payload, list):
        raise ValidationError.from_exception_data(
            title=f"{model.__name__}List",
            line_errors=[
                {
                    "type": "list_type",
                    "loc": (),
                    "input": payload,
                    "ctx": {},
                },
            ],
        )
    return [
        validate_fpl_model(model=model, payload=item, endpoint=endpoint)
        for item in payload
    ]


def log_unknown_fields(
    *,
    model: type[BaseModel],
    payload: JsonValue,
    context: ValidationContext,
) -> None:
    """Recursively log fields not declared by Pydantic models."""
    if not isinstance(payload, Mapping):
        return
    known_fields = set(model.model_fields)
    unknown_fields = sorted(set(payload) - known_fields)
    if unknown_fields:
        LOGGER.warning(
            "Unexpected FPL payload field(s): endpoint=%s model=%s path=%s "
            "entity_id=%s fields=%s",
            context.endpoint,
            context.model_name,
            context.path,
            context.entity_id,
            ",".join(unknown_fields),
        )

    for field_name, field_info in model.model_fields.items():
        if field_name not in payload:
            continue
        nested_model = nested_model_type(annotation=field_info.annotation)
        if nested_model is None:
            continue
        child_payload = payload[field_name]
        if isinstance(child_payload, list):
            for index, item in enumerate(child_payload):
                child_context = ValidationContext(
                    endpoint=context.endpoint,
                    model_name=nested_model.__name__,
                    path=f"{context.path}.{field_name}[{index}]",
                    entity_id=entity_identifier(payload=item),
                )
                log_unknown_fields(
                    model=nested_model,
                    payload=cast("JsonValue", item),
                    context=child_context,
                )
            continue
        child_context = ValidationContext(
            endpoint=context.endpoint,
            model_name=nested_model.__name__,
            path=f"{context.path}.{field_name}",
            entity_id=entity_identifier(payload=child_payload),
        )
        log_unknown_fields(
            model=nested_model,
            payload=cast("JsonValue", child_payload),
            context=child_context,
        )


def nested_model_type(*, annotation: Any) -> type[BaseModel] | None:
    """Return a nested BaseModel type from a field annotation, if present."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    origin = getattr(annotation, "__origin__", None)
    if origin not in (list, Sequence):
        return None
    arguments = getattr(annotation, "__args__", ())
    if len(arguments) != 1:
        return None
    item_type = arguments[0]
    if isinstance(item_type, type) and issubclass(item_type, BaseModel):
        return item_type
    return None


def entity_identifier(*, payload: object) -> int | str | None:
    """Extract a stable entity identifier from raw payload when one exists."""
    if not isinstance(payload, Mapping):
        return None
    for key in ("id", "event", "name", "identifier"):
        value = payload.get(key)
        if isinstance(value, int | str):
            return value
    return None
