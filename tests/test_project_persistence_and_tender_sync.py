"""
项目持久化（SQLite）与拆标联动测试。
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BASE_DIR))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.services.project_store import project_store  # noqa: E402
from conftest import TENDER_SAMPLE  # noqa: E402

client = TestClient(app)


def test_project_crud_and_persistence():
    res = client.post("/api/v1/project/create", json={
        "name": "持久化测试项目", "client_name": "省水利勘测设计院",
        "description": "物联监测调度系统",
    })
    assert res.status_code == 200
    pid = res.json()["id"]

    # 列表可见
    res = client.get("/api/v1/projects")
    assert any(p["id"] == pid for p in res.json())

    # 详情读取
    res = client.get(f"/api/v1/project/{pid}")
    assert res.status_code == 200
    assert res.json()["client_name"] == "省水利勘测设计院"

    # 事实更新持久化（SQLite 而非内存态）
    facts = res.json()["facts"]
    facts["company_name"] = "持久化测试科技有限公司"
    res = client.put(f"/api/v1/project/{pid}/facts", json=facts)
    assert res.status_code == 200
    # 重新从 DB 读取验证
    stored = project_store.get(pid)
    assert stored.facts.company_name == "持久化测试科技有限公司"

    # 删除
    res = client.delete(f"/api/v1/project/{pid}")
    assert res.status_code == 200
    assert project_store.get(pid) is None


def test_tender_apply_and_facts_linkage():
    res = client.post("/api/v1/project/create", json={
        "name": "拆标联动测试项目", "description": "智慧水务项目",
    })
    pid = res.json()["id"]

    analysis = client.post("/api/v1/tender/analyze/text", json={"text": TENDER_SAMPLE}).json()
    res = client.post(f"/api/v1/project/{pid}/tender/apply",
                      json={"analysis": analysis, "tender_text": TENDER_SAMPLE})
    assert res.status_code == 200
    assert res.json()["stage"] == "tender_analyzed"
    assert res.json()["star_count"] >= 3

    # 联动：采购人回填 client_name、工期/质保联动全局事实（只补空字段）
    stored = project_store.get(pid)
    assert "水务环境集团" in stored.client_name
    assert "120个日历日" in stored.facts.delivery_guarantee
    assert "5年驻场质保" in stored.facts.sla_commitment

    # 招标正文存档（偏离表抽取数据源）
    tender_text = project_store.get_tender_text(pid)
    assert "SW-2026-ZB-088" in tender_text

    # 用户已填的事实不被覆盖
    stored.facts.delivery_guarantee = "用户自定义工期承诺"
    project_store.save(stored)
    client.post(f"/api/v1/project/{pid}/tender/apply",
                json={"analysis": analysis, "tender_text": TENDER_SAMPLE})
    assert project_store.get(pid).facts.delivery_guarantee == "用户自定义工期承诺"

    client.delete(f"/api/v1/project/{pid}")


def test_wizard_stage_machine():
    res = client.post("/api/v1/project/create", json={"name": "阶段机测试项目"})
    pid = res.json()["id"]

    # 非法阶段拒绝
    res = client.put(f"/api/v1/project/{pid}/stage", json={"stage": "not_a_stage"})
    assert res.status_code == 400

    # 合法流转
    for stage in ("tender_analyzed", "outline_confirmed", "writing"):
        res = client.put(f"/api/v1/project/{pid}/stage", json={"stage": stage})
        assert res.status_code == 200
        assert res.json()["stage"] == stage
        assert project_store.get(pid).stage == stage

    client.delete(f"/api/v1/project/{pid}")
