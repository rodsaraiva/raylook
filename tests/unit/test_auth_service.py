import bcrypt
from app.services import auth_service as auth


def test_bernardo_is_a_role():
    assert "bernardo" in auth.ROLES


def test_visible_groups_bernardo_only_sees_bernardo():
    assert auth.visible_groups("bernardo") == ("bernardo",)


def test_visible_groups_admin_includes_bernardo_and_clientes():
    g = auth.visible_groups("admin")
    assert "bernardo" in g and "clientes" in g and "comercial" in g


def test_visible_groups_stock_keeps_enquetes_and_clientes():
    assert auth.visible_groups("estoque") == ("estoque", "enquetes", "clientes")
    assert auth.visible_groups("logistica") == ("logistica", "enquetes", "clientes")


def test_verify_credentials_bernardo(monkeypatch):
    h = bcrypt.hashpw(b"Bernard0", bcrypt.gensalt()).decode()
    monkeypatch.setenv("RAYLOOK_USER_BERNARDO_HASH", h)
    assert auth.verify_credentials("bernardo", "Bernard0") == "bernardo"
    assert auth.verify_credentials("bernardo", "errada") is None


def test_bernardo_cannot_cancel():
    assert auth.can_cancel("bernardo") is False
