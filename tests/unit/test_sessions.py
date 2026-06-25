from app.sessions import session_for_title, accumulate_session_for_title, SESSIONS


def test_match_case_insensitive_substring():
    assert session_for_title("Promo BERNARDO 24/06")["name"] == "Bernardo"
    assert session_for_title("bernardo")["name"] == "Bernardo"


def test_no_match_returns_none():
    assert session_for_title("Camisa básica") is None
    assert session_for_title("") is None
    assert session_for_title(None) is None


def test_accumulate_helper_filters_by_mode():
    assert accumulate_session_for_title("Bernardo lote 1")["mode"] == "accumulate"
    assert accumulate_session_for_title("nada") is None


def test_default_session_is_bernardo_accumulate():
    assert any(s["name"] == "Bernardo" and s["mode"] == "accumulate" for s in SESSIONS)
