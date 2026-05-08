import io
from fastapi.testclient import TestClient


def _boot_app(monkeypatch):
    monkeypatch.setenv("ADHOC_PACKAGES_ENABLED", "true")
    monkeypatch.setenv("DASHBOARD_AUTH_DISABLED", "true")
    import importlib
    import app.config as config
    importlib.reload(config)
    import main as main_module
    importlib.reload(main_module)
    return main_module.app


def _png_bytes() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color="red").save(buf, format="PNG")
    return buf.getvalue()


def test_upload_accepts_png(monkeypatch):
    app = _boot_app(monkeypatch)

    captured = {}

    class FakeDriveClient:
        def __init__(self, *a, **kw):
            pass
        def upload_file(self, name, content_bytes, parent_folder_id, mime_type="image/jpeg"):
            captured["name"] = name
            captured["mime"] = mime_type
            captured["size"] = len(content_bytes)
            return "FAKE_DRIVE_ID"
        def get_public_url(self, file_id):
            return f"https://lh3.googleusercontent.com/d/{file_id}"

    monkeypatch.setattr("app.api.adhoc_packages.GoogleDriveClient", FakeDriveClient)

    client = TestClient(app)
    response = client.post(
        "/api/packages/adhoc/upload-image",
        files={"image": ("product.png", _png_bytes(), "image/png")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["drive_file_id"] == "FAKE_DRIVE_ID"
    assert body["full_url"].endswith("FAKE_DRIVE_ID")
    assert captured["mime"] == "image/png"


def test_upload_rejects_unsupported_mime(monkeypatch):
    app = _boot_app(monkeypatch)
    client = TestClient(app)
    response = client.post(
        "/api/packages/adhoc/upload-image",
        files={"image": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert response.status_code == 415


def test_upload_rejects_oversized(monkeypatch):
    app = _boot_app(monkeypatch)
    client = TestClient(app)
    big = b"\x00" * (6 * 1024 * 1024)  # 6MB
    response = client.post(
        "/api/packages/adhoc/upload-image",
        files={"image": ("big.png", big, "image/png")},
    )
    assert response.status_code == 413


def test_upload_rejects_corrupt_image(monkeypatch):
    app = _boot_app(monkeypatch)
    client = TestClient(app)
    response = client.post(
        "/api/packages/adhoc/upload-image",
        files={"image": ("fake.png", b"not-an-image", "image/png")},
    )
    assert response.status_code == 400
