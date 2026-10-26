"""单章与全书的时限、人数比较使用同一口径。"""
import pytest

from app.models.schemas import GlobalFacts, OutlineNode
from app.services.checker import section_check as sc
from app.services.checker.book_consistency import book_consistency
from app.services.project_store import project_store


@pytest.mark.parametrize("fact,text,conflicts,pending", [
    ("1天内响应", "24小时内响应", 0, 0),
    ("4小时内响应", "1天内响应", 1, 0),
    ("1天内到场", "24小时内到场", 0, 0),
    ("故障1天内解决", "故障2天内解决", 1, 0),
    ("驻场3人", "驻场2人", 1, 0),
    ("驻场3人", "驻场4人", 0, 1),
    ("1工作日响应", "1天响应", 0, 1),
    ("2工作日响应", "3工作日响应", 1, 0),
    ("2工作日响应", "1工作日响应", 0, 1),
    ("质保1年", "质保12个月", 0, 0),
    ("工期1天", "24小时交付", 0, 0),
    ("4小时响应", "2小时响应", 0, 1),
])
def test_single_and_book_checks_agree(project_id, fact, text, conflicts, pending):
    project = project_store.get(project_id)
    project.facts = GlobalFacts(custom_facts={"已确认承诺": fact})
    project.outline = [OutlineNode(id="s1", title="方案", content=text)]
    issues, verify = sc._check_commitments(text, project.facts)
    book = book_consistency(project)
    assert len(issues) == len(book["fact_conflicts"]) == conflicts
    assert len(verify) == book["commitments_unbacked"] == pending
    assert book["value_conflicts"] == []


def test_value_conflicts_require_distinct_sections_and_keep_month_tolerance(project_id):
    project = project_store.get(project_id)
    project.outline = [OutlineNode(id="s1", title="方案", content="1小时响应，2小时响应。")]
    assert book_consistency(project)["value_conflicts"] == []
    project.outline.append(OutlineNode(id="s2", title="服务", content="2小时响应。"))
    assert len(book_consistency(project)["value_conflicts"]) == 1
    project.outline[0].content = "1工作日响应。"
    project.outline[1].content = "1天响应。"
    assert book_consistency(project)["value_conflicts"] == []
    project.outline[0].content = "质保1年。"
    project.outline[1].content = "质保12个月。"
    assert book_consistency(project)["value_conflicts"] == []
