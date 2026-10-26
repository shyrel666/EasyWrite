"""每次资料引用独立检查，全书扫描包括父章自己的正文。"""
import pytest

from app.models.schemas import GlobalFacts, OutlineNode
from app.services.assets.asset_manager import asset_manager
from app.services.checker import section_check as sc
from app.services.checker.book_consistency import book_consistency
from app.services.project_store import project_store


@pytest.mark.parametrize("marked_first", [True, False])
def test_unverified_assets_checked_at_every_occurrence(marked_first):
    marked, plain = "【待核实：王工】负责实施。", "王工任项目经理。"
    text = "\n".join((marked, plain) if marked_first else (plain, marked))
    issues, verify = sc._check_assets(text, {"personnel": [{"id": "p", "name": "王工", "status": "unverified"}]})
    assert len(issues) == len(verify) == 1
    assert issues[0].code == "unmarked_unverified" and issues[0].line == (2 if marked_first else 1)
    assert verify[0].line == (1 if marked_first else 2)


def test_overlapping_asset_identifiers_do_not_duplicate_issues():
    assets = {"qualifications": [{"id": "q", "name": "ISO9001", "cert_no": "ISO9001", "status": "example"}]}
    issues, _ = sc._check_assets("持有ISO9001。\n证明文件为ISO9001。", assets)
    assert [i.line for i in issues] == [1, 2]


def test_marker_in_next_paragraph_does_not_confirm_previous_mention():
    assets = {"personnel": [{"id": "p", "name": "王工", "status": "unverified"}]}
    issues, verify = sc._check_assets("王工负责实施\n【待核实：王工】任项目经理", assets)
    assert [i.line for i in issues] == [1] and [v.line for v in verify] == [2]


def test_book_scans_parent_body_once_without_aggregating_child_body(project_id, monkeypatch):
    monkeypatch.setattr(asset_manager, "snapshot", lambda: {"personnel": [{"id": "p", "name": "王工", "status": "example"}]})
    project = project_store.get(project_id)
    project.facts = GlobalFacts(sla_commitment="1小时响应")
    project.outline = [OutlineNode(id="parent", title="父章", content="2小时响应。王工任经理。", children=[
        OutlineNode(id="child", title="子章", content="3小时响应。"),
    ])]
    report = book_consistency(project)
    assert report["sections_checked"] == 2
    assert [i["section_id"] for i in report["fact_conflicts"]] == ["parent", "child"]
    assert [i["section_id"] for i in report["asset_issues"]] == ["parent"]
