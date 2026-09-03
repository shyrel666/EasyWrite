import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BASE_DIR))

from fastapi.testclient import TestClient
from app.main import app
from app.models.schemas import TenderAnalysis18

client = TestClient(app)

def test_project_crud_and_persistence():
    print("[1] 测试项目生命周期管理与本地持久化...")
    # 1. 创建新项目
    proj_name = "持久化测试_智能物联水利工程"
    res = client.post("/api/v1/project/create", json={
        "name": proj_name,
        "client_name": "省水利勘测设计院",
        "description": "物联监测调度系统"
    })
    assert res.status_code == 200
    proj = res.json()
    proj_id = proj["id"]
    print(f"    成功创建项目: {proj_id}")

    # 2. 查询项目列表
    list_res = client.get("/api/v1/projects")
    assert list_res.status_code == 200
    projects = list_res.json()
    assert any(p["id"] == proj_id for p in projects)
    matched_p = next(p for p in projects if p["id"] == proj_id)
    assert matched_p["name"] == proj_name
    print(f"    项目列表查询验证成功，共 {len(projects)} 个项目")

    # 3. 18项拆标数据一键同步至项目全局事实
    sample_analysis = {
        "project_name": proj_name,
        "tender_number": "SL-2026-001",
        "purchaser_name": "省水利勘测设计院",
        "budget_limit": "1200.00万元",
        "duration_requirement": "合同生效后 150 个日历日",
        "warranty_period": "终验合格后 5 年原厂免费维保",
        "star_disqualification_items": [
            "★ 投标人必须具备水利水文信息化甲级资质或高新技术企业认证。",
            "★ 核心数据传输必须支持国密SM4加密。"
        ]
    }
    sync_facts_res = client.post(f"/api/v1/project/{proj_id}/tender/sync-to-facts", json=sample_analysis)
    assert sync_facts_res.status_code == 200
    synced_facts = sync_facts_res.json()["facts"]
    assert "150 个日历日" in synced_facts["delivery_guarantee"]
    assert "5 年原厂免费维保" in synced_facts["sla_commitment"]
    print("    18项拆标一键同步至全局事实验证成功！")

    # 4. ★号条款一键同步至技术偏离表
    sync_dev_res = client.post(f"/api/v1/project/{proj_id}/tender/sync-to-deviations")
    assert sync_dev_res.status_code == 200
    sync_dev_data = sync_dev_res.json()
    assert sync_dev_data["added_count"] == 2
    
    dev_list_res = client.get(f"/api/v1/project/{proj_id}/deviation")
    assert dev_list_res.status_code == 200
    assert len(dev_list_res.json()["items"]) >= 2
    print("    18项拆标★号项一键同步至偏离表验证成功！")

    # 5. 删除项目
    del_res = client.delete(f"/api/v1/project/{proj_id}")
    assert del_res.status_code == 200
    print("    项目删除接口验证成功！")

if __name__ == "__main__":
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    test_project_crud_and_persistence()
