"""阶段 B3："选资料"与"写正文"分离（select_evidence → build_prompts）"""
import pytest

from app.models.schemas import GlobalFacts, OutlineNode, PersonnelAsset, Project, TenderAnalysis18
from app.services.assets.asset_manager import asset_manager
from app.services.generator import evidence_set
from app.services.generator.evidence_set import EvidenceSet, select_evidence
from app.services.generator.section_generator import section_generator

SCORING = [
    {"id": "s_team", "name": "人员资质", "points": 4, "response_type": "evidence",
     "criteria": "项目经理具备信息系统项目管理师（高级）证书，得4分。", "note": "提供证书复印件及社保证明"},
]


@pytest.fixture()
def no_retrieval(monkeypatch):
    """检索打桩：返回空结果并计数；生成环节一旦检索就会被发现"""
    calls = []

    def fake_retrieve(**kw):
        calls.append(kw)
        return {"refs": [], "message": "无高置信参考"}

    monkeypatch.setattr(evidence_set.retrieval_service, "retrieve", fake_retrieve)
    return calls


def _project(node, links=None, scoring=None):
    ta = TenderAnalysis18(scoring_items=scoring or []) if scoring else None
    return Project(id="p", name="某运维项目", client_name="某局", description="", outline=[node],
                   tender_analysis=ta, evidence_links=links or {}, created_at="", updated_at="")


def _add_people():
    asset_manager.add_personnel(PersonnelAsset(id="p_pm", name="王工", role="项目经理", certificates=["信息系统项目管理师"]))
    asset_manager.add_personnel(PersonnelAsset(id="p_dev", name="李工", role="开发工程师", status="unverified"))


def test_generation_never_retrieves(monkeypatch):
    def boom(**_kw):
        raise AssertionError("生成环节不应检索")

    monkeypatch.setattr(evidence_set.retrieval_service, "retrieve", boom)
    evidence = EvidenceSet(refs=[], asset_context="【拟任团队人员】……", retrieval_message="无高置信参考")
    kw = dict(section_title="3.1 项目团队", section_path="第三章 > 3.1 项目团队", requirements=["团队配置"],
              facts=GlobalFacts(), outline=[], section_id="sec_3_1")
    built = section_generator.build_prompts(evidence=evidence, **kw)
    assert "【拟任团队人员】" in built["user"]
    result = section_generator.draft_section(evidence=evidence, **kw)
    assert result["generated_content"] and result["retrieval_message"] == "无高置信参考"


def test_examples_excluded_and_unverified_marked(no_retrieval):
    node = OutlineNode(id="sec_3", title="第三章 项目团队人员配置")
    evidence = select_evidence(_project(node), node)
    assert len(no_retrieval) == 1
    assert evidence.assets == [] and "尚未录入" in evidence.asset_context  # 只有示例资料：不注入

    _add_people()
    evidence = select_evidence(_project(node), node)
    assert [(a["asset_id"], a["status"], a["source"]) for a in evidence.assets] == [
        ("p_pm", "confirmed", "matched"), ("p_dev", "unverified", "matched")]
    line = next(x for x in evidence.asset_context.splitlines() if "李工" in x)
    assert "【待核实：…】" in line
    records = evidence.ref_records()
    assert [r["ref_type"] for r in records] == ["asset", "asset"] and records[1]["name"] == "李工"
    prompt = section_generator.build_prompts(section_title=node.title, section_path=node.title, requirements=[],
                                             evidence=evidence)["user"]
    assert "王工" in prompt and "张文远" not in prompt  # 预设示例人员不出现


def test_only_relevant_assets_are_selected(no_retrieval):
    _add_people()
    node = OutlineNode(id="sec_3_1", title="3.1 项目经理", requirements=["项目经理须具备信息系统项目管理师证书"])
    evidence = select_evidence(_project(node), node)
    assert [a["asset_id"] for a in evidence.assets] == ["p_pm"]  # 只取相关条目，不整库注入


def test_linked_assets_take_priority_and_tender_text_included(no_retrieval):
    _add_people()
    node = OutlineNode(id="sec_9", title="9.1 人员资质", scoring_item_ids=["s_team"])
    project = _project(node, links={"s_team": ["personnel:p_dev"]}, scoring=SCORING)
    evidence = select_evidence(project, node)
    assert [(a["asset_id"], a["source"]) for a in evidence.assets] == [("p_dev", "linked")]
    assert "评分项关联" in evidence.asset_context and "王工" not in evidence.asset_context
    assert evidence.tender_snippets[0]["item_id"] == "s_team"
    prompt = section_generator.build_prompts(section_title=node.title, section_path=node.title, requirements=[],
                                             evidence=evidence)["user"]
    assert "招标原文" in prompt and "信息系统项目管理师（高级）证书" in prompt and "社保证明" in prompt


def test_revision_prompt_uses_base_text_and_issues():
    built = section_generator.build_prompts(
        section_title="1.1 运维流程", section_path="1.1 运维流程", requirements=[], evidence=EvidenceSet(),
        base_text="原稿正文", issues=["缺少应急预案", "响应时限与全局事实不一致"],
    )
    assert "原稿正文" in built["user"] and "1. 缺少应急预案" in built["user"] and "修订任务" in built["user"]
    assert "编写任务" not in built["user"]


def test_stream_selects_evidence_once_and_saves_asset_refs(client, project_id, monkeypatch):
    outline = [{"id": "sec_1", "title": "第一章 项目团队", "level": 1}]
    client.put(f"/api/v1/project/{project_id}/outline", json={"outline": outline})
    calls = []

    def fake_select(project, node, **kw):
        calls.append(node.id)
        return EvidenceSet(assets=[{"kind": "personnel", "asset_id": "p_x", "name": "王工", "status": "unverified",
                                    "source": "matched"}], asset_context="王工")

    def boom(**_kw):
        raise AssertionError("生成环节不应检索")

    from app.api import sections
    monkeypatch.setattr(sections, "select_evidence", fake_select)
    monkeypatch.setattr(evidence_set.retrieval_service, "retrieve", boom)
    body = client.post(f"/api/v1/project/{project_id}/section/generate/stream",
                       json={"project_id": project_id, "section_id": "sec_1", "section_title": "第一章 项目团队"}).text
    assert '"done": true' in body and calls == ["sec_1"]
    node = client.get(f"/api/v1/project/{project_id}").json()["outline"][0]
    assert node["last_refs"] == [{"kind": "personnel", "asset_id": "p_x", "name": "王工", "status": "unverified",
                                  "source": "matched", "ref_type": "asset"}]


def test_expired_or_foreign_assets_are_excluded_from_prompts(no_retrieval):
    """证书过期（含投标截止日前到期）、所属主体与投标人不一致的资料不进入提示词，记为已排除"""
    from app.models.schemas import CompanyQualification
    asset_manager.add_qualification(CompanyQualification(
        id="q_old", name="ISO9001 质量管理体系认证", cert_no="OLD-1", issue_org="某认证中心", expiry_date="2020-01-01"))
    asset_manager.add_qualification(CompanyQualification(
        id="q_soon", name="ISO20000 信息技术服务管理体系认证", cert_no="SOON-1", issue_org="某认证中心",
        expiry_date="2026-10-15"))
    asset_manager.add_qualification(CompanyQualification(
        id="q_ok", name="ISO27001 信息安全管理体系认证", cert_no="OK-1", issue_org="某认证中心",
        status="unverified"))  # 待核实、无附件：仍可使用（以【待核实】标注）
    asset_manager.add_personnel(PersonnelAsset(id="p_other", name="赵工", role="项目经理", holder="别家公司"))

    node = OutlineNode(id="sec_q", title="5.1 企业资质证书")
    project = _project(node).model_copy(update={
        "facts": GlobalFacts(company_name="重庆某某科技有限公司"),
        "tender_analysis": TenderAnalysis18(submission_deadline="2026年10月20日09:30"),
    })
    evidence = select_evidence(project, node)
    assert [a["asset_id"] for a in evidence.assets] == ["q_ok"]
    assert {a["asset_id"] for a in evidence.excluded_assets} == {"q_old", "q_soon"}
    assert "OLD-1" not in evidence.asset_context and "SOON-1" not in evidence.asset_context
    reasons = {a["asset_id"]: a["reason"] for a in evidence.excluded_assets}
    assert "早于投标截止日 2026-10-20" in reasons["q_soon"]
    assert [r["ref_type"] for r in evidence.ref_records()] == ["asset", "asset_excluded", "asset_excluded"]

    # 相关条目全被排除时不改用其他人员，只说明已排除
    pm = OutlineNode(id="sec_pm", title="3.1 项目经理")
    evidence = select_evidence(project.model_copy(update={"outline": [pm]}), pm)
    assert evidence.assets == [] and evidence.excluded_assets[0]["asset_id"] == "p_other"
    assert "已排除" in evidence.asset_context and "赵工" not in evidence.asset_context
    assert "主体" in evidence.excluded_assets[0]["reason"]

    # 用户关联的资料同样排除，且不改用未关联的资料
    linked_node = OutlineNode(id="sec_l", title="5.2 资质", scoring_item_ids=["s_q"])
    scoring = [{"id": "s_q", "name": "企业资质", "points": 2, "response_type": "evidence", "criteria": "ISO9001"}]
    linked = project.model_copy(update={
        "outline": [linked_node], "evidence_links": {"s_q": ["qualifications:q_old"]},
        "tender_analysis": TenderAnalysis18(submission_deadline="2026年10月20日", scoring_items=scoring),
    })
    evidence = select_evidence(linked, linked_node)
    assert evidence.assets == [] and [a["source"] for a in evidence.excluded_assets] == ["linked"]
    assert "已排除" in evidence.asset_context and "q_ok" not in str(evidence.assets)


def test_deviation_context_excludes_expired_assets():
    from app.models.schemas import CompanyQualification, DeviationItem
    from app.services.assets.material_check import blocking_filter
    from app.services.parser.deviation_engine import deviation_engine
    from datetime import date
    asset_manager.add_qualification(CompanyQualification(
        id="q_old2", name="涉密信息系统集成资质", cert_no="SM-OLD", issue_org="某局", expiry_date="2021-01-01"))
    item = DeviationItem(index=1, clause_title="投标人须具备涉密信息系统集成资质")
    _, plain = deviation_engine._item_context(item)
    assert "SM-OLD" in plain
    _, filtered = deviation_engine._item_context(item, blocking_filter(GlobalFacts(), date(2026, 10, 7)))
    assert "SM-OLD" not in filtered and "已排除" in filtered
