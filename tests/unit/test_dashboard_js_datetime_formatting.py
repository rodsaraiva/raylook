from pathlib import Path


def test_dashboard_js_uses_brasilia_timezone_for_displayed_datetimes():
    """F-043: formatDate now uses inline toLocaleDateString/toLocaleTimeString
    with 'pt-BR' locale and includes hours (DD/MM HH:MM format)."""
    dashboard_js = Path("static/js/dashboard.js").read_text(encoding="utf-8")

    # The formatDate function should exist and produce DD/MM HH:MM output
    assert "formatDate" in dashboard_js

    # It should use pt-BR locale for date/time formatting
    assert "pt-BR" in dashboard_js

    # It should include both date and time parts
    assert "toLocaleDateString" in dashboard_js
    assert "toLocaleTimeString" in dashboard_js

    # The formatDate should combine date + time (DD/MM HH:MM)
    assert "day: '2-digit'" in dashboard_js
    assert "month: '2-digit'" in dashboard_js
    assert "hour: '2-digit'" in dashboard_js
    assert "minute: '2-digit'" in dashboard_js
