"""
技术偏离表与模板系统测试（新行为）：
- 确定性抽取（★条款置顶、来源指纹、覆盖率核算、绝不编造缺省项）
- LLM 不可用时响应状态为"待生成"（不伪造"完全满足"声明）
- 手编落库与回填
- Word 模板管理与导出
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BASE_DIR))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.models.schemas import GlobalFacts  # noqa: E402
from app.services.parser.deviation_engine import deviation_engine  # noqa: E402
from app.services.exporter.template_manager import template_manager  # noqa: E402

client = TestClient(app)

SAMPLE = """
技术条款清单：
★ 1. 系统必须具备等保三级合规能力，核心数据加密存储。
2. 支持与第三方OA系统通过 RESTful 接口单点登录（SSO）集成。
★ 3. 数据库必须支持国产信创数据库（达梦DM8/人大金仓）。
4. 性能要求：1000并发用户下核心页面响应时间不超过1.5秒。
5. 售后要求：提供7×24小时技术保障。
"""


def test_extract_no_fabrication():
    items = deviation_engine.extract_requirements(SAMPLE)
    assert len(items) >= 4
    stars = [it for it in items if it.is_star]
    assert len(stars) >= 2
    # ★条款置顶排序
    assert items[0].is_star and items[1].is_star
    # 指纹溯源字段
    assert all(it.source_fingerprint for it in items)
    # 响应状态：未生成而非伪造"完全满足"
    assert all(it.response_status == "待生成" for it in items)


def test_extract_empty_honest():
    """无技术要求文本：返回空列表，不填充假条款"""
    items = deviation_engine.extract_requirements("本段文字不包含任何技术指标要求。")
    assert items == []


def test_generate_response_no_fabrication_without_llm():
    item = deviation_engine.extract_requirements(SAMPLE)[0]
    result = deviation_engine.generate_response_for_item(item, GlobalFacts())
    # 无 LLM：状态保持待生成，响应为空（响应声明是法律承诺，不可编造）
    assert result.response_status == "待生成"
    assert result.response_detail == ""


def test_markdown_table_reflects_real_status():
    items = deviation_engine.extract_requirements(SAMPLE)
    md = deviation_engine.to_markdown_table(items)
    assert "| 序号 |" in md
    assert "待生成" in md
    assert "注意" in md, "存在未生成响应时总结必须提示，不得无条件声明无偏离"


def test_coverage_report():
    items = deviation_engine.extract_requirements(SAMPLE)
    report = deviation_engine.coverage_report(items, SAMPLE)
    assert report["extracted_total"] >= 4
    assert report["star_in_tender"] >= 2


def test_template_manager():
    templates = template_manager.list_templates()
    assert len(templates) >= 3

    new_tpl = {
        "id": "tpl_test_gold", "name": "测试金色模板",
        "primary_font": "仿宋", "heading_font": "黑体", "theme_color": "#B8860B",
        "margin_top": 2.8, "margin_bottom": 2.8, "margin_left": 3.2, "margin_right": 2.6,
        "header_text": "测试模板页眉",
    }
    template_manager.create_or_update_template(new_tpl)
    cfg = template_manager.get_template("tpl_test_gold")
    assert cfg.theme_rgb == (184, 134, 11)
    assert cfg.header_text == "测试模板页眉"
    # 内置模板不可删除
    assert template_manager.delete_template("gov_standard") is False
    assert template_manager.delete_template("tpl_test_gold") is True


def test_api_deviation_flow():
    res = client.post("/api/v1/project/create", json={
        "name": "偏离表API测试项目", "client_name": "测试采购人",
        "description": SAMPLE,
    })
    assert res.status_code == 200
    pid = res.json()["id"]

    # 提取（无招标正文时用 description 兜底）
    res = client.post(f"/api/v1/project/{pid}/deviation/extract", json={"tender_content": SAMPLE})
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) >= 4

    # 手编落库
    items[0]["response_detail"] = "人工响应内容"
    res = client.put(f"/api/v1/project/{pid}/deviation", json=items)
    assert res.status_code == 200

    # 回读验证持久化
    res = client.get(f"/api/v1/project/{pid}/deviation")
    assert res.json()["items"][0]["response_detail"] == "人工响应内容"

    client.delete(f"/api/v1/project/{pid}")
