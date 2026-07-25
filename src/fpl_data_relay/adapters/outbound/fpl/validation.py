"""Validation helpers for upstream FPL payloads."""

from pydantic import BaseModel, ValidationError

from fpl_data_relay.domain.types import JsonValue


def validate_fpl_model[ModelT: BaseModel](
    *,
    model: type[ModelT],
    payload: JsonValue,
) -> ModelT:
    """Validate a JSON object into an FPL model."""
    return model.model_validate(payload)


def validate_fpl_model_list[ModelT: BaseModel](
    *,
    model: type[ModelT],
    payload: JsonValue,
) -> list[ModelT]:
    """Validate a JSON array into FPL models."""
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
        validate_fpl_model(model=model, payload=item)
        for item in payload
    ]
