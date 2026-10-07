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

    # 联动：采购人回填 client_name；工期/质保只作为承诺建议返回，全局事实保持不变
    stored = project_store.get(pid)
    assert "水务环境集团" in stored.client_name
    assert stored.facts.delivery_guarantee == ""
    assert stored.facts.sla_commitment == ""
    suggestions = {s["field"]: s for s in res.json()["commitment_suggestions"]}
    assert "120个日历日" in suggestions["delivery_guarantee"]["suggestion"]
    assert "5年驻场质保" in suggestions["sla_commitment"]["suggestion"]
    # 建议措辞只复述招标原文
    for s in suggestions.values():
        assert s["requirement"] in TENDER_SAMPLE
        assert s["suggestion"] == f"按招标要求，{s['requirement']}"
    res = client.get(f"/api/v1/project/{pid}/tender/commitment-suggestions")
    assert res.json()["suggestions"] == list(suggestions.values())

    # 招标正文存档（偏离表抽取数据源）
    tender_text = project_store.get_tender_text(pid)
    assert "SW-2026-ZB-088" in tender_text

    # 用户已填的事实不被覆盖（再次应用也不改动全局事实）
    stored.facts.delivery_guarantee = "用户自定义工期承诺"
    project_store.save(stored)
    client.post(f"/api/v1/project/{pid}/tender/apply",
                json={"analysis": analysis, "tender_text": TENDER_SAMPLE})
    assert project_store.get(pid).facts.delivery_guarantee == "用户自定义工期承诺"

    client.delete(f"/api/v1/project/{pid}")


def test_commitment_suggestions_only_restate_tender():
    """建议只复述招标要求：不补出 7×24 等原文没有的承诺；未提及的要求不生成建议"""
    from app.models.schemas import NOT_MENTIONED, TenderAnalysis18
    from app.services.parser.commitments import suggest_commitments

    items = suggest_commitments(TenderAnalysis18(duration_requirement="90日历天", warranty_period="1年"))
    assert [(s.field, s.suggestion) for s in items] == [
        ("delivery_guarantee", "按招标要求，工期为90日历天"),
        ("sla_commitment", "按招标要求，质保期为1年"),
    ]
    assert not any("7×24" in s.suggestion for s in items)
    # 原文已是完整表述（如"…服务1年"）时直接沿用，不再套"工期为…"
    item = suggest_commitments(TenderAnalysis18(duration_requirement="中标人在采购合同签订后服务1年"))[0]
    assert item.suggestion == "按招标要求，中标人在采购合同签订后服务1年"
    assert suggest_commitments(TenderAnalysis18(duration_requirement=NOT_MENTIONED)) == []
    assert suggest_commitments(None) == []


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
