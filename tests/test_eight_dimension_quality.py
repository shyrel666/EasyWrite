import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BASE_DIR))

from fastapi.testclient import TestClient
from app.main import app
from app.models.schemas import (
    OutlineNode, GlobalFacts, PolishSectionRequest
)
from app.services.checker.quality_inspector import quality_inspector

client = TestClient(app)

def test_eight_dimension_inspector_standalone():
    print("[1] 单元测试：标书八维全盘质量体检引擎...")
    custom_facts = GlobalFacts(
        company_name="北京信创数智科技有限公司",
        core_product_name="XC-Hub 统一数字中台v5.0",
        database_selection="达梦数据库 DM8"
    )

    good_outline = [
        OutlineNode(
            id="sec_1",
            title="第一章 建设方案与技术路线",
            level=1,
            path="第一章 建设方案与技术路线",
            content=(
                "投标人【北京信创数智科技有限公司】依托成熟的核心产品【XC-Hub 统一数字中台v5.0】，"
                "采用微服务架构与云原生容器集群部署。系统底层全面适配【达梦数据库 DM8】。\n\n"
                "```mermaid\ngraph TD\n  Gateway[网关] --> Service[微服务]\n  Service --> DB[(达梦数据库)]\n```\n\n"
                "| 指标项 | 承诺标准 |\n| --- | --- |\n| 响应时间 | < 1秒 |\n\n"
                "核心数据支持国产商用密码算法（SM2/SM3/SM4）加密存储，完全满足等保三级要求。提供 7×24 小时技术支持。"
            )
        )
    ]

    report = quality_inspector.inspect_quality(good_outline, custom_facts)
    print(f"    质检总分: {report.overall_score}, 评级: {report.rating_level}")
    assert report.overall_score >= 80
    assert len(report.dimensions) == 8
    assert report.passed is True
    print("    -> 八维质检正向用例通过！")

    # 反向风险用例：注入 AI 廉价套话与口语化
    risky_outline = [
        OutlineNode(
            id="sec_2",
            title="第二章 概要介绍",
            level=1,
            path="第二章 概要介绍",
            content=(
                "众所周知，在当今快速发展的时代浪潮中，我们希望差不多能把系统搞定。"
                "作为一家业界领先的企业，不仅如此，而且宛如春风拂面，暂不支持部分复杂配置。"
            )
        )
    ]
    bad_report = quality_inspector.inspect_quality(risky_outline, custom_facts)
    print(f"    含AI套话与负偏离文本质检评分: {bad_report.overall_score}, 评级: {bad_report.rating_level}")
    assert bad_report.overall_score < 75
    assert len(bad_report.de_ai_detected_phrases) >= 2
    assert len(bad_report.high_risk_defects) >= 1
    print("    -> 八维质检反向风险拦截通过！")

def test_de_ai_polish_standalone():
    print("\n[2] 单元测试：深度降AI味润色引擎...")
    custom_facts = GlobalFacts(
        company_name="北京信创数智科技有限公司",
        core_product_name="XC-Hub 统一数字中台v5.0"
    )
    raw_ai_text = (
        "众所周知，在当今数字化转型的浪潮中，该模块作为一家业界领先的解决方案，"
        "不仅如此而且宛如中流砥柱，小伙伴们尽量做到在未来的日子里支持业务。"
    )
    req = PolishSectionRequest(
        project_id="test_proj",
        section_id="sec_1_1",
        content=raw_ai_text,
        polish_mode="de_ai"
    )
    resp = quality_inspector.polish_section(req, custom_facts)
    print(f"    润色前文本: {raw_ai_text}")
    print(f"    润色后文本: {resp.polished_content}")
    print(f"    改进项: {resp.improvements}")
    
    assert "众所周知" not in resp.polished_content
    assert "在当今数字化转型的浪潮中" not in resp.polished_content
    assert "北京信创数智科技有限公司" in resp.polished_content
    assert len(resp.improvements) >= 2
    print("    -> 降AI味润色引擎测试通过！")

def test_quality_and_polish_api():
    print("\n[3] 接口集成测试：质检与润色 RESTful 接口...")
    # 1. 创建项目
    proj_res = client.post("/api/v1/project/create", json={
        "name": "2026年数字工信质量体检测试项目",
        "client_name": "省工信厅"
    })
    assert proj_res.status_code == 200
    proj_id = proj_res.json()["id"]

    # 1.5 新架构：项目创建后大纲为空，需先建立章节（向导流程）
    outline = [{
        "id": "sec_1", "title": "第一章 总体方案", "level": 1, "path": "第一章 总体方案",
        "status": "completed", "requirements": [],
        "children": [{
            "id": "sec_1_1", "title": "1.1 架构设计", "level": 2, "path": "第一章 总体方案 > 1.1 架构设计",
            "status": "pending", "requirements": [], "children": [],
        }],
    }]
    outline_res = client.put(f"/api/v1/project/{proj_id}/outline", json={"outline": outline})
    assert outline_res.status_code == 200

    # 2. 单章润色
    polish_res = client.post(f"/api/v1/project/{proj_id}/section/polish", json={
        "project_id": proj_id,
        "section_id": "sec_1_1",
        "content": "众所周知，在当今快速发展的时代，我们希望尽力满足采购方要求。",
        "polish_mode": "de_ai"
    })
    assert polish_res.status_code == 200
    p_data = polish_res.json()
    assert "众所周知" not in p_data["polished_content"]

    # 3. 八维体检
    inspect_res = client.post(f"/api/v1/project/{proj_id}/quality/inspect")
    assert inspect_res.status_code == 200
    i_data = inspect_res.json()
    assert "dimensions" in i_data
    assert len(i_data["dimensions"]) == 8
    print(f"    接口返回八维体检总分: {i_data['overall_score']}")
    print("    -> 八维质检与降AI味接口验证通过！")

if __name__ == "__main__":
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    test_eight_dimension_inspector_standalone()
    test_de_ai_polish_standalone()
    test_quality_and_polish_api()
