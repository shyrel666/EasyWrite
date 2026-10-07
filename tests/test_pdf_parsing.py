"""
PDF 招标文件解析：
- 合成 PDF：页眉页脚剔除、折行拼段、加粗（含伪粗体）/放大字号标题与编号体例层级、
  跨页表格拼接（被截断的行接回上一行、纵向合并单元格编号沿用）、书签优先识别标题
- 扫描件 / 加密 / 乱码：明确报错，不输出不可用文本
- 上传接口：.pdf 走同一拆标任务，返回来源说明
- 真实招标文件（downloads/ccgp_*/pdf 存在时，由同目录 docx 经 Word 导出）：每包评分合计 100，
  书签版标题与 docx 基本一致
"""
import time
from functools import lru_cache
from pathlib import Path

import pymupdf
import pytest

from app.services.parser.document_parser import describe_source, parse_document, unsupported_reason
from app.services.parser.pdf_parser import PdfParseError, pdf_parser
from app.services.parser.scoring_extractor import extract_scoring_items, summarize
from app.services.parser.word_parser import WordDocumentParser

REAL_DIR = Path(__file__).resolve().parent.parent / "downloads" / "ccgp_2026-10-06"
FONT = "china-s"
W, H = 595, 842
LEFT, RIGHT = 72, 523
SIZE = 10.5
COLS = [LEFT, 100, 160, 260, RIGHT]
_font = pymupdf.Font(FONT)


def _text(page, x, y, s, size=SIZE, bold=False):
    # render_mode=2（填充 + 描边）即 Word 对宋体等的伪粗体
    page.insert_text((x, y), s, fontname=FONT, fontsize=size, render_mode=2 if bold else 0,
                     border_width=0.05 if bold else 1)


def _fill(prefix: str, width: float, size=SIZE) -> str:
    """用重复短语把一行补到恰好写满指定宽度"""
    filler = "运维保障服务"
    s = prefix
    while _font.text_length(s + filler[0], fontsize=size) <= width:
        s += filler[len(s) % len(filler)]
    return s


def _cell(page, rect, lines):
    page.draw_rect(rect, color=(0, 0, 0), width=0.6)
    y = rect[1] + 14
    for s in lines:
        _text(page, rect[0] + 5, y, s)
        y += 15.5


def build_tender_pdf(path: Path, bookmarks: bool = False) -> Path:
    doc = pymupdf.open()
    for _ in range(2):
        doc.new_page(width=W, height=H)
    pages = [doc[0], doc[1]]  # 新增页面后先前的页面对象会失效，统一重新取
    for no, page in enumerate(pages, 1):
        _text(page, LEFT, 50, "某某招标代理有限公司", size=9)  # 页眉
        _text(page, W / 2 - 10, 800, f"- {no} -", size=9)  # 页码
    p1, p2 = pages
    _text(p1, 220, 100, "第二篇 技术需求", size=16)
    _text(p1, LEFT, 130, "一、服务要求", bold=True)
    _text(p1, LEFT + 21, 155, _fill("本项目为智慧园区运维服务，投标人须提供", RIGHT - LEFT - 21))  # 首行缩进、写满
    _text(p1, LEFT, 173, "并配备不少于十名驻场工程师。")
    _text(p1, LEFT + 21, 191, "（1）故障响应时间不超过三十分钟；")
    _text(p1, LEFT, 215, "1.服务范围：覆盖园区全部信息系统。")  # 正文里的编号行，不是标题

    cols = COLS
    rows = [(600, 624), (624, 720), (720, 768)]
    for c, head in enumerate(["序号", "评分因素", "分值", "评分标准"]):
        _cell(p1, (cols[c], rows[0][0], cols[c + 1], rows[0][1]), [head])
    for c, val in enumerate(["1", "技术部分", "技术方案20分", "方案完整得20分。"]):
        _cell(p1, (cols[c], rows[1][0], cols[c + 1], rows[1][1]), [val])
    for c, val in enumerate(["2", "技术部分", "服务方案30分"]):
        _cell(p1, (cols[c], rows[2][0], cols[c + 1], rows[2][1]), [val])
    # 第 2 行评分标准被分页截断：三行写满、末行贴着格子底部，后半句在下一页
    _cell(p1, (cols[3], rows[2][0], cols[4], rows[2][1]), [
        "服务方案包括人员配置、应急预案、培训计划与质量保",
        "障措施、驻场安排与考核办法，内容完整而且针对性强",
        "的得三十分；方案内容存在缺漏或错误的，每缺少一项",
    ])

    for c in range(3):  # 续页：被截断行的空续格
        p2.draw_rect((cols[c], 72, cols[c + 1], 100), color=(0, 0, 0), width=0.6)
    _cell(p2, (cols[3], 72, cols[4], 100), ["扣五分。"])
    for c, val in enumerate(["3", "商务部分", "人员50分", "项目经理具备高级职称。"]):
        _cell(p2, (cols[c], 100, cols[c + 1], 130), [val])
    _text(p2, LEFT, 170, "二、验收要求", bold=True)
    _text(p2, LEFT + 21, 195, "按国家标准验收。")
    if bookmarks:
        doc.set_toc([[1, "第二篇 技术需求", 1], [2, "一、服务要求", 1], [2, "二、验收要求", 2]])
    doc.save(str(path))
    return path


def _titles(sections, out=None):
    out = [] if out is None else out
    for s in sections:
        out.append((s["level"], s["title"]))
        _titles(s["subsections"], out)
    return out


def _find(sections, title):
    for s in sections:
        if s["title"] == title:
            return s
        hit = _find(s["subsections"], title)
        if hit:
            return hit
    return None


@pytest.mark.parametrize("bookmarks", [False, True])
def test_synthetic_pdf_structure(tmp_path, bookmarks):
    r = pdf_parser.parse_pdf(build_tender_pdf(tmp_path / "t.pdf", bookmarks))
    assert r["heading_mode"] == ("bookmarks" if bookmarks else "layout")
    assert _titles(r["sections"]) == [(1, "第二篇 技术需求"), (2, "一、服务要求"), (2, "二、验收要求")]

    text = r["full_text"]
    assert "某某招标代理有限公司" not in text and "- 1 -" not in text  # 页眉页码剔除
    body = _find(r["sections"], "一、服务要求")["content"].split("\n\n")
    assert body[0].startswith("本项目为智慧园区运维服务") and body[0].endswith("并配备不少于十名驻场工程师。")
    assert body[1] == "（1）故障响应时间不超过三十分钟；"
    assert body[2] == "1.服务范围：覆盖园区全部信息系统。"  # 未加粗的编号正文不升为标题

    (table,) = r["tables"]
    assert table["rows"][2][3].endswith("每缺少一项扣五分。")  # 截断行接回
    assert [row[0] for row in table["rows"]] == ["序号", "1", "2", "3"]
    items = extract_scoring_items(r["tables"])
    assert [(it.name, it.points) for it in items] == [("技术方案", 20), ("服务方案", 30), ("人员", 50)]
    assert "扣五分" in items[1].criteria


def test_split_row_vs_new_row(tmp_path):
    """上一页末格是写完的短内容时，下一页首行是新行（不能把新评分项并进上一项）"""
    doc = pymupdf.open()
    doc.new_page(width=W, height=H)
    doc.new_page(width=W, height=H)
    p1, p2 = doc[0], doc[1]
    cols = COLS
    for y in range(80, 680, 20):
        _text(p1, LEFT, y, "评标委员会按照下列评分办法对通过资格审查的投标文件进行综合评分。")
    for c, head in enumerate(["序号", "评分因素", "分值", "评分标准"]):
        _cell(p1, (cols[c], 700, cols[c + 1], 724), [head])
    for c, val in enumerate(["1", "技术部分", "技术方案40分", "方案完整得40分。"]):
        _cell(p1, (cols[c], 724, cols[c + 1], 768), [val])
    for c, val in enumerate(["2", "技术部分", "服务方案60分", "方案可行得60分。"]):
        _cell(p2, (cols[c], 72, cols[c + 1], 110), [val])
    _text(p2, LEFT, 150, "以上为评分办法。")
    path = tmp_path / "n.pdf"
    doc.save(str(path))
    r = pdf_parser.parse_pdf(path)
    assert [row[2] for row in r["tables"][0]["rows"]] == ["分值", "技术方案40分", "服务方案60分"]
    assert summarize(extract_scoring_items(r["tables"])) == "评分表：2 项，合计 100 分"


def test_scanned_pdf_rejected(tmp_path):
    doc = pymupdf.open()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 280), False)
    pix.set_rect(pix.irect, (255, 255, 255))
    for _ in range(3):
        doc.new_page(width=W, height=H).insert_image(pymupdf.Rect(0, 0, W, H), pixmap=pix)
    path = tmp_path / "scan.pdf"
    doc.save(str(path))
    with pytest.raises(PdfParseError, match="扫描件"):
        pdf_parser.parse_pdf(path)


def test_encrypted_pdf_rejected(tmp_path):
    path = build_tender_pdf(tmp_path / "plain.pdf")
    locked = tmp_path / "locked.pdf"
    doc = pymupdf.open(str(path))
    doc.save(str(locked), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    with pytest.raises(PdfParseError, match="加密"):
        pdf_parser.parse_pdf(locked)


def test_garbled_pdf_rejected(tmp_path, monkeypatch):
    from app.services.parser.pdf_parser import PdfDocumentParser
    original = PdfDocumentParser._page_lines

    def garbled(page, flags):  # 字体缺 Unicode 映射时提取出来的是私用区字符
        lines = original(page, flags)
        for line in lines:
            line.text = "".join(chr(0xE000 + ord(ch) % 100) for ch in line.text)
        return lines

    monkeypatch.setattr(PdfDocumentParser, "_page_lines", staticmethod(garbled))
    with pytest.raises(PdfParseError, match="乱码"):
        pdf_parser.parse_pdf(build_tender_pdf(tmp_path / "g.pdf"))


def test_document_dispatch(tmp_path):
    assert unsupported_reason("a.PDF") is None and unsupported_reason("a.docx") is None
    assert "另存为 .docx" in unsupported_reason("a.doc")
    assert "仅支持" in unsupported_reason("a.txt")
    parsed = parse_document(build_tender_pdf(tmp_path / "t.pdf"))
    assert parsed["source_format"] == "pdf" and parsed["page_count"] == 2
    assert describe_source(parsed).startswith("PDF 共 2 页，PDF 无书签")


def test_upload_pdf_tender(client, project_id, tmp_path):
    path = build_tender_pdf(tmp_path / "招标文件.pdf", bookmarks=True)
    with open(path, "rb") as f:
        res = client.post("/api/v1/tender/analyze", files={"file": ("招标文件.pdf", f, "application/pdf")},
                          data={"project_id": project_id})
    assert res.status_code == 200, res.text
    task = {}
    for _ in range(100):
        task = client.get(f"/api/v1/tasks/{res.json()['task_id']}").json()
        if task["status"] in ("completed", "failed"):
            break
        time.sleep(0.1)
    assert task["status"] == "completed", task.get("error")
    result = task["result"]
    assert result["source_note"] == "PDF 共 2 页，按 PDF 书签识别章节"
    assert [it["points"] for it in result["scoring_items"]] == [20, 30, 50]
    assert "按国家标准验收" in client.get(f"/api/v1/project/{project_id}/tender/text").json()["text"]

    bad = client.post("/api/v1/tender/analyze", files={"file": ("旧版.doc", b"x", "application/msword")})
    assert bad.status_code == 400 and "另存为 .docx" in bad.json()["detail"]


# ---------------- 真实招标文件（Word 导出的 PDF） ----------------

def _real_pdfs():
    return sorted((REAL_DIR / "pdf").glob("*.pdf")) if (REAL_DIR / "pdf").exists() else []


@lru_cache(maxsize=None)
def _parse_real(pdf: Path):
    return pdf_parser.parse_pdf(pdf)


@pytest.mark.skipif(not _real_pdfs(), reason="真实招标文件 PDF 不在本地（downloads/ 未纳入版本库）")
@pytest.mark.parametrize("pdf", _real_pdfs(), ids=lambda p: p.name)
def test_real_pdf_rubric_totals_100(pdf):
    parsed = _parse_real(pdf)
    items = extract_scoring_items(parsed["tables"])
    assert items, f"{pdf.name} 未识别到评分表"
    by_pkg = {}
    for it in items:
        by_pkg.setdefault(it.package, []).append(it)
    for pkg, group in by_pkg.items():
        assert all(it.points is not None for it in group), (pdf.name, pkg, [it.name for it in group])
        assert abs(sum(it.points for it in group) - 100) < 0.01, (pdf.name, pkg, summarize(items))


@pytest.mark.skipif(not _real_pdfs(), reason="真实招标文件 PDF 不在本地（downloads/ 未纳入版本库）")
@pytest.mark.parametrize("pdf", [p for p in _real_pdfs() if p.stem.endswith("_bookmarks")], ids=lambda p: p.name)
def test_real_pdf_bookmark_headings_match_docx(pdf):
    docx_file = next(REAL_DIR.glob(pdf.name[:2] + "*.docx"), None)
    if docx_file is None:
        pytest.skip("缺少对应的 docx")
    parsed = _parse_real(pdf)
    assert parsed["heading_mode"] == "bookmarks"
    def key(title):
        return "".join(ch for ch in title if ch.isalnum())

    pdf_titles = [key(t) for _, t in _titles(parsed["sections"])]
    docx_titles = [key(t) for _, t in _titles(WordDocumentParser().parse_docx(docx_file)["sections"])]
    # PDF 标题含 Word 自动编号（「第一篇」「（一）」），docx 文本没有：按包含关系比对
    found = sum(1 for d in docx_titles if any(d in p for p in pdf_titles))
    assert found >= 0.95 * len(docx_titles), (pdf.name, found, len(docx_titles))
