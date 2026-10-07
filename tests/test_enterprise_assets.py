import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BASE_DIR))

from fastapi.testclient import TestClient
from app.main import app
from app.services.assets.asset_manager import asset_manager

client = TestClient(app)

def test_asset_manager_standalone():
    print("[1] 单元测试：企业中台四大核心资产库...")
    stats = asset_manager.get_stats()
    assert stats["total_qualifications"] >= 3
    assert stats["total_personnel"] >= 3
    assert stats["total_cases"] >= 2
    assert stats["total_components"] >= 3
    print(f"    当前资产统计: {stats}")

    # 1. 资质库匹配
    quals = asset_manager.list_qualifications(search="CMMI")
    assert len(quals) >= 1
    assert "CMMI" in quals[0].name
    print(f"    CMMI 资质查询命中: {quals[0].name}")

    # 2. 人员库匹配
    personnel = asset_manager.list_personnel(role="架构师")
    assert len(personnel) >= 1
    assert "架构师" in personnel[0].role
    print(f"    架构师人员查询命中: {personnel[0].name} ({personnel[0].role})")

    # 3. 智能撰写资产关联匹配
    team_match = asset_manager.match_assets_for_section("4.1 实施团队人员配置", ["项目经理资质要求"])
    assert team_match["type"] == "personnel"
    assert len(team_match["items"]) >= 2
    assert "拟任核心团队配置" in team_match["context_text"]
    print("    章节资产智能关联测试通过: 识别出团队人员资产")

    qual_match = asset_manager.match_assets_for_section("1.3 投标人资质条件响应", ["CMMI认证", "涉密资质"])
    assert qual_match["type"] == "qualification"
    assert "已认证核心资质清单" in qual_match["context_text"]
    print("    章节资产智能关联测试通过: 识别出企业资质资产")

def test_asset_api_integration():
    print("\n[2] 接口集成测试：企业资产中台 RESTful 接口...")
    # 1. 统计概览
    res = client.get("/api/v1/assets/stats")
    assert res.status_code == 200
    assert "total_qualifications" in res.json()

    # 2. 新增与查询资质
    new_qual = {
        "id": "qual_test_itss",
        "name": "信息系统建设能力评估 CS3 级",
        "cert_no": "CS-2025-0099",
        "category": "综合资质",
        "level": "CS3",
        "issue_org": "中国电子行业联合会",
        "summary": "重大工程能力评估"
    }
    create_res = client.post("/api/v1/assets/qualifications", json=new_qual)
    assert create_res.status_code == 200
    assert create_res.json()["name"] == new_qual["name"]

    list_res = client.get("/api/v1/assets/qualifications?search=CS3")
    assert list_res.status_code == 200
    assert len(list_res.json()) >= 1

    # 3. 删除测试资质
    del_res = client.delete("/api/v1/assets/qualifications/qual_test_itss")
    assert del_res.status_code == 200
    print("    -> 企业中台资产管理接口全套验证通过！")

def test_missing_file_seeds_examples_once(tmp_path):
    """资料文件不存在（首次启动）时才写入预设示例"""
    from app.services.assets.asset_manager import EnterpriseAssetManager
    path = tmp_path / "enterprise_assets.json"
    manager = EnterpriseAssetManager(path)
    assert path.exists()
    assert len(manager.qualifications) == len(EnterpriseAssetManager.DEFAULT_QUALIFICATIONS)
    # 示例条目是深拷贝：修改实例数据不能污染代码中的预设常量
    manager.personnel[0]["name"] = "改名"
    assert EnterpriseAssetManager.DEFAULT_PERSONNEL[0]["name"] != "改名"


def test_emptied_category_survives_restart(tmp_path):
    """某一类被删空后重启：该类保持为空，其他类（含用户录入）不被示例重置"""
    from app.models.schemas import PersonnelAsset
    from app.services.assets.asset_manager import EnterpriseAssetManager
    path = tmp_path / "enterprise_assets.json"
    manager = EnterpriseAssetManager(path)
    manager.add_personnel(PersonnelAsset(id="person_user", name="王工", role="项目经理"))
    for q in list(manager.qualifications):
        assert manager.delete_qualification(q["id"])
    assert manager.qualifications == []

    reloaded = EnterpriseAssetManager(path)
    assert reloaded.qualifications == []
    assert any(p["id"] == "person_user" for p in reloaded.personnel)
    assert len(reloaded.cases) == len(EnterpriseAssetManager.DEFAULT_CASES)


def test_corrupt_file_is_backed_up_not_reset(tmp_path):
    """文件损坏：原文件备份，资料库以空库启动，不用示例覆盖；之后重启也不补入示例"""
    from app.services.assets.asset_manager import EnterpriseAssetManager
    path = tmp_path / "enterprise_assets.json"
    path.write_text('{"qualifications": [{"id": "q1"', encoding="utf-8")
    manager = EnterpriseAssetManager(path)
    assert manager.get_stats() == {"total_qualifications": 0, "total_personnel": 0,
                                   "total_cases": 0, "total_components": 0}
    backups = list(tmp_path.glob("enterprise_assets.json.corrupt-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == '{"qualifications": [{"id": "q1"'
    assert EnterpriseAssetManager(path).get_stats()["total_qualifications"] == 0


def test_failed_save_keeps_memory_and_file_consistent(tmp_path, monkeypatch):
    """写盘失败：抛出异常，内存回到原状态，文件保持原内容"""
    import pytest
    from app.models.schemas import CaseContract
    from app.services.assets import asset_manager as module
    path = tmp_path / "enterprise_assets.json"
    manager = module.EnterpriseAssetManager(path)
    before_file = path.read_text(encoding="utf-8")
    before_cases = list(manager.cases)

    def broken_replace(*_args, **_kwargs):
        raise OSError("磁盘已满")

    monkeypatch.setattr(module.os, "replace", broken_replace)
    with pytest.raises(OSError):
        manager.add_case(CaseContract(id="case_x", project_name="某项目", client_name="某单位", contract_amount="10 万元"))
    assert manager.cases == before_cases
    assert path.read_text(encoding="utf-8") == before_file
    assert not list(tmp_path.glob("*.tmp"))


if __name__ == "__main__":
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    test_asset_manager_standalone()
    test_asset_api_integration()
