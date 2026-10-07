"""
招标文件写法变体（来自中央/地方代理机构的真实文件，非重庆模板）：
- 评分表：表头格横跨两列（「评审因素」盖住大类与因素名）、没有分值列（分值写在「项目实施团队（16分）」里）、
  分值格带分值档「10.0（0.0-10.0）」、同一因素名纵向合并而各行分值不同、大类与因素名横向合并的价格行、
  「A包」「第1包」式分包标签、投标文件格式里只有表头的空白评分索引表
- 条款标记定义句：「★代表…，#代表重要指标」「凡标有★条款…无效投标」「标“▲”号项…不作为认定无效投标的依据」
- 规则抽取：同一行并排的下一个字段截掉；「采购人名称 | XX」「采购人信息 / 名称：XX」；正文里的「采购人 承担…」不是字段
"""
import pytest

from app.services.parser.scoring_extractor import (
    _package_label, extract_scoring_items, parse_points_cell, summarize,
)
from app.services.parser.tender_analyzer import is_marker_reference, marker_semantics, tender_analyzer


def _table(rows, keys, context="", breadcrumb=""):
    return {"index": 0, "breadcrumb": breadcrumb, "context_before": context, "rows": rows, "cell_keys": keys}


def _totals(items):
    out = {}
    for it in items:
        out[it.package] = out.get(it.package, 0) + (it.points or 0)
    return out


def test_header_spanning_two_columns_and_split_points():
    """「评审因素」横跨大类与因素名两列；同一因素名格纵向合并、各行分值不同 → 一项，分值相加"""
    rows = [
        ["评审因素", "具体要求", "分值"],
        ["价格部分（10分）", "投标报价", "满足招标文件要求且投标价格最低的投标报价为评标基准价。", "10"],
        ["商务部分（20分）", "类似业绩", "每提供1个类似项目业绩得2分，最高得20分。", "20"],
        ["技术部分（70分）", "集成服务方案", "投标人需提供集成服务方案，内容包括但不限于：①用户身份认证集成②密码服务平台集成", "4"],
        ["技术部分（70分）", "集成服务方案", "（1）集成服务方案内容详实、表述清晰，得5分；（2）基本完整，得3分。", "5"],
        ["技术部分（70分）", "应急方案", "（1）应急方案内容详实、表述清晰，得61分。", "61"],
    ]
    keys = [[0, 1, 2], [3, 4, 5, 6], [7, 8, 9, 10], [11, 12, 13, 14], [11, 12, 15, 16], [11, 17, 18, 19]]
    items = extract_scoring_items([_table(rows, keys)])
    assert [(it.name, it.points) for it in items] == [
        ("投标报价", 10), ("类似业绩", 20), ("集成服务方案", 9), ("应急方案", 61)]
    assert items[2].category == "技术部分（70分）" and "得5分" in items[2].criteria
    assert items[0].kind == "price" and _totals(items) == {"": 100}


def test_points_inside_factor_name_and_letter_packages():
    """没有分值列：分值写在评分因素里；表前「A包：」「B包：」是分包；资格审查表不是评分表"""
    def package(label, tech):
        rows = [
            ["分值构成(总分100分)", f"价格分值：30分技术部分：{tech}分商务部分：{70 - tech}分"],
            ["评审项", "评分因素", "评标标准"],
            ["价格分（30分）", "投标价格（30分）", "满足招标文件要求且投标价格最低的投标报价为评标基准价。"],
            [f"技术部分（{tech}分）", f"项目实施团队（{tech - 10}分）", "投标人针对本项目配备的网络工程师……"],
            [f"技术部分（{tech}分）", "培训方案（10分）", "投标人提供针对本项目的培训方案……"],
            [f"商务部分（{70 - tech}分）", f"业绩（{70 - tech}分）", "投标人提供2023年1月1日以来类似项目的业绩……"],
        ]
        keys = [[0, 1], [2, 3, 4], [5, 6, 7], [8, 9, 10], [8, 11, 12], [13, 14, 15]]
        return _table(rows, keys, context=f"（6）评标标准\n{label}：")

    qualification = _table(
        [["评审项", "评审因素", "评审标准"], ["资格审查", "营业执照", "符合"], ["资格审查", "信用记录", "符合"]],
        [[0, 1, 2], [3, 4, 5], [3, 6, 7]], context="资格审查表")
    items = extract_scoring_items([qualification, package("A包", 52), package("B包", 53)])
    assert _totals(items) == {"A包": 100, "B包": 100}
    first = [it for it in items if it.package == "A包"]
    assert [(it.name, it.points) for it in first] == [
        ("投标价格", 30), ("项目实施团队", 42), ("培训方案", 10), ("业绩", 18)]
    assert first[1].category == "技术部分（52分）"


def test_point_steps_and_category_name_columns():
    """「类别 | 评审因素 | 评分标准说明 | 分值」：评审因素是名称；分值格里的分值档不是名称"""
    rows = [
        ["类别", "评审因素", "评分标准说明（同一指标不得重复打分，★代表实质性指标）", "分值"],
        ["价格分", "价格评审", "满足招标文件要求且投标价格最低的投标报价为评标基准价。", "10.0（0.0-10.0）"],
        ["客观分（商务、技术及服务部分）", "稿件内容校对", "对相关内容、栏目进行专业校对……", "5.0（0,2.5,5）"],
        ["客观分（商务、技术及服务部分）", "稿件发布有关的内容保障和安全服务", "具备完善的采编业务规范……", "85.0（0,1,2,3,4,5, 6）"],
    ]
    keys = [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9, 10, 11], [8, 12, 13, 14]]
    items = extract_scoring_items([_table(rows, keys, context="第1包详细评审标准")])
    assert [(it.package, it.name, it.points) for it in items] == [
        ("包1", "价格评审", 10), ("包1", "稿件内容校对", 5), ("包1", "稿件发布有关的内容保障和安全服务", 85)]
    assert parse_points_cell("6.0（0,1,2,3,4,5, 6）") == ("", 6.0)
    assert parse_points_cell("技术服务方案 （50分）") == ("技术服务方案", 50.0)


def test_merged_price_row_and_template_table_not_a_package():
    """大类与因素名横向合并的价格行（少一格）照样识别；投标文件格式里的空白评分索引表不算一个分包"""
    template = _table([["评分索引表"], ["序号", "评分因素", "分值", "评分标准", "对应页码"]],
                      [[0], [1, 2, 3, 4, 5]], breadcrumb="第五部分投标文件格式")
    rows = [
        ["评分内容", "评分因素", "评分标准说明", "分值"],
        ["商务部分（30分）", "企业资质", "提供质量管理体系认证证书……", "30分"],
        ["技术部分（60分）", "技术方案", "方案合理科学……", "60分"],
        ["价格部分（10分）", "满足招标文件要求且最终报价最低的投标报价为评标基准价。", "10分"],
    ]
    keys = [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9, 10, 11], [12, 13, 14]]
    items = extract_scoring_items([template, _table(rows, keys, context="2)评分具体标准")])
    assert [(it.package, it.name, it.points, it.kind) for it in items] == [
        ("", "企业资质", 30, "business"), ("", "技术方案", 60, "technical"), ("", "价格部分", 10, "price")]
    assert "合计 100 分" in summarize(items)


@pytest.mark.parametrize("context, label", [
    ("第1包详细评审标准", "包1"),
    ("（二）包2评审因素", "包2"),
    ("分包一评分表", "包1"),
    ("3.c包：自然资源部信息中心综合业务管理平台优化完善", "C包"),
    ("包B 评分标准", "B包"),
    ("WiFi包含在报价内", ""),
])
def test_package_labels(context, label):
    assert _package_label({"context_before": context}) == label


# ---------------- 条款标记定义句 ----------------

def test_marker_legend_with_several_markers_in_one_sentence():
    text = ("①指标按重要性分为“★”、“☆”、“#”和“△”。★代表实质性指标，不满足该指标项将导致投标被拒绝，"
            "☆代表优质优价指标，#代表重要指标，△则表示一般指标项。")
    semantics = marker_semantics(text)
    assert semantics["★"][0] == "invalid" and semantics["#"][0] == "deduct"
    assert "☆" not in semantics and "△" not in semantics
    assert is_marker_reference(text)

    result = tender_analyzer.analyze_text(text + "\n1 | ★ | 稿件版面编排 | 按要求进行编辑和排版\n2 | # | 英文留言处理 | 即时初筛")
    assert result.star_disqualification_items == ["1 | ★ | 稿件版面编排 | 按要求进行编辑和排版"]
    assert result.important_items == ["2 | # | 英文留言处理 | 即时初筛"]


@pytest.mark.parametrize("line, marker, level", [
    ("招标文件中凡标有★条款均为实质性要求条款，投标文件须完全响应，未实质响应的，按照无效投标处理。", "★", "invalid"),
    ("2、标“★”号项（如有）为不允许负偏离的实质性要求和条件，如不满足要求则不通过符合性审查，为无效投标；", "★", "invalid"),
    ("3、标“▲”号项（如有）为重要条款，评标时为重要评分项，不作为认定无效投标的依据；", "▲", "deduct"),
    ("“※”标注的服务需求为符合性审查中的实质性要求，若不满足按无效投标处理。", "※", "invalid"),
])
def test_marker_legend_phrasings(line, marker, level):
    semantics = marker_semantics(line)
    assert semantics[marker] == (level, line)
    assert is_marker_reference(line)


def test_marked_clause_is_not_a_legend():
    for line in ("技术指标★支持国密SM4", "★投标人资格：具有独立承担民事责任的能力", "7 | ★号条款响应（如有） | 满足★号条款要求的"):
        assert not is_marker_reference(line)
    assert marker_semantics("7 | ★号条款响应（如有） | 投标文件满足第五章中★号条款要求的；") == {
        "★": ("invalid", ""), "▲": ("deduct", "")}


# ---------------- 规则抽取 ----------------

def test_rule_fields_from_pdf_side_by_side_lines():
    text = ("项目名称：中央政府采购管理系统升级改造项目项目编号：ZZGJ20250268\n"
            "联合体各方应当共同与采购人签订采购合同，就采购合同约定的事项对采购人 承担连带责任。\n"
            "采购人名称：自然资源部信息中心采购人地址：北京市海淀区莲花池西路28号\n")
    result = tender_analyzer.analyze_text(text)
    assert result.project_name == "中央政府采购管理系统升级改造项目"
    assert result.tender_number == "ZZGJ20250268"
    assert result.purchaser_name == "自然资源部信息中心"


@pytest.mark.parametrize("text, buyer", [
    ("序号 | 内容 | 说明与要求\n1 | 采购人名称 | 国务院办公厅机关服务中心\n", "国务院办公厅机关服务中心"),
    ("1.采购人信息\n名 称：自然资源部信息中心\n地 址：北京市海淀区\n", "自然资源部信息中心"),
    ("对采购人 承担连带责任。\n", ""),
])
def test_rule_purchaser_variants(text, buyer):
    assert tender_analyzer.analyze_text(text).purchaser_name == buyer
