import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BASE_DIR))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_web_frontend_serving():
    print("[1] 测试首页 Web 工作台直出 (HTML Request)...")
    res = client.get("/", headers={"Accept": "text/html"})
    assert res.status_code == 200
    assert "EasyWrite 智能招投标技术标编纂系统" in res.text
    assert 'id="app"' in res.text
    print("    -> 浏览器访问首页顺利返回 Web 工作台 HTML！")

    print("\n[2] 测试静态资源访问 (/static/app.js)...")
    js_res = client.get("/static/app.js")
    assert js_res.status_code == 200
    assert "createApp" in js_res.text
    assert "fetchInitialProject" in js_res.text
    print("    -> 静态资源 app.js 成功加载！")

    print("\n[3] 测试接口健康探针 (/health)...")
    health_res = client.get("/health")
    assert health_res.status_code == 200
    assert health_res.json()["status"] == "ok"
    print("    -> 健康探针 /health 正常！")

    print("\n[SUCCESS] Web 前后端整合服务全套验证通过！")

if __name__ == "__main__":
    test_web_frontend_serving()
