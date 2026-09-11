from fastapi.testclient import TestClient

from app.main import create_app


def test_built_frontend_can_share_the_api_process(settings, tmp_path):
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<main>Flame Console</main>", encoding="utf-8")
    (assets / "app.js").write_text("console.log('flame')", encoding="utf-8")
    runtime_settings = settings.model_copy(
        update={"serve_frontend": True, "frontend_dist_dir": dist}
    )

    app = create_app(runtime_settings)
    with TestClient(app) as client:
        root = client.get("/", headers={"Accept": "text/html"})
        spa_fallback = client.get("/account", headers={"Accept": "text/html"})
        asset = client.get("/assets/app.js")
        health = client.get("/health")

    assert root.status_code == 200
    assert "Flame Console" in root.text
    assert spa_fallback.status_code == 200
    assert "Flame Console" in spa_fallback.text
    assert asset.status_code == 200
    assert asset.text == "console.log('flame')"
    assert health.status_code == 200
    assert health.json()["database"] == "ready"
