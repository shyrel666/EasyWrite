"""
评分细则驱动的大纲规划与评分点覆盖核查：
- 离线确定性规划：方案类逐项成章、其余按应答方式归并、子章节即评分要点、字数按分值
- 大模型规划漏项自动补章
- 覆盖核查：未承接 / 未撰写 / 要点缺失 / 已覆盖，按分值加权
- 空标书不再给出"乙级可投"
"""
from app.core.llm_client import llm_client
from app.models.schemas import GlobalFacts, OutlineNode, ScoringItem, ScoringSubItem, TenderAnalysis18
from app.services.checker.quality_inspector import quality_inspector
from app.services.checker.scoring_coverage import check_scoring_coverage, coverage_rate, is_mentioned
from app.services.generator import rubric_planner as rp
from app.services.generator.outline_generator import outline_generator

ITEMS = [
    ScoringItem(id="s_s1", name="投标报价", points=20, kind="price", response_type="price"),
    ScoringItem(id="s_s2", name="项目理解", points=10, kind="technical", response_type="proposal",
                criteria="投标人对本项目进行理解阐述，内容包括： 1）本项目运维的认识； 2）运维现状分析。 上述内容不存在瑕疵得10分"),
    ScoringItem(id="s_s3", name="技术服务方案", points=40, kind="technical", response_type="proposal",
                sub_items=[ScoringSubItem(name="现状及需求分析", points=10), ScoringSubItem(name="运维服务方案", points=30)]),
    ScoringItem(id="s_s4", name="服务响应偏离", points=10, kind="technical", response_type="compliance"),
    ScoringItem(id="s_s5", name="人员配置", points=12, kind="business", response_type="evidence", note="提供证书复印件"),
    ScoringItem(id="s_s6", name="类似项目业绩", points=8, kind="business", response_type="evidence"),
]
ANALYSIS = TenderAnalysis18(project_name="运维项目", scoring_items=ITEMS)


def _offline(monkeypatch):
    monkeypatch.setattr(llm_client, "is_configured", False)


def test_content_points_variants():
    assert rp.content_points("内容包括： 1.审计作业场景升级优化方案； 2.业务板块升级优化方案。 上述内容不存在瑕疵") == [
        "审计作业场景升级优化方案", "业务板块升级优化方案"]
    assert rp.content_points("内容包含： ①工作进度计划； ②详细工作内容的阐述； ③预期成果保障方案。 上述内容全面") == [
        "工作进度计划", "详细工作内容的阐述", "预期成果保障方案"]
    assert rp.content_points("内容包括： 应急基本流程； 预防措施； 突发事件应急策略等。 上述方案内容全面") == [
        "应急基本流程", "预防措施", "突发事件应急策略"]
    assert rp.content_points("过程管理工具包括工单管理、任务管理、人员管理、知识库等功能。") == [
        "工单管理", "任务管理", "人员管理", "知识库"]
    assert rp.content_points("有效的投标报价中的最低价为评标基准价。") == []


def test_offline_draft_follows_rubric(monkeypatch):
    _offline(monkeypatch)
    res = outline_generator.draft_level1("运维项目", ANALYSIS)
    assert res["mode"] == "rules" and "评分细则" in res["message"]
    titles = [c["title"] for c in res["chapters"]]
    assert titles == ["第一章 项目理解", "第二章 技术服务方案", "第三章 技术需求逐条响应", "第四章 商务资信与证明材料"]
    assert [c["scoring_item_ids"] for c in res["chapters"]] == [["s_s2"], ["s_s3"], ["s_s4"], ["s_s5", "s_s6"]]
    assert "s_s1" not in str(res["chapters"]), "报价不进入技术标"


def test_offline_expand_maps_children_to_scoring_points(monkeypatch):
    _offline(monkeypatch)
    chapters = outline_generator.draft_level1("运维项目", ANALYSIS)["chapters"]
    outline = outline_generator.expand_full_tree(chapters, "运维项目", ANALYSIS, total_word_budget=10000)["outline"]
    understanding, plan, compliance, evidence = outline
    assert [c.title for c in understanding.children] == ["1.1 本项目运维的认识", "1.2 运维现状分析"]
    assert [c.title for c in plan.children] == ["2.1 现状及需求分析", "2.2 运维服务方案"]
    assert abs(plan.children[1].word_budget - 3 * plan.children[0].word_budget) <= 150, "子章节字数按评分子项分值分配"
    assert compliance.children[0].content_mode == "point_to_point"
    assert {c.content_mode for c in evidence.children} == {"template_fill"}
    # 分值相同的情况下方案篇幅远大于证书清单；总量≈预算×0.8
    assert plan.word_budget > evidence.word_budget * 5
    assert abs(sum(n.word_budget for n in outline) - 8000) <= 200


def test_llm_draft_missing_items_are_backfilled(monkeypatch):
    monkeypatch.setattr(llm_client, "is_configured", True)
    monkeypatch.setattr(llm_client, "get_mode", lambda: "llm")
    monkeypatch.setattr(llm_client, "chat_completion_structured", lambda **kw: {"chapters": [
        {"title": "第一章 总体技术方案", "requirements": ["总体"], "scoring_item_ids": ["s_s3", "bogus"]},
        {"title": "第二章 项目理解", "scoring_item_ids": ["s_s2"]},
    ]})
    res = outline_generator.draft_level1("运维项目", ANALYSIS)
    assert res["mode"] == "llm"
    assert res["chapters"][0]["scoring_item_ids"] == ["s_s3"], "未知评分项 ID 应剔除"
    covered = {x for c in res["chapters"] for x in c["scoring_item_ids"]}
    assert covered == {"s_s2", "s_s3", "s_s4", "s_s5", "s_s6"}
    assert "服务响应偏离" in res["message"] and "人员配置" in res["message"]
    assert res["chapters"][-1]["title"].startswith("第四章")


def _outline_with_content():
    return [
        OutlineNode(id="sec_1", title="第一章 项目理解", scoring_item_ids=["s_s2"], children=[
            OutlineNode(id="sec_1_1", title="1.1 运维认识", level=2, word_budget=500,
                        content="对本项目运维的认识：" + "系统现状良好" * 40),
        ]),
        OutlineNode(id="sec_2", title="第二章 技术服务方案", scoring_item_ids=["s_s3"], children=[
            OutlineNode(id="sec_2_1", title="2.1 现状及需求分析", level=2, word_budget=500, content="现状分析" * 60),
            OutlineNode(id="sec_2_2", title="2.2 运维服务方案", level=2, word_budget=1500),
        ]),
        OutlineNode(id="sec_3", title="第三章 商务资信", scoring_item_ids=["s_s5"], children=[
            OutlineNode(id="sec_3_1", title="3.1 人员配置", level=2, content="项目经理：高级工程师"),
        ]),
    ]


def test_scoring_coverage_statuses():
    results = {r.item_id: r for r in check_scoring_coverage(_outline_with_content(), ANALYSIS)}
    assert "s_s1" not in results
    assert results["s_s2"].status == "要点缺失" and results["s_s2"].missing_points == ["运维现状分析"]
    assert results["s_s3"].status == "要点缺失" and results["s_s3"].missing_points == ["运维服务方案"], \
        "空白小节的标题不算覆盖"
    assert results["s_s4"].status == "未承接"
    assert results["s_s5"].status == "已覆盖"
    assert results["s_s6"].status == "未承接"
    # 分值加权：项目理解 10×0.5 + 技术方案 40×0.5 + 人员 12×1 = 37 / 80
    assert abs(coverage_rate(list(results.values())) - 37 / 80) < 1e-6


def test_short_written_section_penalized_once():
    outline = [OutlineNode(id="sec_2", title="第二章 技术服务方案", scoring_item_ids=["s_s3"], children=[
        OutlineNode(id="sec_2_1", title="2.1 现状及需求分析", level=2, word_budget=1000, content="现状分析" * 10),
        OutlineNode(id="sec_2_2", title="2.2 运维服务方案", level=2, word_budget=1000, content="运维服务方案" * 60),
    ])]
    result = next(r for r in check_scoring_coverage(outline, ANALYSIS) if r.item_id == "s_s3")
    assert result.status == "篇幅不足" and result.coverage == 0.5


def test_is_mentioned_tolerates_rewording():
    assert is_mentioned("运维质量保证", "本章阐述运维过程的质量保证体系")
    assert not is_mentioned("数据清洗", "本章阐述运维流程")


def test_quality_report_uses_real_coverage():
    report = quality_inspector.inspect_quality(_outline_with_content(), GlobalFacts(), [], ANALYSIS)
    d7 = next(d for d in report.dimensions if d.dimension_name.startswith("7."))
    assert d7.score == round(37 / 80 * 100)
    assert report.scoring_coverage_rate is not None and len(report.scoring_coverage) == 5


def test_empty_bid_is_not_rated_submittable():
    outline = [OutlineNode(id="sec_1", title="第一章 项目理解", scoring_item_ids=["s_s2"])]
    report = quality_inspector.inspect_quality(outline, GlobalFacts(), ["★ 必须提供原厂授权"], ANALYSIS)
    assert report.rating_level == "未撰写" and report.passed is False
    text_dims = [d for d in report.dimensions if d.dimension_name.startswith(("1.", "2.", "4.", "5.", "6.", "8."))]
    assert all(d.score is None and d.status == "未检测" for d in text_dims)


def test_quality_without_rubric_marks_coverage_unchecked():
    outline = [OutlineNode(id="sec_1", title="第一章 概述", content="系统采用微服务架构，支持高可用集群部署。")]
    report = quality_inspector.inspect_quality(outline, GlobalFacts(), [], None)
    d7 = next(d for d in report.dimensions if d.dimension_name.startswith("7."))
    assert d7.score is None and d7.status == "未检测"
    assert report.overall_score == round(
        sum(d.score for d in report.dimensions if d.score is not None)
        / len([d for d in report.dimensions if d.score is not None]))


def test_coverage_uses_most_specific_sections(monkeypatch):
    _offline(monkeypatch)
    chapters = outline_generator.draft_level1("运维项目", ANALYSIS)["chapters"]
    outline = outline_generator.expand_full_tree(chapters, "运维项目", ANALYSIS, total_word_budget=10000)["outline"]
    results = {r.item_id: r for r in check_scoring_coverage(outline, ANALYSIS)}
    staff = results["s_s5"]
    assert staff.section_titles == ["4.1 人员配置"], "共享章节中的证明材料项应定位到具体小节"
    assert staff.word_budget == outline[3].children[0].word_budget
    assert results["s_s3"].section_titles == ["2.1 现状及需求分析", "2.2 运维服务方案"]
