"""
Web 静态托管与健康检测测试（SPA 构建产物由后端单进程全托管）。
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BASE_DIR))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def test_health_probe():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_root_serves_spa_for_browsers():
    res = client.get("/", headers={"Accept": "text/html"})
    assert res.status_code == 200
    assert "EasyWrite" in res.text
    assert 'id="app"' in res.text


def test_root_returns_json_for_api_clients():
    res = client.get("/")
    assert res.status_code == 200
    assert res.json()["status"] == "online"


def test_spa_assets_served():
    """构建产物 assets 目录存在时静态资源可达"""
    assets_dir = Path(BASE_DIR) / "app" / "static" / "assets"
    js_files = sorted(assets_dir.glob("index-*.js")) if assets_dir.exists() else []
    if not js_files:
        import pytest
        pytest.skip("前端尚未构建（assets 目录不存在）")
    res = client.get(f"/assets/{js_files[0].name}")
    assert res.status_code == 200
    assert len(res.content) > 1000


def test_spa_deep_link_fallback():
    """vue-router history 模式深链接：非 API 路径回退 SPA 入口，API 未知路径返回 JSON 404"""
    res = client.get("/project/proj_x/workspace", headers={"Accept": "text/html"})
    assert res.status_code == 200
    assert 'id="app"' in res.text

    res = client.get("/api/v1/nonexistent-endpoint")
    assert res.status_code == 404
    assert res.headers.get("content-type", "").startswith("application/json")


def test_cors_for_vite_dev():
    """开发期跨域：Vite(5173) 允许访问 API"""
    res = client.options("/api/v1/projects", headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "GET",
    })
    assert res.status_code == 200
    assert "localhost:5173" in res.headers.get("access-control-allow-origin", "")
