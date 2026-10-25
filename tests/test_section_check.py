"""阶段 C1：章节检查器（规则离线可用，模型评审可选；只检查文本，不改动正文）"""
import pytest

from app.core.llm_client import llm_client
from app.models.schemas import GlobalFacts, OutlineNode
from app.services.checker import section_check as sc
from app.services.generator.evidence_set import EvidenceSet
from app.services.project_store import find_node, project_store
from app.services.rag.retriever import retrieval_service

ANALYSIS = {"scoring_items": [
    {"id": "s1", "name": "技术服务方案", "points": 20, "response_type": "proposal",
     "criteria": "内容包括：①系统架构设计；②数据安全保障；③运维服务体系。"},
    {"id": "s2", "name": "实施方案", "points": 10, "response_type": "proposal",
     "criteria": "内容包括：①进度计划；②质量保障措施。"},
]}
OUTLINE = [
    {"id": "sec_1", "title": "第一章 技术服务方案", "level": 1, "scoring_item_ids": ["s1"], "children": [
        {"id": "sec_1_1", "title": "1.1 系统架构设计", "level": 2, "scoring_item_ids": ["s1"],
         "requirements": ["评分要点：系统架构设计", "所属评分项：技术服务方案（20分）"]},
        {"id": "sec_1_2", "title": "1.2 安全与运维", "level": 2, "scoring_item_ids": ["s1"],
         "requirements": ["评分要点：数据安全保障", "评分要点：运维服务体系"], "word_budget": 100},
    ]},
    {"id": "sec_2", "title": "第二章 实施方案", "level": 1, "scoring_item_ids": ["s2"], "word_budget": 1000},
]


@pytest.fixture()
def project(client, project_id):
    client.post(f"/api/v1/project/{project_id}/tender/apply", json={"analysis": ANALYSIS})
    client.put(f"/api/v1/project/{project_id}/outline", json={"outline": OUTLINE})
    return project_id


def _check(project_id, section_id, text, facts=None, **kw):
    p = project_store.get(project_id)
    if facts is not None:
        p.facts = facts
    return sc.check_section(p, find_node(p.outline, section_id), text, **kw)


def _codes(report, level=None):
    return [i.code for i in report.issues if level is None or i.level == level]


def test_points_from_own_requirements_and_whole_item(project):
    r = _check(project, "sec_1_2", "本节阐述数据安全保障措施，采用分级加密。")
    assert [(p.point, p.mentioned) for p in r.points] == [("数据安全保障", True), ("运维服务体系", False)]
    missing = [i for i in r.issues if i.code == "missing_point"]
    assert len(missing) == 1 and "运维服务体系" in missing[0].message and missing[0].level == "blocking"
    assert not r.passed and r.blocking_count == 1

    # 单独承接评分项的章节：检查评分标准列举的全部要点；标题中的要点视为写到
    r = _check(project, "sec_2", "制定详细的进度计划，明确各阶段里程碑。")
    assert {p.point: p.mentioned for p in r.points} == {"进度计划": True, "质量保障措施": False}


def test_chapter_point_counted_in_children_and_split_items_skipped(project, client):
    # 一级章节承接 s1、子节分担要点：章节综述与子节正文一起判断（虚拟大纲中只替换本节正文）
    client.put(f"/api/v1/project/{project}/section",
               json={"section_id": "sec_1_2", "content": "数据安全保障与运维服务体系说明", "status": "completed"})
    r = _check(project, "sec_1", "本章围绕系统架构设计展开。")
    assert all(p.mentioned for p in r.points)
    # 兄弟章节分担同一评分项、又未写明各自要点时无法归属：不检查要点
    outline = [{"id": "a", "title": "一、甲", "level": 1, "scoring_item_ids": ["s2"]},
               {"id": "b", "title": "二、乙", "level": 1, "scoring_item_ids": ["s2"]}]

    def mutate(p):
        p.outline = [OutlineNode(**n) for n in outline]
    project_store.update(project, mutate)
    assert _check(project, "a", "无关正文").points == []


SLA = GlobalFacts(sla_commitment="接到报修后30分钟内响应，2小时内到达现场；按招标要求，质保期为1年",
                  delivery_guarantee="按招标要求，合同签订后90日历天内完成",
                  custom_facts={"巡检频次": "每月1次", "服务时间": "5×8小时"})


def test_commitment_conflicts_with_facts_are_blocking(project):
    text = ("我方承诺接到报修后2小时内响应，4小时内到达现场。\n"
            "项目工期为120日历天。质保期3年。每季度巡检1次。提供7×24小时服务。")
    r = _check(project, "sec_2", text, facts=SLA)
    conflicts = [i.message for i in r.issues if i.code == "fact_conflict"]
    # 7×24 相对事实的 5×8 窗口更宽：优于事实，不算冲突，列入待核实
    assert len(conflicts) == 5
    assert any("响应时限" in m and "2小时" in m for m in conflicts)
    assert any("到场时限" in m and "4小时" in m for m in conflicts)
    assert any("工期" in m and "120日历天" in m for m in conflicts)
    assert any("质保期" in m and "3年" in m for m in conflicts)
    assert any("巡检频次" in m for m in conflicts)
    conflict = next(i for i in r.issues if i.code == "fact_conflict")
    assert conflict.line == 1 and "2小时内响应" in conflict.excerpt


def test_matching_commitments_pass_and_unknown_ones_go_to_verify_list(project):
    text = ("接到报修后半小时内响应，两小时内到达现场，工期90天，质保期12个月，每月对系统进行一次全面巡检，"
            "提供5×8小时服务。驻场工程师3名。2026年10月1日起实施，第2年开展回访。")
    r = _check(project, "sec_2", text, facts=SLA)
    assert "fact_conflict" not in _codes(r)
    verify = [v for v in r.pending_verification if v.kind == "commitment"]
    assert len(verify) == 1 and "驻场人数" in verify[0].note and "3名" in verify[0].note

    # 全局事实为空：具体承诺数值全部列入待核实清单，不计为阻塞
    r = _check(project, "sec_2", "4小时内到达现场，工期100日历天。", facts=GlobalFacts())
    assert "fact_conflict" not in _codes(r)
    assert [v.kind for v in r.pending_verification] == ["commitment", "commitment"]


def test_fact_conflicts_tolerate_tiers_and_milestones(project):
    """评估 2026-10-25-2 修复：分阶段里程碑与分级巡检优于事实承诺时只列待核实；劣于事实仍阻塞"""
    facts = GlobalFacts(
        sla_commitment="接到报修后30分钟内响应，4小时内到达现场；重大故障2小时内恢复",
        delivery_guarantee="中标后30日历天内完成进场接管",
        custom_facts={"巡检频次": "每月1次现场全面巡检，每周1次远程健康检查"})
    text = ("故障响应4小时内到达现场处置。实施分阶段推进：进场准备7日历天，系统部署15日历天完成。"
            "实行分级巡检，开展日巡检，每日1次。")
    r = _check(project, "sec_2", text, facts=facts)
    assert "fact_conflict" not in _codes(r)
    notes = " ".join(v.note for v in r.pending_verification if v.kind == "commitment")
    assert "7日历天" in notes and "不低于全局事实承诺" in notes

    # 劣于事实：总工期 120 日历天、季度巡检仍为阻塞
    r = _check(project, "sec_2", "全部实施工作120日历天内完成，每季度巡检1次。", facts=facts)
    conflicts = [i.message for i in r.issues if i.code == "fact_conflict"]
    assert len(conflicts) == 2 and any("120日历天" in m for m in conflicts) and any("巡检" in m for m in conflicts)


def test_onsite_keyword_beats_response_in_same_clause():
    """同一分句中"响应…到达现场"：数值判为到场时限（具体词优先），与事实的到场时限比对而非响应"""
    from app.services.checker.section_check import extract_quantities
    qs = extract_quantities("故障响应4小时内到达现场处置")
    assert [(q["topic"], q["label"]) for q in qs] == [("onsite", "4小时")]


def test_numbers_in_placeholders_and_diagrams_are_ignored(project):
    text = "响应时限为【待填写：2小时】。\n```mermaid\ngraph TD\nA[4小时到场] --> B\n```"
    r = _check(project, "sec_2", text, facts=SLA)
    assert "fact_conflict" not in _codes(r)
    assert [v.kind for v in r.pending_verification] == ["placeholder"]


def test_example_and_unmarked_unverified_assets_are_blocking(project, client):
    client.post("/api/v1/assets/personnel", json={"id": "p_unv", "name": "李建国", "role": "项目经理",
                                                  "status": "unverified"})
    client.post("/api/v1/assets/cases", json={"id": "c_ok", "project_name": "某区政务云运维项目", "client_name": "某区",
                                              "contract_amount": "100万元", "status": "confirmed"})
    # 预设示例：人员"张文远"、资质证书编号、业绩名称
    text = ("项目经理由张文远担任，技术负责人李建国。我司持有证书CMMI-V2.0-5-2023-0988。"
            "曾承担某区政务云运维项目。")
    r = _check(project, "sec_2", text)
    blocking = [i for i in r.issues if i.code in ("example_asset", "unmarked_unverified")]
    assert sorted(i.code for i in blocking) == ["example_asset", "example_asset", "unmarked_unverified"]
    assert any("张文远" in i.message for i in blocking) and any("李建国" in i.excerpt for i in blocking)

    marked = "技术负责人【待核实：李建国，PMP证书】负责实施。"
    r = _check(project, "sec_2", marked)
    assert "unmarked_unverified" not in _codes(r)
    assert {v.kind for v in r.pending_verification} == {"unverified_asset", "placeholder"}

    # 示例清空后同名文本不再报示例资料
    client.delete("/api/v1/assets/examples")
    assert "example_asset" not in _codes(_check(project, "sec_2", "项目经理由张文远担任。"))


def test_employment_claims_blocked_without_personnel_data(project):
    """评估报告 2026-10-09 优先问题：企业没有人员资料时，在职/劳动关系/社保现状断言为无依据（阻塞）"""
    no_staff = EvidenceSet(library_snapshot={"personnel": [], "qualifications": [], "cases": []})
    text = "本项目成员全部为投标人直接聘用并依法缴纳社会保险的在职员工。"
    r = _check(project, "sec_2", text, evidence=no_staff)
    claims = [i for i in r.issues if i.code == "employment_claim"]
    # 同一分句命中"直接聘用""依法缴纳社会保险""在职员工"三处，只报一次
    assert len(claims) == 1 and claims[0].level == "blocking" and not r.passed
    assert "在职" in claims[0].message and "拟派" in claims[0].message

    # 已用【待核实】标注的不算（带标签前缀的待核实占位同样有效）
    marked = "成员名单及用工情况为【在职员工与社保缴纳情况：待核实】，投标前补齐证明。"
    assert "employment_claim" not in _codes(_check(project, "sec_2", marked, evidence=no_staff))

    # 计划性表述（中标后怎么做）不是企业现状断言
    plan = "中标后与全体项目成员签订劳动合同并依法缴纳社会保险。"
    assert "employment_claim" not in _codes(_check(project, "sec_2", plan, evidence=no_staff))

    # 录入了人员资料：断言与资料是否相符交给模型评审，规则层不判
    with_staff = EvidenceSet(library_snapshot={"personnel": [
        {"id": "p1", "name": "王工", "role": "项目经理", "status": "confirmed"}]})
    assert "employment_claim" not in _codes(_check(project, "sec_2", text, evidence=with_staff))


def test_labelled_placeholders_are_listed_and_numbers_inside_ignored(project):
    """【投标人全称：待填写】这类带标签前缀的占位也要列出，其中的数值不算承诺"""
    r = _check(project, "sec_2", "我方（【投标人全称：待填写】）承诺质保期【质保期：待填写1年】。")
    texts = [v.text for v in r.pending_verification if v.kind == "placeholder"]
    assert "【投标人全称：待填写】" in texts and "【质保期：待填写1年】" in texts
    assert "commitment" not in [v.kind for v in r.pending_verification]


def test_quality_checks(project):
    text = "## 1.2 安全与运维\n众所周知，数据安全保障很重要。\n# 小标题\n运维服务体系基本上满足要求。"
    r = _check(project, "sec_1_2", text)
    quality = _codes(r, "quality")
    assert set(quality) == {"length", "title_repeat", "markdown_heading", "cliche", "weak_phrase"}
    assert quality.count("markdown_heading") == 2
    assert r.passed and r.blocking_count == 0 and r.quality_count == len(quality)
    length = next(i for i in r.issues if i.code == "length")
    assert "预算 100 字" in length.message


def test_empty_text_and_offline_llm_review(project):
    r = _check(project, "sec_2", "  ")
    assert _codes(r) == ["empty"] and not r.passed
    r = _check(project, "sec_2", "进度计划与质量保障措施。", llm_review=True)
    assert r.llm_review == "skipped" and "未配置模型" in r.llm_note and r.mode == "rules"


def test_llm_review_reports_unsupported_claims(project, monkeypatch):
    calls = []

    def fake_structured(system, user, **kw):
        calls.append(kw.get("purpose"))
        return {"claims": [{"text": "我司拥有300名研发人员", "reason": "全局事实与企业资料中均无"}]}

    monkeypatch.setattr(llm_client, "is_configured", True)
    monkeypatch.setattr(llm_client, "chat_completion_structured", fake_structured)
    r = _check(project, "sec_2", "进度计划与质量保障措施。\n我司拥有300名研发人员。", llm_review=True)
    llm = [i for i in r.issues if i.source == "llm"]
    assert calls == ["section_review"] and r.llm_review == "done" and r.mode == "llm"
    assert len(llm) == 1 and llm[0].level == "quality" and llm[0].line == 2
    assert r.passed  # 模型评审只作参考，不阻塞


def test_check_endpoint_does_not_modify_content_or_retrieve(client, project, monkeypatch):
    def no_retrieval(*a, **kw):
        raise AssertionError("章节检查不应检索知识库")

    monkeypatch.setattr(retrieval_service, "retrieve", no_retrieval)
    client.put(f"/api/v1/project/{project}/section",
               json={"section_id": "sec_2", "content": "已保存的进度计划。", "status": "completed"})
    res = client.post(f"/api/v1/project/{project}/section/sec_2/check",
                      json={"content": "未保存的进度计划与质量保障措施。"})
    assert res.status_code == 200
    data = res.json()
    assert data["passed"] and data["llm_note"] == "未做模型评审（仅规则检查）"
    assert find_node(project_store.get(project).outline, "sec_2").content == "已保存的进度计划。"

    # 不传正文时检查已保存的正文
    data = client.post(f"/api/v1/project/{project}/section/sec_2/check").json()
    assert [p["point"] for p in data["points"] if not p["mentioned"]] == ["质量保障措施"]
    assert client.post(f"/api/v1/project/{project}/section/nope/check").status_code == 404


def test_extract_quantities_units_and_numerals():
    qs = sc.extract_quantities("接到通知后三十分钟响应，一百二十日历天内完工，2025年3月1日签订，第3年回访")
    assert [(q["topic"], q["value"]) for q in qs] == [("response", 30), ("duration", 120)]
    qs = sc.extract_quantities("每周巡检2次，故障1个工作日内解决")
    assert [(q["topic"], q["value"]) for q in qs] == [("inspection", "周:2"), ("resolve", "workday:1")]
