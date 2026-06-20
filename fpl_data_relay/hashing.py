import hashlib
import json

from fpl_data_relay.json_types import JsonValue


def canonical_json(*, payload: object) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def payload_sha256(*, payload: object) -> str:
    canonical_payload = canonical_json(payload=payload)
    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


def parse_json_payload(*, payload: str) -> JsonValue:
    parsed_payload = json.loads(payload)
    if isinstance(parsed_payload, dict | list | str | int | float | bool):
        return parsed_payload
    if parsed_payload is None:
        return None
    raise TypeError(f"Decoded payload is not JSON-compatible: {type(parsed_payload)}")

