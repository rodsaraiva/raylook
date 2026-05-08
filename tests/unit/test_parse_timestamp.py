from metrics.processors import parse_timestamp

def test_parse_seconds_timestamp():
    ts = "1672531200"  # 2023-01-01T00:00:00Z approx
    dt = parse_timestamp(ts)
    assert dt is not None

def test_parse_milliseconds_timestamp():
    ms = str(1672531200000)
    dt = parse_timestamp(ms)
    assert dt is not None

def test_parse_iso_z():
    dt = parse_timestamp("2023-01-01T00:00:00Z")
    assert dt is not None

def test_parse_invalid_returns_none():
    assert parse_timestamp("not-a-date") is None

