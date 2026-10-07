"""
PDF 版面变体（真实代理机构文件：WPS 导出 / Aspose 生成，非 Word 导出）：
- 页眉：随章节变化的页眉（同一位置、少数几种文字）、三行高的页眉（超出 11% 判定带的原样重复文字）
- 表格：页眉横线贴着表格上边框多出的空边列、白色段落底纹被当成一行单元格、
  同一行有格子在句中被截断时其余格在段落边界处断开也算跨页续写
- 段落：各页行距不同、WPS 字符网格使满行右端参差、段落比版心窄、跨页的悬挂缩进、
  行首单独排版的条款编号「1.2」、「15.3 如果…」式编号
- 目录页（无页码的目录）与残缺书签（WPS 把正文条目生成书签、各章没有书签）
- 真实文件（downloads/ccgp_2026-10-06/0*.pdf 存在时）：每包评分合计 100、分包标签、章节方式、采购人、典型断句
"""
import itertools
from functools import lru_cache
from pathlib import Path

import pymupdf
import pytest

from app.services.parser.deviation_engine import deviation_engine
from app.services.parser.document_parser import describe_source
from app.services.parser.pdf_parser import _Grid, _Line, _Paragraph, _clean_text, pdf_parser
from app.services.parser.scoring_extractor import extract_scoring_items, summarize
from app.services.parser.tender_analyzer import tender_analyzer

REAL_DIR = Path(__file__).resolve().parent.parent / "downloads" / "ccgp_2026-10-06"
W, H = 595, 842


def L(page, x0, y0, x1, text, size=10.5, height=None, bold=False):
    return _Line(page, (x0, y0, x1, y0 + (height or size)), text, size, bold)


def P(text, page=0):
    return _Paragraph(L(page, 90, 100, 300, text))


# ---------------- 页眉页脚 ----------------

def test_running_and_tall_headers_stripped():
    chapters = ["投标人须知"] * 4 + ["评标方法和评标标准"] * 3 + ["采购需求"] * 3
    # 正文首行也在判定带内，每页文字不同，不能误删（不能只差数字：比较页眉时数字归一）
    openings = ["投标人应仔细阅读招标文件", "投标文件应按规定格式编制", "投标保证金按前附表缴纳", "开标在电子平台进行",
                "评标委员会依法组建", "综合评分法计算得分", "价格分按基准价计算", "服务期限为一年",
                "驻场人员不少于五人", "验收按国家标准执行"]
    pages = []
    for no, chapter in enumerate(chapters):
        pages.append([
            L(no, 71, 46, 437, "中经国际工程咨询集团有限公司", size=22),
            L(no, 71, 57, 180, chapter),  # 随章节变化的页眉
            L(no, 71, 100, 340, "地址：北京市朝阳区东土城路12号"),  # 第三行页眉：超出 11% 判定带
            L(no, 90, 75, 520, openings[no]),
            L(no, 90, 140, 520, f"第{no}页正文内容"),
            L(no, 290, 788, 305, f"第 {no + 1} 页"),
        ])
    pdf_parser._strip_page_furniture(pages, [(W, H)] * len(pages))
    for no, lines in enumerate(pages):
        assert [l.text for l in lines] == [openings[no], f"第{no}页正文内容"]


# ---------------- 表格 ----------------

def test_phantom_edge_columns_dropped():
    def grid(rules):
        g = _Grid(0, (71, 119, 565, 624), [70.9, 84.7, 143.6, 196.5, 464.4, 510.6, 564.7], [[1, 2, 3, 4, 5, 6]])
        g.texts = {1: "", 2: "技术部分", 3: "安全保障", 4: "提供完整、合理的方案", 5: "6分", 6: ""}
        pdf_parser._drop_phantom_columns(g, [(x, 119, 624) for x in rules])
        return g

    inner = [84.7, 143.6, 196.5, 464.4, 510.6]
    assert grid(inner).xs == inner  # 页眉横线造成的两条空边列：外侧没有竖线
    assert grid([70.9] + inner).xs[0] == 70.9  # 有竖线的空列是表格自己的


def test_white_fill_not_a_table_row():
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    xs, ys = [80, 145, 220, 568], [100, 140, 180, 220]
    for x in xs:
        page.draw_line((x, ys[0]), (x, ys[-1]), color=(0, 0, 0), width=0.6)
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y), color=(0, 0, 0), width=0.6)
    for r in range(3):
        for c in range(3):
            page.insert_text((xs[c] + 4, ys[r] + 16), f"格{r}{c}", fontname="china-s", fontsize=10.5)
    # 紧贴表格下边框的白色段落底纹（WPS），右端不到表格右边框
    page.draw_rect(pymupdf.Rect(80, 220, 522, 246), color=None, fill=(1, 1, 1))
    page.insert_text((84, 238), "B包：", fontname="china-s", fontsize=10.5)
    assert page.find_tables().tables[0].row_count == 4  # 不过滤时白底被当成表格一行

    flags = (pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES) | pymupdf.TEXT_COLLECT_STYLES
    (grid,) = pdf_parser._page_tables(page, pdf_parser._page_lines(page, flags), itertools.count(1))
    assert len(grid.xs) == 4 and len(grid.grid) == 3
    assert not any("B包" in t for t in grid.texts.values())


def test_row_split_mid_paragraph_in_one_cell():
    """因素名格在词中被截断（多行写满）→ 整行跨页；评分标准格恰在段落边界断开（末行未写满）也是续写"""
    def grids(strong):
        prev = _Grid(0, (59, 71, 536, 772), [59, 123, 196, 485, 536], [[1, 2, 3, 4]])
        prev.texts = {1: "技术部分（77分）", 2: "安全保密管理措施和技", 3: "供应商提供对本项目的保密承诺，得1分，否则得0分。", 4: "1"}
        prev.cut = {1: False, 2: True, 3: False, 4: False}
        prev.bottom = {1: False, 2: True, 3: True, 4: False}
        prev.strong = {1: False, 2: strong, 3: False, 4: False}
        cur = _Grid(1, (59, 71, 536, 120), [59, 123, 196, 485, 536], [[5, 6, 7, 8]])
        cur.texts = {5: "", 6: "术方案", 7: "注：供应商应提供保密承诺函，并加盖公章。", 8: ""}
        cur.cut = cur.bottom = cur.strong = {5: False, 6: False, 7: False, 8: False}
        return prev, cur

    prev, cur = grids(strong=True)
    assert pdf_parser._is_split_row(prev, cur, cur.grid[0])
    pdf_parser._append_rows(prev, cur)
    rows, _ = pdf_parser._grid_rows(prev)
    assert rows == [["技术部分（77分）", "安全保密管理措施和技术方案",
                     "供应商提供对本项目的保密承诺，得1分，否则得0分。 注：供应商应提供保密承诺函，并加盖公章。", "1"]]

    prev, cur = grids(strong=False)  # 单行写满可能只是恰好写满的短内容：按新行处理
    assert not pdf_parser._is_split_row(prev, cur, cur.grid[0])


# ---------------- 段落 ----------------

def test_line_number_box_merged_into_its_row():
    lines = [
        L(0, 104.9, 230.4, 514.4, "投标人若存在任何理解上无法确定之处，均应当按照招标文件所规定的澄清等程序提出，", height=17.2),
        L(0, 76.6, 234.2, 92.3, "1.2"),  # 编号框比正文行矮：按上沿排序会落到正文之后
        L(0, 104.9, 252.4, 377.9, "否则，可能导致的任何不利后果均应当由投标人自行承担。", height=17.2),
    ]
    merged = pdf_parser._merge_same_row(lines)
    assert [l.text for l in merged] == [
        "1.2 投标人若存在任何理解上无法确定之处，均应当按照招标文件所规定的澄清等程序提出，",
        "否则，可能导致的任何不利后果均应当由投标人自行承担。"]
    assert merged[0].x0 == 76.6


def _continues(lines, nxt, margins=(71, 71), rights=(524, 524), gaps=(10, 10)):
    para = _Paragraph(lines[0])
    para.lines = list(lines)
    return pdf_parser._continues_paragraph(para, nxt, list(margins), list(rights), list(gaps))


def test_paragraph_rules_for_wps_layouts():
    first = L(0, 92, 164, 524, "基于网站群现有系统架构，围绕资源信息内容呈现与视觉表达需求，开展资源信息专题的数据")
    second = L(0, 71, 194, 524, "梳理、栏目规划、页面和模板设计、开发，完成专题数据整理、加工和初始化，实现专题")
    # 本页行距 20（2 倍行距）：行间空白 19.5 仍是同一段；按 10 的行距就会断开
    assert _continues([first], second, gaps=(20, 10))
    assert not _continues([first], second, gaps=(10, 10))

    # 字符网格让满行右端参差：句子没写完、差两个字的长行也是写满
    short = L(0, 71, 120, 503, "目需确保具备面向中国疾病预防控制中心信息化环境持续演进的开发")
    tail = L(0, 71, 140, 300, "适配能力，具体要求如下。")
    assert _continues([L(0, 92, 100, 524, "本项目……"), short], tail)
    ended = L(0, 71, 120, 503, "目需确保具备面向中国疾病预防控制中心信息化环境持续演进的开发。")
    assert not _continues([L(0, 92, 100, 524, "本项目……"), ended], tail)

    # 首行缩进的两行段，段落比版心窄：下一行回到段落左边，说明首行已写满
    assert _continues([L(0, 97.6, 430, 509.7, "可在中央政府采购网（www.zycg.gov.cn）在“单独委托")],
                      L(0, 76.6, 444, 300, "项目”栏目或通过投标工具，免费下载招标文件。"), rights=(528, 528))

    # 跨页：悬挂缩进的条目，句子没写完 → 续写；上一页句末结束、本页缩进的是新段落
    item = L(0, 139, 750, 538, "（2）非政府强制采购的节能产品或环境标志产品，依据品目清单和认证证")
    nxt = L(1, 184, 72, 400, "书实施政府优先采购。优先采购的具体规定见第四章。")
    assert _continues([item], nxt, margins=(57, 57), rights=(545, 545))
    done = L(0, 139, 750, 538, "（2）非政府强制采购的节能产品或环境标志产品，依据品目清单和认证。")
    assert not _continues([done], nxt, margins=(57, 57), rights=(545, 545))

    # 「15.3 如果…」是新段落
    assert not _continues([L(0, 138, 708, 524, "（2）在封装处加盖投标人公章，或由法定代表人签字。")],
                          L(0, 102, 730, 524, "15.3 如果投标人未按上述要求密封及加写标记"))


def test_spaced_cjk_collapsed():
    assert _clean_text("网 络 安 全7*24小时运维服务") == "网络安全7*24小时运维服务"
    assert _clean_text("地 址：北京市") == "地址：北京市"
    assert _clean_text("甲方： 乙方：") == "甲方： 乙方："
    assert _clean_text("张 三丰") == "张 三丰"


# ---------------- 目录与书签 ----------------

def test_toc_without_page_numbers_skipped():
    toc = [P("招标文件目录", 1), P("第一章投标邀请", 1), P("第二章项目需求", 1),
           P("一、概念释义", 1), P("四、投标文件的递交", 1)]  # 正文里写作「四、投标文件的提交」
    body = [P("第一章投标邀请", 2), P("许昌市政府采购服务中心受委托，对项目进行国内公开招标。", 2),
            P("第二章项目需求", 6), P("一、概念释义", 12), P("四、投标文件的提交", 14)]
    assert pdf_parser._toc_entries(toc + body) == {id(p) for p in toc}


def test_incomplete_bookmarks_rejected():
    chapters = [P(f"第{n}章 内容{n}") for n in "一二三四五六"]
    others = [P("（1）平台需和学校的统一身份认证进行对接"), P("一、投标人应答索引表")]
    blocks = chapters + others
    inferred = {id(p): 1 for p in chapters}
    junk = {id(chapters[-1]): 1, id(others[0]): 2, id(others[1]): 2}  # 只有「第六章」有书签
    assert not pdf_parser._bookmarks_cover(blocks, junk, inferred)
    good = {id(p): 1 for p in chapters[:4]} | {id(others[1]): 2}
    assert pdf_parser._bookmarks_cover(blocks, good, inferred)

    note = describe_source({"source_format": "pdf", "page_count": 86, "heading_mode": "layout",
                            "bookmarks_ignored": True, "warnings": []})
    assert "书签与正文章节对不上" in note


# ---------------- 真实代理机构 PDF ----------------

REAL_EXPECTED = {
    # 前缀: (分包, 章节方式, 采购人)
    "01": ({"包1", "包2"}, "layout", "国务院办公厅机关服务中心"),  # Aspose 生成，无书签
    "02": ({""}, "bookmarks", "中国疾病预防控制中心"),
    "03": ({""}, "bookmarks", "财政部信息网络中心"),
    "04": ({"A包", "B包"}, "layout", "许昌职业技术学院"),  # 书签残缺，弃用
    "05": ({"A包", "B包", "C包"}, "bookmarks", "自然资源部信息中心"),
}


def _real_agency_pdfs():
    return sorted(p for p in REAL_DIR.glob("0*.pdf") if p.name[:2] in REAL_EXPECTED) if REAL_DIR.exists() else []


@lru_cache(maxsize=None)
def _parse_real(pdf: Path):
    return pdf_parser.parse_pdf(pdf)


needs_real = pytest.mark.skipif(not _real_agency_pdfs(), reason="真实招标文件 PDF 不在本地（downloads/ 未纳入版本库）")


@needs_real
@pytest.mark.parametrize("pdf", _real_agency_pdfs(), ids=lambda p: p.name[:2])
def test_real_agency_pdf_rubrics_and_structure(pdf):
    packages, mode, buyer = REAL_EXPECTED[pdf.name[:2]]
    parsed = _parse_real(pdf)
    assert parsed["heading_mode"] == mode
    items = extract_scoring_items(parsed["tables"])
    totals = {}
    for it in items:
        assert it.points is not None, (pdf.name, it.name)
        totals[it.package] = totals.get(it.package, 0) + it.points
    assert set(totals) == packages, summarize(items)
    assert all(abs(t - 100) < 0.01 for t in totals.values()), summarize(items)
    assert tender_analyzer.analyze_document(parsed, pdf.name).purchaser_name == buyer


@needs_real
def test_real_agency_pdf_details():
    by_prefix = {p.name[:2]: p for p in _real_agency_pdfs()}
    if "04" in by_prefix:
        parsed = _parse_real(by_prefix["04"])
        assert parsed["bookmarks_ignored"]
        chapters = [s["title"] for s in parsed["sections"] if s["title"].startswith("第") and "章" in s["title"][:4]]
        assert chapters == ["第一章投标邀请", "第二章项目需求", "第三章投标人须知前附表", "第四章投标人须知",
                            "第五章政府采购政策功能", "第六章资格审查与评标", "第七章拟签订的合同文本",
                            "第八章投标文件有关格式"], "目录页条目不应成为章节"
    joined = {  # 跨页 / 行距大 / 段落比版心窄处的句子要接上
        "01": ["均应当按照招标文件所规定的澄清等程序提出，否则"],
        "02": ["按月支付了不低于单位所在区县", "依据品目清单和认证证书实施政府优先采购"],
        "03": ["关于进口产品的相关规定依据《政府采购进口产品管理办法》"],
        "05": ["为自然资源部门户网站的版块完善、栏目调整和内容优化决策提供数据支撑"],
    }
    for prefix, snippets in joined.items():
        if prefix in by_prefix:
            text = _parse_real(by_prefix[prefix])["full_text"]
            for s in snippets:
                assert s in text, (prefix, s)
    if "01" in by_prefix:
        parsed = _parse_real(by_prefix["01"])
        items, _ = deviation_engine.extract_from_structure(parsed["sections"], parsed["full_text"])
        levels = {it.level for it in items}
        assert {"redline", "important"} <= levels, "「★代表实质性指标」「#代表重要指标」两种标记都要识别"
