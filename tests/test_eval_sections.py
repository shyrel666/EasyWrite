"""
阶段 E 评估脚本 scripts/eval_sections.py：
- 评估大纲一个评分项一节（要点不拆成小节），按规则选出高分值方案、运维服务、团队与资质三类章节
- 两种写法用同一套规则检查测量，请求次数与用量按运行汇总；都不写入正文（模型桩）
- 报告：人工标注列留空；summarize 读回标注、按改稿算修改量、判断决策门槛，保留备注
- 真实招标文件（存在时）：默认样本三类章节都能选到，方案类章节有可检查的评分要点
"""
import importlib.util
from datetime import datetime
from pathlib import Path

import pytest

from app.models.schemas import ProjectCreate, ScoringItem, ScoringSubItem, TenderAnalysis18
from app.services.checker.section_check import section_points
from app.services.project_store import find_node, project_store
from refine_stub import FULL, HALF, LACKING, OUTLINE, FakeModel, no_kb

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("eval_sections", ROOT / "scripts" / "eval_sections.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(ev, "POLL_SECONDS", 0.02)


def _analysis() -> TenderAnalysis18:
    return TenderAnalysis18(scoring_items=[
        ScoringItem(id="s1", name="项目理解", points=20, criteria="内容包括：①需求理解；②现状分析。"),
        ScoringItem(id="s2", name="技术服务方案", points=20,
                    criteria="内容包括：1.架构设计方案；2.数据治理方案；3.安全防护方案。"),
        ScoringItem(id="s3", name="服务方案", points=10,
                    sub_items=[ScoringSubItem(name="故障处理流程", points=5), ScoringSubItem(name="巡检安排", points=5)]),
        ScoringItem(id="s4", name="技术参数响应", points=10, response_type="compliance"),
        ScoringItem(id="s5", name="拟派项目团队人员", points=8, response_type="evidence"),
        ScoringItem(id="s6", name="类似业绩", points=6, response_type="evidence"),
    ])


@pytest.fixture()
def eval_project():
    project = project_store.create(ProjectCreate(name="评估大纲测试"))
    analysis = _analysis()
    outline = ev.item_outline(analysis)

    def mutate(p):
        p.tender_analysis, p.outline = analysis, outline
        return p

    project = project_store.update(project.id, mutate)
    yield project
    project_store.delete(project.id)


def _leaf(project, title_part):
    return next(n for n in ev._flatten(project.outline) if not n.children and title_part in n.title)


def test_item_outline_keeps_every_point_of_an_item_in_one_section(eval_project):
    tech = _leaf(eval_project, "技术服务方案")
    service = _leaf(eval_project, "3.1 服务方案")
    assert [p for _, p in section_points(eval_project, tech)] == ["架构设计方案", "数据治理方案", "安全防护方案"]
    assert [p for _, p in section_points(eval_project, service)] == ["故障处理流程", "巡检安排"]
    assert tech.scoring_item_ids == ["s2"] and tech.word_budget and tech.path.endswith(tech.title)
    assert _leaf(eval_project, "技术参数响应").content_mode == "point_to_point"
    assert _leaf(eval_project, "拟派项目团队人员").content_mode == "template_fill"


def test_picks_one_section_per_category(eval_project):
    picked = {c["category"]: c["node"].title for c in ev.pick_sections(eval_project)}
    # 同为 20 分时"项目理解"靠后；运维服务按评分要点（故障 / 巡检）识别；逐条应答表不参与
    assert picked == {"proposal": "2.1 技术服务方案", "service": "3.1 服务方案", "team": "5.1 拟派项目团队人员"}
    custom = ev.pick_sections(eval_project, ["sec_1_1"])
    assert [(c["category"], c["node"].id) for c in custom] == [("custom", "sec_1_1")]
    with pytest.raises(SystemExit):
        ev.pick_sections(eval_project, ["sec_4_1"])  # 逐条应答表


def test_both_methods_measured_with_same_rules_and_nothing_written(client, project_id, monkeypatch):
    client.put(f"/api/v1/project/{project_id}/outline", json={"outline": OUTLINE})
    no_kb(monkeypatch)
    model = FakeModel(monkeypatch, drafts=[LACKING], revisions=[HALF, FULL])
    quiet = lambda *_: None  # noqa: E731

    single = ev.run_single(project_id, "sec_1_1", log=quiet)
    assert (single["status"], single["content"], single["blocking"]) == ("completed", LACKING, 2)
    assert (single["points_hit"], single["points_total"]) == (0, 2)
    assert single["usage"]["calls"] == 1 and single["usage"]["total_tokens"] == 150
    assert any("尚未" in g or "知识库" in g for g in single["evidence_gaps"])

    refine = ev.run_refine(project_id, "sec_1_1", 2, log=quiet)
    assert (refine["content"], refine["blocking"], refine["points_hit"]) == (FULL, 0, 2)
    assert (refine["outcome"], refine["rounds"], refine["history"]) == ("goal_met", 2, [2, 1, 0])
    assert refine["outcome_label"] == "已达成检查目标" and refine["usage"]["calls"] == 3
    assert model.kinds() == ["draft", "draft", "revise", "revise"]
    assert find_node(project_store.get(project_id).outline, "sec_1_1").content == ""


def _metrics(blocking, calls, content, **extra):
    return {"status": "completed", "content": content, "blocking": blocking, "quality": 1,
            "points_hit": 2 - min(blocking, 2), "points_total": 2, "pending": 1, "chars": len(content), "seconds": 3.0,
            "blocking_issues": [f"本节承接的评分要点未写到：要点{i}" for i in range(blocking)],
            "usage": {"calls": calls, "calls_without_usage": 0, "total_tokens": 100 * calls},
            "evidence_gaps": ["知识库中没有本节的高置信参考"], **extra}


def _refined(blocking, calls, content, outcome="goal_met"):
    return _metrics(blocking, calls, content, outcome=outcome, outcome_label="已达成检查目标", stop_reason=outcome,
                    stop_label="目标达成", rounds=1, max_rounds=2, history=[2, blocking])


def _results():
    sections = []
    for i, (sb, rb) in enumerate([(2, 0), (2, 1)], 1):
        sections.append({"key": f"T1-S{i}", "tender": "T1", "title": f"{i}.1 运维服务方案", "category": "service",
                         "share": 10.0, "word_budget": 1000, "points_total": 2,
                         "single": _metrics(sb, 1, f"第{i}节单次生成的正文。\n故障处理流程。"),
                         "refine": _refined(rb, 3, f"第{i}节智能完善的正文。\n故障处理流程与巡检安排。")})
    return {"date": "2026-10-08", "started_at": "2026-10-08 10:00", "finished_at": "2026-10-08 10:30",
            "config": {"model": "fake / fake-model", "max_rounds": 2, "budget_calls": 15, "budget_seconds": 600,
                       "facts": "未填写"},
            "basis": {"kb_documents": 0, "kb_chunks": 0, "assets_confirmed": 0, "assets_unverified": 0, "assets_example": 16},
            "tenders": [{"key": "T1", "name": "测试招标项目", "file": "t.docx"}], "sections": sections}


def _fill(text, key, method, values):
    """在明细表中填写人工标注列"""
    lines = text.splitlines()
    header = next(i for i, line in enumerate(lines) if line.startswith("| 编号 | 写法 |"))
    cols = [c.strip() for c in lines[header].strip().strip("|").split("|")]
    for i, line in enumerate(lines):
        if line.startswith(f"| {key} | {ev.METHODS[method]} |"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            for col, value in values.items():
                cells[cols.index(col)] = value
            lines[i] = "| " + " | ".join(cells) + " |"
    return "\n".join(lines)


def test_report_roundtrip_annotations_edit_ratio_and_gates(tmp_path):
    workspace, report = tmp_path / "ws", tmp_path / "docs" / "2026-10-08.md"
    results = _results()
    for s in results["sections"]:
        for method in ev.METHODS:
            ev.write_texts(workspace, s["key"], method, s[method]["content"])
    ev.save(results, workspace, report)
    text = report.read_text(encoding="utf-8")
    assert "<!-- eval-workspace: " in text and "| 阻塞问题减少一半以上 | 4 | 1 | 是 |" in text
    assert "3.0 倍" in text and "人工标注完成后再判断" in text
    assert "| T1-S1 | 单次生成 | 2 |" in text and "[原稿](" in text
    assert "本节承接的评分要点未写到：要点0" in text and "知识库中没有本节的高置信参考" in text

    # 人工标注：无依据声明单次 2+1、完善 0+1；完善稿第二节的 1 条阻塞问题是误报；第一节两份改稿完成（删除标记）
    cols = ev.HUMAN_COLUMNS
    for key, method, values in (("T1-S1", "single", ("2", "1", "0")), ("T1-S1", "refine", ("0", "0", "0")),
                                ("T1-S2", "single", ("1", "1", "0")), ("T1-S2", "refine", ("1", "0", "1（误判）"))):
        text = _fill(text, key, method, dict(zip(cols, values)))
    text = text.replace(ev.DEFAULT_NOTES, "第二节的正文过短。")
    report.write_text(text, encoding="utf-8")
    texts = workspace / "texts"
    (texts / "T1-S1.single.edited.md").write_text("第1节单次生成的正文（已改）。\r\n故障处理流程。", encoding="utf-8")
    (texts / "T1-S1.refine.edited.md").write_text("第1节智能完善的正文。\n故障处理流程与巡检安排。", encoding="utf-8")

    ev.main(["summarize", str(report)])
    text = report.read_text(encoding="utf-8")
    assert "| 无依据企业事实声明不增加（人工） | 3 | 1 | 是 |" in text
    assert "**达到门槛" in text and "第二节的正文过短。" in text and ev.DEFAULT_NOTES not in text
    assert "| 阻塞问题误报率（人工） | 0% | 100% |" in text
    row = next(line for line in text.splitlines() if line.startswith("| T1-S1 | 单次生成 |"))
    assert "| 2 | 1 | 0 |" in row and "%" in row  # 标注保留，修改量已计算
    assert next(line for line in text.splitlines() if line.startswith("| T1-S1 | 智能完善 |")).count("0.0%") == 1
    assert "（1 节）" in next(line for line in text.splitlines() if line.startswith("| 平均修改量"))
    assert "1（误判）" in text

    # 无依据声明增加：未达到门槛
    report.write_text(_fill(text, "T1-S2", "refine", {cols[0]: "5"}), encoding="utf-8")
    ev.main(["summarize", str(report)])
    assert "**未达到门槛" in report.read_text(encoding="utf-8")


def test_failed_rows_are_reported_but_not_compared(tmp_path):
    results = _results()
    results["sections"][1]["refine"] = {"status": "failed", "error": "模型调用失败，未能起草", "usage": {"calls": 1},
                                        "seconds": 1.0, "content": ""}
    ev.save(results, tmp_path / "ws", tmp_path / "r.md")
    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "## 汇总（1/2 节两种写法都产出了正文" in text
    assert "| 阻塞问题减少一半以上 | 2 | 0 | 是 |" in text
    assert "失败：模型调用失败，未能起草" in text


def test_helpers():
    assert ev.edit_ratio("abcd", "abcd") == 0 and ev.edit_ratio("abcd", "abXd") == 0.25
    assert ev.edit_ratio("一行\n二行", "一行\r\n二行  ") == 0
    assert ev.date_label(datetime(2026, 10, 8, 9, 5)) == "2026-10-08"
    assert ev.max_requests(2, 0) == (1, 3) and ev.max_requests(2, 10) == (2, 4)
    assert ev.parse_int(" 3（见备注）") == 3 and ev.parse_int("") is None


@pytest.mark.skipif(not ev.TENDER_DIR.is_dir(), reason="真实招标文件不在仓库中（downloads/ 被忽略）")
def test_default_tenders_have_three_categories_with_checkable_points():
    for path in ev.resolve_tenders(ev.DEFAULT_TENDERS):
        project = ev.setup_project(path)
        try:
            picked = ev.pick_sections(project)
            assert [c["category"] for c in picked] == ["proposal", "service", "team"], path.name
            for c in picked[:2]:
                assert len(section_points(project, c["node"])) >= 2, (path.name, c["node"].title)
        finally:
            project_store.delete(project.id)
