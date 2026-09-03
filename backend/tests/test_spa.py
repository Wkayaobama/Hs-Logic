from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import mount_spa


def _build(tmp_path: Path, with_bundle: bool = True) -> tuple[FastAPI, bool]:
    app = FastAPI()

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    if with_bundle:
        (tmp_path / "assets").mkdir()
        (tmp_path / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
        (tmp_path / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
        (tmp_path / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    mounted = mount_spa(app, tmp_path)
    return app, mounted


def test_no_bundle_is_a_noop(tmp_path):
    app, mounted = _build(tmp_path, with_bundle=False)
    assert mounted is False
    client = TestClient(app)
    assert client.get("/api/health").status_code == 200
    assert client.get("/").status_code == 404


def test_root_serves_index(tmp_path):
    app, mounted = _build(tmp_path)
    assert mounted is True
    res = TestClient(app).get("/")
    assert res.status_code == 200
    assert '<div id="root">' in res.text


def test_client_route_falls_back_to_index(tmp_path):
    app, _ = _build(tmp_path)
    res = TestClient(app).get("/contacts/some/deep/link")
    assert res.status_code == 200
    assert '<div id="root">' in res.text


def test_static_file_at_root_is_served(tmp_path):
    app, _ = _build(tmp_path)
    res = TestClient(app).get("/favicon.svg")
    assert res.status_code == 200
    assert res.text == "<svg/>"


def test_assets_are_served(tmp_path):
    app, _ = _build(tmp_path)
    res = TestClient(app).get("/assets/app.js")
    assert res.status_code == 200
    assert res.text == "console.log(1)"


def test_api_routes_keep_precedence(tmp_path):
    app, _ = _build(tmp_path)
    client = TestClient(app)
    assert client.get("/api/health").json() == {"status": "ok"}
    missing = client.get("/api/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Not Found"


def test_traversal_never_leaves_static_dir(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    app, _ = _build(tmp_path)
    res = TestClient(app).get("/../outside.txt")
    assert res.status_code == 200
    assert "secret" not in res.text


def test_real_app_health_without_bundle():
    from app.main import app

    res = TestClient(app).get("/api/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
