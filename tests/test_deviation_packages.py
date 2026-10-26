"""
多分包偏离表（F1）：需求篇按分包分写时只保留投标分包与各包通用的条款
- 标题里的分包管到章节结束，写了几个分包的标题为通用
- 分包标签段落管到下一个标签或章节结束；章节最后一段的标签引导后续同级章节
- 表格按文档位置归属其前面的标签；需求表格首列写了分包的行归该包
- 对应不上投标分包、或无法确定投标分包时不过滤并提示
- 真实招标文件（downloads/ 存在时）：每个分包都能单独抽出，互不混入
"""
from functools import lru_cache
from pathlib import Path

import pytest

from app.models.schemas import ScoringItem, TenderAnalysis18
from app.services.parser.deviation_engine import deviation_engine
from app.services.parser.document_parser import parse_document
from app.services.parser.scoring_extractor import extract_scoring_items
from app.services.parser.word_parser import DocumentTreeBuilder
from app.services.project_store import project_store

REAL_DIR = Path(__file__).resolve().parent.parent / "downloads" / "ccgp_2026-10-06"


def _sec(title, content=(), tables=None, subs=None, anchors=None):
    node = {"title": title, "content": "\n\n".join(content), "tables": tables or [], "subsections": subs or []}
    if anchors is not None:
        node["table_anchors"] = anchors
    return node


def _packages(items):
    return {it.clause_title: it.package for it in items}


def test_heading_packages_and_shared_heading():
    sections = [_sec("第二篇 项目服务需求", subs=[
        _sec("（一）包1服务范围、要求及标准", ["投标人须提供网络安全监测服务。"]),
        _sec("（二）包2服务范围、要求及标准", ["投标人须提供等保测评服务。"]),
        _sec("四、商务要求（包1、包2）", ["投标人须在合同签订后15日内进场。"]),
        _sec("五、验收标准", ["投标人须按国家标准提交验收文档。"]),
    ])]
    items, scope, info = deviation_engine.extract_from_structure(sections)
    assert _packages(items) == {
        "投标人须提供网络安全监测服务。": "包1",
        "投标人须提供等保测评服务。": "包2",
        "投标人须在合同签订后15日内进场。": "",
        "投标人须按国家标准提交验收文档。": "",
    }
    assert info["found"] == ["包1", "包2"] and not info["filtered"]
    assert "无法确定投标分包" in info["note"], "多个分包却没有投标分包：不过滤，提示人工核对"


def test_paragraph_labels_lead_following_sections():
    """「第2包」是上一节的最后一段（PDF 没把它识别成标题），它引导的是其后的同级章节"""
    sections = [_sec("第五部分采购需求", ["第1包"], subs=[
        _sec("一、技术需求", ["系统须支持7×24小时监控。"]),
        _sec("五、付款方式", ["按季度支付", "第2包"]),
        _sec("一、技术需求", ["系统须支持英文版稿件发布。"]),
    ])]
    items, _, info = deviation_engine.extract_from_structure(sections, target_package="包2")
    assert _packages(items) == {"系统须支持英文版稿件发布。": "包2"}
    assert info["filtered"] and info["excluded"] == 1 and info["excluded_by_package"] == {"包1": 1}


def test_labels_tables_in_document_order_and_scope_ends():
    table_a = [["序号", "服务项目", "服务要求"], ["1", "核心交换机维保", "维保设备须提供原厂备件"]]
    table_b = [["序号", "服务内容", "服务要求"], ["1", "杀毒软件授权", "授权点数须不少于500个"]]
    sections = [_sec("第二章项目需求", subs=[
        _sec("二、采购清单", ["A包：", "B包："], tables=[table_a, table_b], anchors=[1, 2]),
        _sec("三、技术要求", ["A包:", "维保设备出现硬件损坏，须在4小时内更换备件。"], subs=[
            _sec("（4）及时修复补丁", ["B包:", "投标人须在2小时内派技术人员到场。"]),
        ]),
        _sec("五、验收标准", ["采购人须在7个工作日内组织验收。"]),
    ])]
    items, _, info = deviation_engine.extract_from_structure(sections, target_package="A包")
    assert _packages(items) == {
        "核心交换机维保：维保设备须提供原厂备件": "A包",
        "维保设备出现硬件损坏，须在4小时内更换备件。": "A包",
        "采购人须在7个工作日内组织验收。": "",
    }, "标签只管到所在章节结束：其后的验收标准各包通用"
    assert info["excluded_by_package"] == {"B包": 2}

    # 旧存档没有表格位置：表格排在段落之后，归最后一个标签
    del sections[0]["subsections"][0]["table_anchors"]
    items, _, _ = deviation_engine.extract_from_structure(sections)
    assert _packages(items)["核心交换机维保：维保设备须提供原厂备件"] == "B包"


def test_inline_numbered_labels_and_table_rows():
    sections = [_sec("第四部分采购需求", subs=[
        _sec("（三）工作内容", [
            "开展门户网站群运维与内容检测工作，须保障全年稳定运行。",
            "1.A包：2026年度门户网站群系统运行维护",
            "每月须开展1次安全巡检。",
            "2.B包：部属网站和新媒体内容检测运维服务",
            "须24小时不间断开展内容检测。",
        ]),
        _sec("（四）服务清单", tables=[[["包号", "服务要求"],
                                      ["包1：网络安全", "须提供渗透测试服务"],
                                      ["包2：等保测评", "须完成6个三级系统测评"]]]),
    ])]
    items, _, _ = deviation_engine.extract_from_structure(sections)
    assert _packages(items) == {
        "开展门户网站群运维与内容检测工作，须保障全年稳定运行。": "",
        "每月须开展1次安全巡检。": "A包",
        "须24小时不间断开展内容检测。": "B包",
        "包1：网络安全：须提供渗透测试服务": "包1",
        "包2：等保测评：须完成6个三级系统测评": "包2",
    }


def test_unmatched_target_package_is_not_filtered():
    sections = [_sec("第二篇 项目服务需求", subs=[
        _sec("（一）包1服务要求", ["投标人须提供网络安全监测服务。"]),
        _sec("（二）包2服务要求", ["投标人须提供等保测评服务。"]),
    ])]
    items, _, info = deviation_engine.extract_from_structure(sections, target_package="A包")
    assert len(items) == 2 and not info["filtered"]
    assert "对应不上" in info["note"]
    single = [_sec("第二篇 项目服务需求", ["投标人须提供网络安全监测服务。"])]
    items, _, info = deviation_engine.extract_from_structure(single, target_package="")
    assert info["found"] == [] and info["note"] == "" and items[0].package == ""


def test_identical_requirements_remain_distinct_across_packages_and_sections():
    clause = "系统须支持7×24小时监控。"
    sections = [_sec("第二篇 项目服务需求", subs=[
        _sec("包1服务要求", [clause, clause]),
        _sec("包2服务要求", [clause]),
        _sec("三、验收标准", [clause]),
    ])]
    items, _, _ = deviation_engine.extract_from_structure(sections)
    assert len(items) == 3
    assert {it.package for it in items} == {"包1", "包2", ""}
    selected, _, info = deviation_engine.extract_from_structure(sections, target_package="包2")
    assert len(selected) == 2 and {it.package for it in selected} == {"包2", ""}
    assert info["excluded"] == 1


@pytest.mark.parametrize("text,expected", [
    ("第2包", "包2"),
    ("A包:", "A包"),
    ("包2：等保及密评服务", "包2"),
    ("1.A包：2026年度自然资源部门户网站群系统运行维护", "A包"),
    ("（3）C包：综合业务管理平台优化完善", "C包"),
    ("二、包一招标项目服务需求", None),  # 标签后直接接文字：不是标签段落（标题另按标题处理）
    ("A包、B包通用要求", ""),
    ("A包：90%，B包90%，C包：80%", ""),
    ("A包主要内容：核心交换机维保服务1年。", None),
    ("包1年免费维保", None),
    ("投标人须提供包1所列全部服务", None),
])
def test_package_marker(text, expected):
    assert deviation_engine._package_marker(text) == expected


def test_parser_records_table_positions():
    builder = DocumentTreeBuilder()
    builder.add_heading("二、采购清单", 1)
    builder.add_paragraph("A包：")
    builder.add_table([["序号", "服务要求"], ["1", "须维保"]])
    builder.add_paragraph("B包：")
    builder.add_table([["序号", "服务要求"], ["1", "须授权"]])
    section = builder.result("x.docx", "styles")["sections"][0]
    assert section["table_anchors"] == [1, 2]


def test_api_extract_keeps_target_package(client, project_id):
    sections = [_sec("第二篇 项目服务需求", subs=[
        _sec("（一）包1服务要求", ["投标人须提供网络安全监测服务。"]),
        _sec("（二）包2服务要求", ["投标人须提供等保测评服务。", "投标人须提交测评报告。"]),
        _sec("三、验收标准", ["投标人须按国家标准提交验收文档。"]),
    ])]
    project_store.set_tender_document(project_id, "", {"sections": sections})

    def mutate(project):
        project.tender_analysis = TenderAnalysis18(target_package="包2", scoring_items=[
            ScoringItem(id="pkg1_s1", package="包1", name="方案", points=100),
            ScoringItem(id="pkg2_s2", package="包2", name="方案", points=100),
        ])

    project_store.update(project_id, mutate)
    res = client.post(f"/api/v1/project/{project_id}/deviation/extract", json={})
    assert res.status_code == 200
    data = res.json()
    assert [it["package"] for it in data["items"]] == ["包2", "包2", ""]
    assert data["coverage"]["package"]["excluded"] == 1 and data["coverage"]["note"] == ""
    stored = client.get(f"/api/v1/project/{project_id}/deviation").json()["items"]
    assert [it["package"] for it in stored] == ["包2", "包2", ""]


# ---------------- 真实招标文件（仓库不附带，存在时验证） ----------------

REAL_MULTI = ["01_*.docx", "04_*.docx", "01_*.pdf", "04_*.pdf", "05_*.pdf"]


def _real(pattern):
    matches = sorted(REAL_DIR.glob(pattern)) if REAL_DIR.exists() else []
    if not matches:
        pytest.skip("未找到真实招标文件 downloads/ccgp_2026-10-06")
    return matches[0]


@lru_cache(maxsize=None)
def _parse(path: Path):
    return parse_document(str(path))


@pytest.mark.parametrize("pattern", REAL_MULTI)
def test_real_multi_package_tenders_split(pattern):
    parsed = _parse(_real(pattern))
    rubric = list(dict.fromkeys(it.package for it in extract_scoring_items(parsed["tables"])))
    everything, _, info = deviation_engine.extract_from_structure(parsed["sections"], parsed["full_text"])
    assert info["found"] == rubric, "需求篇的分包标签要与评分表的分包对应"
    for target in rubric:
        items, _, info = deviation_engine.extract_from_structure(parsed["sections"], parsed["full_text"], target)
        assert info["filtered"] and info["excluded"] > 0
        assert len(items) + info["excluded"] == len(everything)
        assert {it.package for it in items} <= {target, ""}
        assert sum(1 for it in items if it.package == target) >= 5, (target, len(items))


def test_real_shared_sections_stay_common():
    parsed = _parse(_real("04_*.pdf"))
    items, _, _ = deviation_engine.extract_from_structure(parsed["sections"], parsed["full_text"], "A包")
    by_section = {}
    for it in items:
        by_section.setdefault(it.section.split(" > ")[1], set()).add(it.package)
    assert by_section["五、验收标准"] == {""} and by_section["六、采购标的的其他技术、服务等要求"] == {""}
    assert by_section["三、技术要求"] == {"A包"}, "B包的技术要求不进 A包的偏离表"
