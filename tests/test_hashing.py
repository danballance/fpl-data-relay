import pytest

from fpl_data_relay.hashing import canonical_json, parse_json_payload, payload_sha256


def test_payload_hash_is_canonical_for_key_order() -> None:
    first = {"b": 2, "a": 1}
    second = {"a": 1, "b": 2}
    assert canonical_json(payload=first) == canonical_json(payload=second)
    assert payload_sha256(payload=first) == payload_sha256(payload=second)


def test_parse_json_payload_returns_json_value() -> None:
    assert parse_json_payload(payload='{"a":1}') == {"a": 1}
    assert parse_json_payload(payload="null") is None


def test_parse_json_payload_rejects_non_json_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Unsupported:
        pass

    def fake_loads(payload: str) -> Unsupported:
        del payload
        return Unsupported()

    monkeypatch.setattr("fpl_data_relay.hashing.json.loads", fake_loads)
    with pytest.raises(TypeError, match="not JSON-compatible"):
        parse_json_payload(payload="{}")
