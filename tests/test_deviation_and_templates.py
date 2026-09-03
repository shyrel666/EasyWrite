import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BASE_DIR))

from fastapi.testclient import TestClient
from app.main import app
from app.models.schemas import GlobalFacts, DeviationItem
from app.services.parser.deviation_engine import deviation_engine
from app.services.exporter.template_manager import template_manager

client = TestClient(app)

def test_deviation_engine_standalone():
    print("[1] 单元测试：技术偏离表提取与点对点应答引擎...")
    sample_text = """
    技术条款清单：
    ★ 1. 系统必须具备二级等保或三级等保合规能力，核心数据加密存储。
    2. 支持与第三方OA协同办公系统通过 RESTful 接口单点登录（SSO）无缝集成。
    ★ 3. 数据库必须支持国产信创数据库（达梦DM8/人大金仓），不支持外资闭源数据库。
    4. 性能要求：在1000并发用户下，核心业务页面加载响应时间不超过1.5秒。
    5. 售后要求：提供 7×24 小时技术保障，关键问题15分钟内给出处置方案。
    """
    items = deviation_engine.extract_requirements(sample_text)
    print(f"    成功提取技术条款数: {len(items)}")
    assert len(items) >= 4
    
    star_items = [it for it in items if it.is_star]
    print(f"    其中识别出的★号条款数: {len(star_items)}")
    assert len(star_items) >= 2

    # 批量生成点对点响应
    facts = GlobalFacts(
        company_name="智信数安科技集团",
        core_product_name="SecPlatform 信安政企协同底座v7.0",
        database_selection="达梦数据库 DM8 原厂认证"
    )
    answered_items = deviation_engine.batch_generate_responses(items, facts)
    print(f"    首项条款应答状态: {answered_items[0].response_status}")
    print(f"    首项条款论述内容: {answered_items[0].response_detail[:60]}...")
    assert "智信数安" in answered_items[0].response_detail or "完全满足" in answered_items[0].response_status

    # 生成 Markdown 表格
    md_table = deviation_engine.to_markdown_table(answered_items)
    assert "| 序号 |" in md_table
    assert "| ★号核心条款 |" in md_table
    print("    -> 偏离表提取与应答引擎测试通过！")

def test_template_manager_standalone():
    print("\n[2] 单元测试：Word 模板持久化管理服务...")
    templates = template_manager.list_templates()
    print(f"    当前内置模板总数: {len(templates)}")
    assert len(templates) >= 3

    # 创建金融专属新模板
    new_tpl = {
        "id": "tpl_fin_gold",
        "name": "银行金融机构黄金典雅模板",
        "primary_font": "仿宋",
        "heading_font": "黑体",
        "theme_color": "#B8860B",
        "margin_top": 2.8,
        "margin_bottom": 2.8,
        "margin_left": 3.2,
        "margin_right": 2.6,
        "header_text": "商业银行核心系统投标专版"
    }
    saved = template_manager.create_or_update_template(new_tpl)
    print(f"    成功保存自定义模板: {saved['name']}, ID: {saved['id']}")
    
    cfg = template_manager.get_template("tpl_fin_gold")
    assert cfg.template_name == "银行金融机构黄金典雅模板"
    assert cfg.theme_rgb == (184, 134, 11)
    print("    -> 模板管理与动态配置测试通过！")

def test_api_integration():
    print("\n[3] 接口集成测试：偏离表抽取、批量应答与带偏离表的标书导出...")
    # 1. 创建标书项目
    res = client.post("/api/v1/project/create", json={
        "name": "某商业银行信创协同云平台采购项目",
        "client_name": "华夏联合商业银行",
        "description": "★ 系统必须支持国密加密算法。支持国产达梦数据库。提供7x24小时现场技术保障。"
    })
    assert res.status_code == 200
    proj_id = res.json()["id"]

    # 2. 抽取偏离表项
    ext_res = client.post(f"/api/v1/project/{proj_id}/deviation/extract", json={})
    assert ext_res.status_code == 200
    ext_data = ext_res.json()
    print(f"    API 偏离表项提取成功，共计 {ext_data['total_items']} 条")
    assert ext_data["total_items"] >= 3

    # 3. 批量生成应答并回填至大纲节点 sec_4_1
    gen_dev_res = client.post(
        f"/api/v1/project/{proj_id}/deviation/generate?auto_inject_section_id=sec_4_1"
    )
    assert gen_dev_res.status_code == 200
    gen_dev_data = gen_dev_res.json()
    assert "table_markdown" in gen_dev_data
    print(f"    API 点对点偏离表批量生成完成，已自动注入大纲 sec_4_1")

    # 4. 采用自定义模板导出 Word
    export_res = client.get(f"/api/v1/project/{proj_id}/export?template_id=tpl_fin_gold")
    assert export_res.status_code == 200
    assert len(export_res.content) > 10000
    print(f"    带完整原生技术偏离表与自定义模板的 Word 导出成功，文件大小: {len(export_res.content)} 字节")
    print("    -> 接口集成测试全流程通过！")

def run_all():
    test_deviation_engine_standalone()
    test_template_manager_standalone()
    test_api_integration()
    print("\n[SUCCESS] 技术偏离表与模板系统测试套件全部通过！")

if __name__ == "__main__":
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    run_all()
