import pytest

from fpl_data_relay.domain.changes import (
    ChangeKind,
    EntitySnapshot,
    diff_entity_snapshots,
)


def snapshot(
    *,
    key: str,
    label: str,
    data: dict[str, object],
) -> EntitySnapshot:
    return EntitySnapshot.model_validate(
        {"entity_key": key, "entity_label": label, "data": data},
    )


def test_entity_diff_reports_top_level_updates_and_explicit_nulls() -> None:
    diff = diff_entity_snapshots(
        previous=[
            snapshot(
                key="1",
                label="Player",
                data={"now_cost": 50, "news": "Injured", "nested": [1, 2]},
            ),
        ],
        current=[
            snapshot(
                key="1",
                label="Player renamed",
                data={"now_cost": 51, "news": None, "nested": [2, 3]},
            ),
        ],
        authoritative=True,
        baseline=False,
    )

    assert diff.created_count == 0
    assert diff.updated_count == 1
    assert diff.deleted_count == 0
    assert diff.changes[0].entity_label == "Player renamed"
    assert [field.field for field in diff.changes[0].fields] == [
        "nested",
        "news",
        "now_cost",
    ]
    news = diff.changes[0].fields[1]
    assert news.before.present is True
    assert news.before.value == "Injured"
    assert news.after.present is True
    assert news.after.value is None


def test_entity_diff_reports_creates_and_authoritative_deletes() -> None:
    diff = diff_entity_snapshots(
        previous=[snapshot(key="1", label="Old", data={"id": 1})],
        current=[snapshot(key="2", label="New", data={"id": 2})],
        authoritative=True,
        baseline=False,
    )

    assert [change.kind for change in diff.changes] == [
        ChangeKind.DELETED,
        ChangeKind.CREATED,
    ]
    assert diff.changes[0].fields[0].after.present is False
    assert diff.changes[1].fields[0].before.present is False


def test_entity_diff_keeps_missing_entities_for_partial_snapshots() -> None:
    diff = diff_entity_snapshots(
        previous=[snapshot(key="1", label="Existing", data={"id": 1})],
        current=[],
        authoritative=False,
        baseline=False,
    )
    assert diff.changes == []


def test_entity_diff_suppresses_initial_baseline() -> None:
    diff = diff_entity_snapshots(
        previous=[],
        current=[snapshot(key="1", label="Initial", data={"id": 1})],
        authoritative=True,
        baseline=True,
    )
    assert diff.changes == []


def test_entity_diff_rejects_duplicate_keys() -> None:
    duplicate = snapshot(key="1", label="Duplicate", data={"id": 1})
    with pytest.raises(ValueError, match="Duplicate entity key"):
        diff_entity_snapshots(
            previous=[],
            current=[duplicate, duplicate],
            authoritative=True,
            baseline=False,
        )
