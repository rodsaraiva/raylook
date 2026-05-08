from app.services.whatsapp_domain_service import normalize_webhook_events


def _vote_update_payload(votes):
    return {
        "messages_updates": [
            {
                "id": "poll-123",
                "trigger": {
                    "from": "55 11 99999-0000",
                    "from_name": "Ana",
                    "chat_id": "120363403901156886@g.us",
                    "action": {
                        "type": "vote",
                        "target": "poll-123",
                        "votes": votes,
                    },
                },
                "after_update": {
                    "poll": {
                        "results": [
                            {"id": "opt-12", "name": "12"},
                            {"id": "opt-9", "name": "9"},
                        ]
                    }
                },
            }
        ]
    }


def test_vote_updated_event_key_changes_when_vote_payload_changes():
    added = normalize_webhook_events(_vote_update_payload(["opt-12"]))
    removed = normalize_webhook_events(_vote_update_payload([]))

    assert len(added) == 1
    assert len(removed) == 1
    assert added[0].event_key != removed[0].event_key


def test_vote_updated_event_key_stays_stable_for_identical_payload():
    payload = _vote_update_payload(["opt-12"])

    first = normalize_webhook_events(payload)
    second = normalize_webhook_events(payload)

    assert len(first) == 1
    assert len(second) == 1
    assert first[0].event_key == second[0].event_key
