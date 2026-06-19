from app.services.label_token import make_ship_token, read_ship_token


def test_token_roundtrip(monkeypatch):
    monkeypatch.setenv("LABEL_QR_SECRET", "s3cr3t")
    tok = make_ship_token("pac-1", "cli-1")
    assert read_ship_token(tok) == ("pac-1", "cli-1")


def test_token_tampered_returns_none(monkeypatch):
    monkeypatch.setenv("LABEL_QR_SECRET", "s3cr3t")
    tok = make_ship_token("pac-1", "cli-1")
    body, sig = tok.split(".", 1)
    forged = body + "x." + sig  # corpo alterado, assinatura não confere
    assert read_ship_token(forged) is None


def test_token_wrong_secret_returns_none(monkeypatch):
    monkeypatch.setenv("LABEL_QR_SECRET", "secretA")
    tok = make_ship_token("pac-1", "cli-1")
    monkeypatch.setenv("LABEL_QR_SECRET", "secretB")
    assert read_ship_token(tok) is None


def test_token_malformed_returns_none(monkeypatch):
    monkeypatch.setenv("LABEL_QR_SECRET", "s3cr3t")
    assert read_ship_token("garbage") is None
    assert read_ship_token("") is None
    assert read_ship_token("a.b.c") is None
