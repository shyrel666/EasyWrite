"""
偏离表按章节树抽取 + 批量响应：
- 只抽技术/服务需求篇，跳过商务篇、项目概况、一览表、合计行、结构性小标签
- 条款等级以文件标记定义为准，段落继承所在小节标题标记，带标记短标签把等级带给后续段落
- 批量响应：每次调用多条，漏答条目逐条重试，模型拟稿标记 ai；离线不编造
"""

from app.core.llm_client import llm_client
from app.models.schemas import DeviationItem, GlobalFacts
from app.services.parser import deviation_engine as de_module
from app.services.parser.deviation_engine import deviation_engine

LEGEND = (
    "“※”标注的服务需求为符合性审查中的实质性要求，若不满足按无效投标处理。\n"
    "“★”标注的技术需求为重要技术需求，若不满足将按照评标因素中相关规定处理。"
)


def _sec(title, content="", tables=None, subs=None, level=1):
    return {"title": title, "level": level, "content": "\n\n".join(content) if isinstance(content, list) else content,
            "tables": tables or [], "subsections": subs or []}


SECTIONS = [
    _sec("第一篇 投标邀请书", ["投标人须提供营业执照复印件。"]),
    _sec("第二篇 项目技术（质量）需求", [LEGEND.split("\n")[0]], subs=[
        _sec("一、项目基本概况介绍", ["本项目于2020年建成，系统须保持稳定。"],
             tables=[[["包号", "采购预算", "备注"], ["1", "93万元", "无"]]], level=2),
        _sec("※二、服务范围及技术要求", ["（一）服务标准及技术要求", "投标人须提供7×24小时驻场运维服务。"],
             tables=[[["序号", "需求分类", "具体需求", "金额（元）"],
                      ["1", "等保测评需求", "需完成4个信息系统等保测评", "120000"],
                      ["合计", "人民币大写：玖拾叁万元整", "", ""]]],
             level=2, subs=[
                 _sec("（1）★数据确权", ["支持以部门为单位完成数据权责认定。", "系统留存完整确权操作记录。"], level=3),
                 _sec("（2）数据质量检测", [
                     "★1.1.6数据质量提升",
                     "按照评分指标体系要求，分析形成字段级评价规则。",
                     "1.1.7应用场景支撑",
                     "分析各部门应用场景，须提供共享接口支持。",
                 ], level=3),
             ]),
    ]),
    _sec("第三篇 项目商务需求", ["※服务期：合同签订后1年。"]),
]


def test_structure_extraction_scope_and_levels():
    items, scope = deviation_engine.extract_from_structure(SECTIONS, LEGEND)
    assert scope == ["第二篇 项目技术（质量）需求"]
    by_text = {it.clause_title: it for it in items}

    assert "投标人须提供营业执照复印件。" not in by_text, "投标邀请书不在偏离表范围"
    assert "※服务期：合同签订后1年。" not in by_text, "商务篇不进入技术偏离表"
    assert "本项目于2020年建成，系统须保持稳定。" not in by_text, "项目概况小节跳过"
    assert "（一）服务标准及技术要求" not in by_text, "结构性小标签不是条款"
    assert not any(t.startswith("合计") or "人民币大写" in t for t in by_text), "合计行不是条款"

    assert by_text["投标人须提供7×24小时驻场运维服务。"].level == "redline", "继承所在小节标题的※"
    assert by_text["等保测评需求：需完成4个信息系统等保测评"].level == "redline", "需求表格逐行成条，去掉金额列"
    assert by_text["支持以部门为单位完成数据权责认定。"].level == "important"
    assert by_text["支持以部门为单位完成数据权责认定。"].section.endswith("（1）★数据确权")
    assert by_text["★1.1.6数据质量提升：按照评分指标体系要求，分析形成字段级评价规则。"].level == "important", \
        "带标记短标签把等级带给后续段落"
    assert by_text["分析各部门应用场景，须提供共享接口支持。"].level == "normal", "下一个标签（应用≠应）结束带入"
    assert [it.level for it in items] == sorted((it.level for it in items),
                                               key=lambda lv: {"redline": 0, "important": 1, "normal": 2}[lv])
    assert all(it.is_star == (it.level == "redline") for it in items)


def test_structure_without_requirement_part_returns_empty():
    items, scope = deviation_engine.extract_from_structure([_sec("第一篇 投标邀请书", ["须提供资料。"])], "")
    assert items == [] and scope == []


def test_api_extract_uses_structure(client, project_id, monkeypatch):
    from app.services.project_store import project_store
    project_store.set_tender_structure(project_id, {"sections": SECTIONS})
    project_store.set_tender_text(project_id, LEGEND)
    res = client.post(f"/api/v1/project/{project_id}/deviation/extract", json={})
    assert res.status_code == 200
    data = res.json()
    assert data["coverage"]["scope"] == ["第二篇 项目技术（质量）需求"]
    assert data["coverage"]["redline_count"] == 2
    assert data["items"][0]["section"]


def test_batch_responses_grouped_with_retry(monkeypatch):
    calls = []

    def fake_structured(system_prompt, user_prompt, temperature=None, max_tokens=None, purpose=""):
        calls.append(user_prompt)
        if "逐条响应以下" in user_prompt:  # 批量调用：故意漏答第 2 条
            n = user_prompt.count("招标要求：")
            return {"responses": [{"no": i, "response_status": "完全满足", "response_detail": f"批量响应{i}"}
                                  for i in range(1, n + 1) if i != 2]}
        return {"response_status": "正偏离", "response_detail": "单条重试响应"}

    monkeypatch.setattr(llm_client, "is_configured", True)
    monkeypatch.setattr(llm_client, "get_mode", lambda: "llm")
    monkeypatch.setattr(llm_client, "chat_completion_structured", fake_structured)
    monkeypatch.setattr(de_module, "BATCH_SIZE", 3)
    items = [DeviationItem(index=i, clause_title=f"系统须支持功能{i}", response_status="待生成") for i in range(1, 6)]
    deviation_engine.batch_generate_responses(items, GlobalFacts())

    assert len(calls) == 4, "5 条按 3 条一批 = 2 次批量调用 + 2 次漏答重试"
    assert [it.response_detail for it in items] == ["批量响应1", "单条重试响应", "批量响应3", "批量响应1", "单条重试响应"]
    assert items[1].response_status == "正偏离"
    assert all(it.response_source == "ai" for it in items)


def test_batch_responses_offline_keep_pending(monkeypatch):
    monkeypatch.setattr(llm_client, "is_configured", False)
    items = [DeviationItem(index=1, clause_title="系统须支持等保三级", response_status="待生成")]
    deviation_engine.batch_generate_responses(items, GlobalFacts())
    assert items[0].response_status == "待生成" and items[0].response_detail == "" and items[0].response_source == ""
