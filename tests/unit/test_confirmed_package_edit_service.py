from app.services.confirmed_package_edit_service import (
    build_edit_columns,
    normalize_votes_payload,
    split_added_removed_votes,
    validate_package_total,
)


def test_build_edit_columns_excludes_votes_from_other_confirmed_packages():
    package = {
        "id": "pkg-1",
        "poll_id": "poll-A",
        "votes": [
            {"phone": "5511999990001", "name": "Ana", "qty": 12},
        ],
    }
    active_votes = [
        {"phone": "5511999990001", "name": "Ana", "qty": 12},
        {"phone": "5511999990002", "name": "Bia", "qty": 12},
        {"phone": "5511999990003", "name": "Cris", "qty": 10},
    ]
    confirmed_packages = [
        package,
        {
            "id": "pkg-2",
            "poll_id": "poll-A",
            "votes": [{"phone": "5511999990002", "name": "Bia", "qty": 12}],
        },
    ]

    available, selected = build_edit_columns(package, active_votes, confirmed_packages)

    assert [v["phone"] for v in selected] == ["5511999990001"]
    # "5511999990002" já está em outro pacote confirmado, então não pode aparecer disponível
    assert [v["phone"] for v in available] == ["5511999990003"]


def test_validate_package_total_requires_exactly_24():
    assert validate_package_total([{"qty": 10}, {"qty": 14}]) == 24
    assert validate_package_total([{"qty": 23}]) is None


def test_split_added_removed_votes_by_phone():
    current_votes = [
        {"phone": "5511999990001", "name": "Ana", "qty": 12},
        {"phone": "5511999990002", "name": "Bia", "qty": 12},
    ]
    new_votes = [
        {"phone": "5511999990002", "name": "Bia", "qty": 12},
        {"phone": "5511999990003", "name": "Cris", "qty": 12},
    ]

    added, removed = split_added_removed_votes(current_votes, new_votes)

    assert [v["phone"] for v in added] == ["5511999990003"]
    assert [v["phone"] for v in removed] == ["5511999990001"]


def test_normalize_votes_payload_keeps_only_valid_votes():
    payload_votes = [
        {"phone": "5511999990001", "name": "Ana", "qty": 12},
        {"phone": "", "name": "Sem Fone", "qty": 5},
        {"phone": "5511999990002", "name": "Zero", "qty": 0},
    ]

    normalized = normalize_votes_payload(payload_votes)

    assert normalized == [{"phone": "5511999990001", "name": "Ana", "qty": 12}]
