import pytest
from estoque.pdf_builder import render_label_html, build_pdf

PACKAGE = {
    "id": "p1",
    "friendly_id": "R-12",
    "poll_title": "Blusas",
    "votes": [
        {"name": "Maria", "phone": "5562999990000", "qty": 3, "cliente_id": "c1"},
        {"name": "Ana", "phone": "5562988880000", "qty": 5, "cliente_id": "c2"},
    ],
}


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("LABEL_QR_SECRET", "test-secret")
    monkeypatch.setenv("DOMAIN_HOST", "raylook.v4smc.com")


def test_termica_html_one_block_per_client():
    html = render_label_html(PACKAGE, formato="termica", w_mm=60, h_mm=40)
    assert html.count('class="label"') == 2
    assert "60mm 40mm" in html  # @page size aplicado


def test_termica_html_has_qr_img_per_client():
    html = render_label_html(PACKAGE, formato="termica")
    assert html.count("<img") == 2
    assert "data:image/png;base64," in html


def test_termica_build_pdf_returns_pdf_bytes():
    pdf = build_pdf(PACKAGE, 5.0, formato="termica", w_mm=60, h_mm=40)
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 500


def test_a4_still_default():
    html = render_label_html(PACKAGE)  # formato="a4"
    assert "size: A4" in html or "size:A4" in html.replace(" ", "") or "A4" in html
