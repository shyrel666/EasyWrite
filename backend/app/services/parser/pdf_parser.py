"""
PDF 招标文件解析（PyMuPDF）：输出与 WordDocumentParser.parse_docx() 同构的结果
（sections 章节树 / tables 扁平表格含 cell_keys / full_text 按序全文），
下游拆标、评分表抽取、偏离表、知识库分块无需区分来源格式。

PDF 里没有段落和样式，只有定位好的文字行，因此：
1. 段落：按行首缩进、上一行是否写满、字号变化、行距空隙把文字行拼回段落；跨页续写的段落会接上
2. 页眉页脚：页面上下边缘、在多页重复出现的文字（数字归一后比较）与页码行剔除
3. 标题：有书签（Word 导出时"使用标题创建书签"）时按书签层级识别，与 Word 标题样式同等对待，
   并沿用 Word 解析的过滤（成句的、「标签：值」行按正文）与编号体例层级归一；
   无书签时只把"加粗或放大字号 + 短行 + 不以句读结尾"的段落当作标题，层级按编号体例的嵌套顺序推断
4. 表格：按表格线识别（无框线的排版表格按正文处理）；跨页断开的表格按列边界对齐后接上，
   去掉重复表头，断在页尾的单元格（下一页空的续格）沿用上一页同列单元格的编号——与 Word 纵向合并单元格同义，
   评分表据此区分"一个评分项跨多行"与"相邻多项同分值"
5. 扫描件 / 加密 / 字体缺 Unicode 映射导致乱码时明确报错，不输出不可用的文本（暂不做文字识别）
"""
import itertools
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from app.services.parser.word_parser import DocumentTreeBuilder, WordDocumentParser


class PdfParseError(ValueError):
    """PDF 无法可靠提取文字（扫描件 / 加密 / 编码不可识别），消息直接展示给用户"""


def _load_pymupdf():
    try:
        import pymupdf
    except ImportError:  # 旧版本只有 fitz 模块名
        try:
            import fitz as pymupdf
        except ImportError:
            raise PdfParseError("未安装 PDF 解析组件（pip install pymupdf），暂无法解析 PDF 文件") from None
    if hasattr(pymupdf, "no_recommend_layout"):
        pymupdf.no_recommend_layout()
    return pymupdf


CJK = "\u3400-\u9fff\uf900-\ufaff"
CJK_PUNCT = "\u3000-\u303f\uff01-\uff60\u2018-\u201f\u2026\xb7"
# Word 导出 PDF 时在中西文之间插入的自动间距（原文并无空格）：「150 人月」→「150人月」
_AUTOSPACE = re.compile(
    rf"(?<=[{CJK}{CJK_PUNCT}])[ \xa0]+(?=[A-Za-z0-9])|(?<=[A-Za-z0-9%])[ \xa0]+(?=[{CJK}{CJK_PUNCT}])"
)
_MULTISPACE = re.compile(r"[ \xa0\t]{2,}")
# 分散对齐的单字之间被拆出的空格：「网 络 安 全」「地 址：」→「网络安全」「地址：」（只收拢逐字隔开的连续单字）
_SPACED_CJK = re.compile(rf"(?<![{CJK}])[{CJK}](?:[ \xa0]+[{CJK}](?![{CJK}]))+")
_LATIN_END = re.compile(r"[A-Za-z0-9,.;:)\]]$")
_LATIN_START = re.compile(r"^[A-Za-z0-9(\[]")
_SENTENCE_STOP = "。；;！!？?"
PAGE_NUMBER = re.compile(
    r"^[-—–\s]*(?:第\s*)?\d{1,4}\s*页?\s*(?:[/／]\s*\d{1,4}|共\s*\d{1,4}\s*页)?[-—–\s]*$"
    r"|^page\s*\d{1,4}(?:\s*of\s*\d{1,4})?$",
    re.I,
)
COVER_DATE = re.compile(r"^[〇○零一二三四五六七八九十\d]{4}\s*年")
_CLAUSE_END = "。；;！!？?：:"
# 段首编号 / 条款标记（「2.5万元」这类小数不算）
PARA_START = re.compile(
    r"^(?:[★※▲◆●■□√]|第[一二三四五六七八九十百\d]+[篇章节条部]|[一二三四五六七八九十]+[、.．]"
    r"|[（(][一二三四五六七八九十\d]{1,3}[）)]|\d{1,2}(?:[.．]\d{1,2})*[、.．)）](?!\d)|[①-⑳]"
    r"|\d{1,2}(?:[.．]\d{1,2})+(?=\s*[^\s\d.．万元亿%％倍个]))"  # 「15.3 如果…」「1.3.4通过…」
)
# 目录行：标题 + 引导点 + 页码（PDF 中制表位引导符导出为一串点）
TOC_LEADER = re.compile(r"[.…·．]{4,}\s*[-—]?\s*\d{1,4}\s*[-—]?\s*$")


def _clean_text(text: str) -> str:
    text = _AUTOSPACE.sub("", text.replace("\u3000", " "))
    text = _SPACED_CJK.sub(lambda m: re.sub(r"[ \xa0]+", "", m.group(0)), text)
    return _MULTISPACE.sub(" ", text).strip()


def _join(parts: List[str], cell: bool = False) -> str:
    """把折行拼回一段：中文直接相连，西文单词之间补空格；单元格内以句号结尾的行视为分段（补空格，同 Word 解析）"""
    out = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if out and ((_LATIN_END.search(out) and _LATIN_START.match(part)) or (cell and out[-1] in _SENTENCE_STOP)):
            out += " "
        out += part
    return _clean_text(out)


def _is_bold(span: dict) -> bool:
    """粗体字形，或宋体等无粗体字形时 Word 用的「填充 + 描边」伪粗体（char_flags：BOLD=8、FILLED=16、STROKED=32）"""
    char_flags = span.get("char_flags", 0)
    return bool(span["flags"] & 16 or char_flags & 8 or (char_flags & 48) == 48
                or re.search(r"bold|black|heavy|-bol\b", span["font"], re.I))


def _key(text: str) -> str:
    """书签与正文比对用：只保留字母数字与汉字（书签常把「·」等符号换成空格）"""
    return "".join(ch for ch in text if ch.isalnum())


class _Line:
    __slots__ = ("page", "x0", "y0", "x1", "y1", "text", "size", "bold")

    def __init__(self, page, bbox, text, size, bold):
        self.page = page
        self.x0, self.y0, self.x1, self.y1 = bbox
        self.text = text
        self.size = size
        self.bold = bold


class _Paragraph:
    def __init__(self, line: _Line):
        self.lines = [line]

    @property
    def page(self) -> int:
        return self.lines[0].page

    @property
    def text(self) -> str:
        return _join([l.text for l in self.lines])

    @property
    def size(self) -> float:
        return max(l.size for l in self.lines)

    @property
    def bold(self) -> bool:
        return all(l.bold for l in self.lines)


class _Grid:
    """一张（可能跨页拼接的）表格：grid[r][c] 为覆盖该网格位置的单元格 id（全局唯一）"""

    def __init__(self, page: int, bbox, xs: List[float], grid: List[List[Optional[int]]]):
        self.page = page
        self.bbox = bbox
        self.xs = xs
        self.grid = grid
        self.texts: Dict[int, str] = {}
        # 单元格文字在所在页是否被分页截断：末行写满且写到了格子底部
        self.cut: Dict[int, bool] = {}
        self.bottom: Dict[int, bool] = {}  # 文字写到了格子底部（可能恰在段落边界处断开）
        self.strong: Dict[int, bool] = {}  # 多行写满、在句中被截断：所在行必然跨页
        self.first_on_page = False
        self.last_on_page = False


class PdfDocumentParser:
    """PDF → 与 Word 解析同构的章节树 / 表格 / 全文"""

    MIN_BOOKMARKS = 3
    HEADER_BAND = 0.11  # 页眉页脚判定带：页面上下各 11%
    WIDE_BAND = 0.15  # 多页原样重复的文字放宽到 15%
    MAX_HEADING_CHARS = WordDocumentParser.MAX_STYLED_HEADING_CHARS
    SENTENCE_END = WordDocumentParser.SENTENCE_END

    def __init__(self):
        self._word = WordDocumentParser()  # 复用编号体例、目录行与「标签：值」判定

    # ---------------- 入口 ----------------

    def parse_pdf(self, file_path) -> Dict[str, Any]:
        fitz = _load_pymupdf()
        try:
            doc = fitz.open(str(file_path))
        except Exception as e:
            raise PdfParseError(f"PDF 文件无法打开（文件可能已损坏）：{e}") from e
        try:
            if doc.needs_pass and not doc.authenticate(""):
                raise PdfParseError("PDF 已加密，需要打开密码；请提供未加密的 PDF 或 Word 版本")
            return self._parse(doc, fitz, str(file_path))
        finally:
            doc.close()

    def _parse(self, doc, fitz, doc_path: str) -> Dict[str, Any]:
        # 收集字形样式：宋体等无粗体字形时 Word 用「填充 + 描边」伪粗体，不收集就识别不出加粗标题
        flags = (fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES) | getattr(fitz, "TEXT_COLLECT_STYLES", 0)

        pages_lines: List[List[_Line]] = []
        pages_tables: List[List[_Grid]] = []
        page_sizes: List[Tuple[float, float]] = []
        image_only_pages: List[int] = []
        total_chars = bad_chars = 0
        cell_ids = itertools.count(1)  # 单元格编号：每次解析独立计数（解析器单例被多个后台线程共享）
        for page in doc:
            lines = self._page_lines(page, flags)
            chars = sum(len(l.text.replace(" ", "")) for l in lines)
            total_chars += chars
            bad_chars += sum(1 for l in lines for ch in l.text if "\ue000" <= ch <= "\uf8ff" or ch == "\ufffd")
            if chars < 20 and page.get_images():
                image_only_pages.append(page.number + 1)
            pages_lines.append(lines)
            pages_tables.append(self._page_tables(page, lines, cell_ids))
            page_sizes.append((page.rect.width, page.rect.height))

        page_count = len(pages_lines)
        text_pages = page_count - len(image_only_pages)
        if total_chars < 100 or (page_count >= 3 and text_pages < page_count * 0.3):
            raise PdfParseError(
                "未能从 PDF 中提取到足够的文字：该文件可能是扫描件或图片 PDF。"
                "系统暂不支持文字识别（OCR），请上传 Word 版本或可复制文字的 PDF"
            )
        if bad_chars > total_chars * 0.1:
            raise PdfParseError(
                "PDF 中的文字编码无法识别（字体缺少 Unicode 映射，提取出来是乱码）；请上传 Word 版本或重新导出的 PDF"
            )

        self._strip_page_furniture(pages_lines, page_sizes)
        pages_lines = [self._merge_same_row(lines) for lines in pages_lines]

        body_size, line_gaps, margins, right_edges = self._metrics(pages_lines, pages_tables, page_sizes)
        stream = self._ordered_stream(pages_lines, pages_tables)
        blocks = self._assemble(stream, margins, right_edges, line_gaps)
        tables = self._stitch_tables(blocks)

        toc = [(lvl, title, page) for lvl, title, page in doc.get_toc(simple=True) if title and title.strip()]
        toc_ids = self._toc_entries(blocks)
        headings = None
        heading_mode = "layout"
        bookmarks_ignored = False
        if len(toc) >= self.MIN_BOOKMARKS:
            marked = self._match_bookmarks(blocks, toc, toc_ids)
            if marked is not None and self._bookmarks_cover(blocks, marked, self._infer_headings(blocks, body_size, toc_ids)):
                headings, heading_mode = marked, "bookmarks"
            elif marked is not None:
                # 书签残缺（WPS 把正文条目生成了书签、各章没有书签）：书签匹配切分过段落，重新组段后按版面推断
                blocks = self._assemble(stream, margins, right_edges, line_gaps)
                toc_ids = self._toc_entries(blocks)
                bookmarks_ignored = True
        if headings is None:
            headings = self._infer_headings(blocks, body_size, toc_ids)

        builder = DocumentTreeBuilder()
        for block in blocks:
            if isinstance(block, _Grid):
                if block in tables:
                    rows, keys = self._grid_rows(block)
                    builder.add_table(rows, keys)
                continue
            text = block.text
            if not text or self._is_toc(text) or id(block) in toc_ids:
                continue
            if id(block) in headings:
                builder.add_heading(text, headings[id(block)])
            else:
                builder.add_paragraph(text)

        result = builder.result(doc_path, heading_mode)
        warnings = []
        if image_only_pages:
            pages = "、".join(str(p) for p in image_only_pages[:10]) + ("等" if len(image_only_pages) > 10 else "")
            warnings.append(f"第 {pages} 页为图片（可能是扫描页），其中内容未被识别，请人工核对")
        result.update({"source_format": "pdf", "page_count": page_count, "warnings": warnings,
                       "bookmarks_ignored": bookmarks_ignored})
        return result

    # ---------------- 文字行 ----------------

    @staticmethod
    def _page_lines(page, flags) -> List[_Line]:
        lines = []
        for block in page.get_text("dict", flags=flags)["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                spans = [s for s in line["spans"] if s["text"]]
                text = "".join(s["text"] for s in spans)
                if not text.strip():
                    continue
                sizes, bold_chars, chars = Counter(), 0, 0
                for s in spans:
                    n = len(s["text"].strip())
                    sizes[round(s["size"] * 2) / 2] += n
                    chars += n
                    if _is_bold(s):
                        bold_chars += n
                size = sizes.most_common(1)[0][0] if sizes else 0
                lines.append(_Line(page.number, tuple(line["bbox"]), text, size, chars > 0 and bold_chars * 2 >= chars))
        return lines

    def _strip_page_furniture(self, pages_lines: List[List[_Line]], page_sizes):
        """页眉页脚：上下边缘带内、在多页重复出现（数字归一后）的文字行，以及页码行"""
        def band_key(line: _Line, height: float, band: float = self.HEADER_BAND) -> Optional[str]:
            if line.y1 <= height * band or line.y0 >= height * (1 - band):
                return re.sub(r"\d+", "#", re.sub(r"\s+", "", line.text))
            return None

        counts: Counter = Counter()
        # 页眉位：带内同一纵坐标上的文字。随章节变化的页眉（「采购需求」「评标方法和评标标准」）单条重复不够多，
        # 但同一位置几乎每页都有、且只有少数几种文字；正文首行也每页同位置，但文字各不相同
        slot_pages: Counter = Counter()
        slot_texts: Dict[int, set] = {}
        for lines, (_, h) in zip(pages_lines, page_sizes):
            # 多行页眉（公司名 + 英文名 + 地址传真）会超出判定带，原样重复的文字放宽到 15%
            counts.update({k for k in (band_key(l, h, self.WIDE_BAND) for l in lines) if k})
            keys = {(k, round(l.y0)) for l in lines for k in [band_key(l, h)] if k}
            slot_pages.update({y for _, y in keys})
            for k, y in keys:
                slot_texts.setdefault(y, set()).add(k)
        threshold = max(2, len(pages_lines) * 0.3)

        def running_header(y: int) -> bool:
            near = range(y - 2, y + 3)
            pages = sum(slot_pages[v] for v in near)
            texts = set().union(*(slot_texts.get(v, set()) for v in near))
            return pages >= threshold and len(texts) * 4 <= pages

        for idx, (lines, (_, h)) in enumerate(zip(pages_lines, page_sizes)):
            kept = []
            for line in lines:
                key = band_key(line, h)
                wide = band_key(line, h, self.WIDE_BAND)
                if (wide is not None and len(pages_lines) >= 2 and counts[wide] >= threshold) or (
                        key is not None and (PAGE_NUMBER.match(line.text.strip())
                                             or (len(pages_lines) >= 4 and running_header(round(line.y0))))):
                    continue
                kept.append(line)
            pages_lines[idx] = kept

    @staticmethod
    def _merge_same_row(lines: List[_Line]) -> List[_Line]:
        """
        同一行上被拆开的文字合成一行：「联系人：xx   电话：xx」，以及行首单独排版的条款编号「1.2」——
        编号框比正文行矮，按上沿排序会落到正文之后，所以按行中心分组再从左到右拼接
        """
        def same_row(a: _Line, b: _Line) -> bool:
            overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
            return overlap > 0.6 * min(a.y1 - a.y0, b.y1 - b.y0)

        rows: List[List[_Line]] = []
        for line in sorted(lines, key=lambda l: ((l.y0 + l.y1) / 2, l.x0)):
            if rows and any(same_row(line, other) for other in rows[-1]):
                rows[-1].append(line)
            else:
                rows.append([line])
        merged: List[_Line] = []
        for row in rows:
            current = None
            for line in sorted(row, key=lambda l: l.x0):
                if current is not None and line.x0 >= current.x1 - 1:
                    gap = line.x0 - current.x1
                    current.text = current.text.rstrip() + (" " if gap > 0.5 * line.size else "") + line.text.lstrip()
                    current.x1, current.y0, current.y1 = line.x1, min(current.y0, line.y0), max(current.y1, line.y1)
                    current.size = max(current.size, line.size)
                    current.bold = current.bold and line.bold
                    continue
                current = line
                merged.append(line)
        merged.sort(key=lambda l: (l.y0, l.x0))
        return merged

    @staticmethod
    def _metrics(pages_lines: List[List[_Line]], pages_tables: List[List[_Grid]], page_sizes):
        """
        正文字号、各页正文行距（相邻行间空白的众数）、左边距（按页面尺寸分组，兼容横向页）与各页右边界。
        右边界取正文行尾成片聚集处的最右一处（两端对齐的满行都停在这里），表格内的行不参与
        """
        sizes: Counter = Counter()
        for lines in pages_lines:
            for l in lines:
                sizes[l.size] += len(l.text)
        body_size = sizes.most_common(1)[0][0] if sizes else 10.5

        def in_table(line: _Line, tables: List[_Grid]) -> bool:
            cx, cy = (line.x0 + line.x1) / 2, (line.y0 + line.y1) / 2
            return any(t.bbox[0] <= cx <= t.bbox[2] and t.bbox[1] <= cy <= t.bbox[3] for t in tables)

        groups: Dict[tuple, List[_Line]] = {}
        gaps: Counter = Counter()
        page_gaps: List[Counter] = []
        for lines, tables, (w, h) in zip(pages_lines, pages_tables, page_sizes):
            body = [l for l in lines if abs(l.size - body_size) <= 1 and not in_table(l, tables)]
            groups.setdefault((round(w), round(h)), []).extend(body)
            here: Counter = Counter()
            for a, b in zip(body, body[1:]):
                gap = b.y0 - a.y1
                if 0 <= gap <= 3 * body_size and b.x0 < a.x1:
                    here[round(gap)] += 1
            gaps.update(here)
            page_gaps.append(here)
        # 行距按页取众数（同一文件里各部分行距可能不同），样本太少的页用全文众数
        doc_gap = gaps.most_common(1)[0][0] if gaps else 0.0
        line_gaps = [here.most_common(1)[0][0] if sum(here.values()) >= 5 else doc_gap for here in page_gaps]

        def long_ends(lines: List[_Line], width: float) -> List[float]:
            return sorted(l.x1 for l in lines if len(l.text) >= 10 and l.x1 > width / 2)

        def right_edge(ends: List[float], min_count: float) -> Optional[float]:
            """行尾聚集处（宽一个字的窗口内至少 min_count 行）中最靠右的一处，取窗口内最右的行尾"""
            best, j = None, 0
            for i, end in enumerate(ends):
                while end - ends[j] > body_size:
                    j += 1
                if i - j + 1 >= min_count:
                    best = end
            return best

        margins, rights = {}, {}
        for key, lines in groups.items():
            xs = Counter(round(l.x0) for l in lines)
            frequent = [x for x, n in xs.items() if n >= max(3, len(lines) * 0.05)]
            margins[key] = min(frequent) if frequent else (min(xs) if xs else 0)
            ends = long_ends(lines, key[0])
            rights[key] = right_edge(ends, max(3, len(ends) * 0.2)) or (ends[-1] if ends else key[0])

        page_margin, page_right = [], []
        for lines, tables, (w, h) in zip(pages_lines, pages_tables, page_sizes):
            key = (round(w), round(h))
            page_margin.append(margins[key])
            # 同一文件里各部分版心宽度可能不同（WPS 分节）：行数够的页按本页算，明显偏窄的（多为短句列表）仍用全文值
            ends = long_ends([l for l in lines if abs(l.size - body_size) <= 1 and not in_table(l, tables)], w)
            edge = right_edge(ends, max(3, len(ends) * 0.25)) if len(ends) >= 6 else None
            page_right.append(edge if edge is not None and edge >= rights[key] - 3 * body_size else rights[key])
        return body_size, line_gaps, page_margin, page_right

    # ---------------- 表格 ----------------

    @staticmethod
    def _cluster(values: List[float], tol: float = 2.0) -> List[float]:
        groups: List[List[float]] = []
        for v in sorted(values):
            if groups and v - groups[-1][-1] <= tol:
                groups[-1].append(v)
            else:
                groups.append([v])
        return [sum(g) / len(g) for g in groups]

    @staticmethod
    def _nearest(bounds: List[float], v: float) -> int:
        return min(range(len(bounds)), key=lambda i: abs(bounds[i] - v))

    @staticmethod
    def _visible_drawings(page) -> Optional[List[dict]]:
        """
        矢量图形中去掉白色纯填充（无描边）的矩形：白纸上看不见，多为页面/段落底色；
        WPS 给表格旁的段落加白底时，表格识别会把它当成一行单元格，凭空多出行和列边界
        """
        try:
            drawings = page.get_drawings()
        except Exception:
            return None

        def white(color) -> bool:
            return color is not None and len(color) >= 3 and all(c >= 0.98 for c in color[:3])

        return [d for d in drawings if not (d.get("type") == "f" and white(d.get("fill")))]

    @staticmethod
    def _vertical_rules(drawings: List[dict]) -> List[Tuple[float, float, float]]:
        """页面上的竖线（x, y0, y1）：直线段、细长矩形，以及描边矩形的左右边"""
        rules = []
        for d in drawings:
            for item in d.get("items", []):
                if item[0] == "l":
                    p1, p2 = item[1], item[2]
                    if abs(p1.x - p2.x) < 1:
                        rules.append((p1.x, min(p1.y, p2.y), max(p1.y, p2.y)))
                elif item[0] == "re":
                    r = item[1]
                    if r.width < 2:
                        rules.append(((r.x0 + r.x1) / 2, r.y0, r.y1))
                    elif d.get("color") is not None:
                        rules.extend([(r.x0, r.y0, r.y1), (r.x1, r.y0, r.y1)])
        return rules

    def _page_tables(self, page, lines: List[_Line], cell_ids) -> List[_Grid]:
        drawings = self._visible_drawings(page)
        try:
            found = page.find_tables(paths=drawings).tables
        except Exception:
            return []
        rules = self._vertical_rules(drawings or []) if found else []
        grids = []
        for tb in found:
            cells = [c for c in tb.cells if c]
            if tb.col_count < 2 or not cells:
                continue  # 单列"表格"多为版面边框，按正文处理
            texts_by_bbox = {}
            extracted = tb.extract()
            for r, row in enumerate(tb.rows):
                for c, bbox in enumerate(row.cells):
                    if bbox is not None and r < len(extracted) and c < len(extracted[r]) and extracted[r][c] is not None:
                        texts_by_bbox[tuple(round(v, 1) for v in bbox)] = extracted[r][c]
            xs = self._cluster([c[0] for c in cells] + [c[2] for c in cells])
            ys = self._cluster([c[1] for c in cells] + [c[3] for c in cells])
            if len(xs) < 3 or len(ys) < 2:
                continue
            grid = _Grid(page.number, tuple(tb.bbox), xs, [[None] * (len(xs) - 1) for _ in range(len(ys) - 1)])
            for bbox in cells:
                c0, c1 = self._nearest(xs, bbox[0]), self._nearest(xs, bbox[2])
                r0, r1 = self._nearest(ys, bbox[1]), self._nearest(ys, bbox[3])
                if c1 <= c0 or r1 <= r0:
                    continue
                cid = next(cell_ids)
                raw = texts_by_bbox.get(tuple(round(v, 1) for v in bbox), "") or ""
                grid.texts[cid] = _join(raw.split("\n"), cell=True)
                inside = [l for l in lines
                          if bbox[0] <= (l.x0 + l.x1) / 2 <= bbox[2] and bbox[1] <= (l.y0 + l.y1) / 2 <= bbox[3]]
                tail = max(inside, key=lambda l: (l.y1, l.x1)) if inside else None
                if tail:
                    # 写满：多行时与格内最宽的行齐平；单行时按 Word 默认内边距（约 5.4pt）估计
                    others = [l.x1 for l in inside if l is not tail]
                    full_x = max(others) - 0.6 * tail.size if others else bbox[2] - 6 - 1.2 * tail.size
                    grid.bottom[cid] = bbox[3] - tail.y1 <= 1.6 * tail.size
                    grid.cut[cid] = grid.bottom[cid] and tail.x1 >= full_x
                    grid.strong[cid] = grid.cut[cid] and len(inside) >= 2
                else:
                    grid.cut[cid] = grid.bottom[cid] = grid.strong[cid] = False
                for r in range(r0, r1):
                    for c in range(c0, c1):
                        grid.grid[r][c] = cid
            self._drop_phantom_columns(grid, rules)
            grids.append(grid)
        return grids

    @staticmethod
    def _drop_phantom_columns(grid: _Grid, rules: List[Tuple[float, float, float]]):
        """
        去掉不属于表格的空列，否则跨页拼接时列边界对不上：
        - 窄于 8pt 的全空列（续页表格边框错位产生的窄缝）；
        - 最外侧的全空列且外侧没有竖线（页眉横线贴着表格上边框，被识别成表格的一部分，多出两条边列）
        """
        def empty(c: int) -> bool:
            return all(row[c] is None or not grid.texts.get(row[c], "").strip() for row in grid.grid)

        def ruled(x: float) -> bool:
            top, bottom = grid.bbox[1], grid.bbox[3]
            covered = sum(max(0.0, min(y1, bottom) - max(y0, top)) for rx, y0, y1 in rules if abs(rx - x) <= 2)
            return covered >= 0.5 * (bottom - top)

        def drop(c: int):
            for row in grid.grid:
                del row[c]
            del grid.xs[c if c == 0 else c + 1]

        while len(grid.xs) > 3 and empty(0) and not ruled(grid.xs[0]):
            drop(0)
        while len(grid.xs) > 3 and empty(len(grid.xs) - 2) and not ruled(grid.xs[-1]):
            drop(len(grid.xs) - 2)
        c = 0
        while c < len(grid.xs) - 1 and len(grid.xs) > 3:
            if grid.xs[c + 1] - grid.xs[c] < 8 and empty(c):
                drop(c)
            else:
                c += 1

    def _stitch_tables(self, blocks: List[Any]) -> List[_Grid]:
        """跨页断开的表格接上：前表是上一页最后一个元素、后表是本页第一个元素且列边界一致"""
        kept: List[_Grid] = []
        prev_block = None
        for block in blocks:
            if isinstance(block, _Grid) and isinstance(prev_block, _Grid) and kept and self._continues(kept[-1], block):
                self._append_rows(kept[-1], block)
            elif isinstance(block, _Grid):
                kept.append(block)
            prev_block = block
        return kept

    @staticmethod
    def _continues(prev: _Grid, cur: _Grid) -> bool:
        return (cur.page == prev.page + 1 and prev.last_on_page and cur.first_on_page
                and len(prev.xs) == len(cur.xs) and all(abs(a - b) <= 3 for a, b in zip(prev.xs, cur.xs)))

    @staticmethod
    def _row_texts(grid: _Grid, r: int) -> List[str]:
        return [grid.texts.get(cid, "") if cid is not None else "" for cid in grid.grid[r]]

    @staticmethod
    def _is_split_row(prev: _Grid, cur: _Grid, first: List[Optional[int]]) -> bool:
        """
        下一页首行是否为上一页末行被分页截断的后半部分（Word 默认允许跨页断行）：
        首行有文字的每一列，上一页同列单元格都被截断（末行写满且写到格子底部；同一行另有格子多行写满、
        在句中被截断时，写到格子底部即可——该格恰在段落边界处断开）；
        并且至少有一列在上一页已写完（首行该格为空）——各列都有新内容的是新的一行。
        若某列在上一页是写完的短内容（如「服务响应偏离（10分）」「0.25%」），首行也是新的一行。
        """
        last = prev.grid[-1]
        # 同一行有单元格多行写满、在句中被截断：整行跨页，写到格底的其他格是恰在段落边界处断开
        row_split = any(prev.strong.get(cid) for cid in set(last) if cid is not None)
        finished = False
        for c, cid in enumerate(first):
            if cid is None:
                continue
            above = last[c] if c < len(last) else None
            above_text = above is not None and bool(prev.texts.get(above, "").strip())
            if not cur.texts.get(cid, "").strip():
                finished = finished or above_text
                continue
            if not above_text or not (prev.cut.get(above) or (row_split and prev.bottom.get(above))):
                return False
        return finished

    def _append_rows(self, prev: _Grid, cur: _Grid):
        rows = cur.grid
        if rows and prev.grid and self._row_texts(cur, 0) == self._row_texts(prev, 0) and any(self._row_texts(cur, 0)):
            rows = rows[1:]  # Word「重复标题行」
        if rows and prev.grid:
            split = self._is_split_row(prev, cur, rows[0])
            alias: Dict[int, int] = {}
            last = prev.grid[-1]
            for c, cid in enumerate(rows[0]):
                above = last[c] if c < len(last) else None
                if cid is None or above is None or cid in alias:
                    continue
                tail = cur.texts.get(cid, "").strip()
                if split:
                    # 截断行：各格文字接到上一页同列单元格之后，编号沿用
                    if tail:
                        prev.texts[above] = _join([prev.texts.get(above, ""), tail], cell=True)
                elif tail or not prev.texts.get(above, "").strip():
                    continue  # 新行中有内容的格子是新单元格
                # 空的续格 = 跨页的纵向合并单元格；该格在新一页是否被截断随之更新
                alias[cid] = above
                prev.cut[above] = cur.cut.get(cid, False)
                prev.bottom[above] = cur.bottom.get(cid, False)
                prev.strong[above] = cur.strong.get(cid, False)
            rows = [[alias.get(cid, cid) for cid in row] for row in rows]
            if split and all(cid is None or cid in alias.values() for cid in rows[0]):
                rows = rows[1:]
        for cid, text in cur.texts.items():
            prev.texts.setdefault(cid, text)
        for state in ("cut", "bottom", "strong"):
            for cid, value in getattr(cur, state).items():
                getattr(prev, state).setdefault(cid, value)
        prev.grid.extend(rows)
        prev.page = cur.page
        prev.last_on_page = cur.last_on_page

    @staticmethod
    def _grid_rows(grid: _Grid) -> Tuple[List[List[str]], List[List[int]]]:
        """输出与 Word 解析一致：横向合并只保留一次，纵向合并各行重复文本且编号相同，空行丢弃"""
        renumber: Dict[Any, int] = {}
        rows, keys = [], []
        for r, row in enumerate(grid.grid):
            texts, row_keys, prev = [], [], object()
            for c, cid in enumerate(row):
                if cid is not None and cid == prev:
                    continue
                prev = cid
                texts.append(grid.texts.get(cid, "") if cid is not None else "")
                row_keys.append(renumber.setdefault(cid if cid is not None else ("empty", r, c), len(renumber)))
            if any(texts):
                rows.append(texts)
                keys.append(row_keys)
        return rows, keys

    # ---------------- 版面流与段落 ----------------

    @staticmethod
    def _ordered_stream(pages_lines: List[List[_Line]], pages_tables: List[List[_Grid]]) -> List[Any]:
        """每页按纵坐标排列正文行与表格（表格区域内的文字行归表格所有）"""
        stream: List[Any] = []
        for lines, tables in zip(pages_lines, pages_tables):
            items: List[Tuple[float, Any]] = []
            for line in lines:
                cx, cy = (line.x0 + line.x1) / 2, (line.y0 + line.y1) / 2
                if any(t.bbox[0] - 1 <= cx <= t.bbox[2] + 1 and t.bbox[1] - 1 <= cy <= t.bbox[3] + 1 for t in tables):
                    continue
                items.append((line.y0, line))
            items.extend((t.bbox[1], t) for t in tables)
            items.sort(key=lambda it: it[0])
            if items:
                if isinstance(items[0][1], _Grid):
                    items[0][1].first_on_page = True
                if isinstance(items[-1][1], _Grid):
                    items[-1][1].last_on_page = True
            stream.extend(item for _, item in items)
        return stream

    def _assemble(self, stream: List[Any], margins: List[float], rights: List[float],
                  line_gaps: Optional[List[float]] = None) -> List[Any]:
        blocks: List[Any] = []
        para: Optional[_Paragraph] = None
        for item in stream:
            if isinstance(item, _Grid):
                if para:
                    blocks.append(para)
                    para = None
                blocks.append(item)
                continue
            if para and self._continues_paragraph(para, item, margins, rights, line_gaps):
                para.lines.append(item)
            else:
                if para:
                    blocks.append(para)
                para = _Paragraph(item)
        if para:
            blocks.append(para)
        return blocks

    @staticmethod
    def _continues_paragraph(para: _Paragraph, line: _Line, margins, rights, line_gaps=None) -> bool:
        prev, first = para.lines[-1], para.lines[0]
        size = max(prev.size, line.size, 1)
        right = rights[prev.page]
        # 上一行没在句末标点处结束：句子没写完
        mid_sentence = prev.text.rstrip()[-1:] not in _CLAUSE_END
        starts_new = PARA_START.match(line.text.strip())
        prev_full = prev.x1 >= right - 1.5 * size
        if not prev_full and prev.x1 >= right - 5 * size and line.bold == prev.bold and not starts_new:
            before = para.lines[-2] if len(para.lines) >= 2 else None
            if mid_sentence and line.page in (prev.page, prev.page + 1):
                # 句子没写完的长行：WPS 按字符网格排版，满行右端参差一两个字，段落自带缩进时更窄；
                # 首行缩进的段落，下一行回到段落左边也说明首行已写满
                prev_full = prev.x1 >= right - 4 * size or (before is None and line.x0 < prev.x0 - 0.8 * size)
            elif before is not None:
                # 句末恰好写满一行（段落比版心窄）：与本段上一行右端对齐
                prev_full = prev.x1 >= right - 4 * size and abs(prev.x1 - before.x1) <= 0.5 * size
        if not prev_full or abs(line.size - prev.size) > 0.6 or TOC_LEADER.search(prev.text):
            return False
        if line.bold and not prev.bold and starts_new:
            return False  # 正文行之后以编号开头的加粗行：标题（段尾加粗的「（提供截图并盖章）」仍是续行）
        if line.page != prev.page:
            # 跨页：上一页末行写满且字形一致，本页首行顶格（不缩进）→ 同一段续写；
            # 首行缩进时，句子没写完或与上一续行同样缩进（悬挂缩进的条目）也是续写
            if line.page != prev.page + 1 or line.bold != prev.bold:
                return False
            if line.x0 <= margins[line.page] + 0.6 * size:
                return True
            return not starts_new and (mid_sentence or (prev is not first and abs(line.x0 - prev.x0) <= 0.8 * size))
        line_gap = line_gaps[line.page] if line_gaps else 0.0
        if line.y0 - prev.y1 > max(1.2 * size, line_gap + 0.8 * size):  # 行距大的页面（WPS 1.5/2 倍行距）按本页行距算
            return False
        if not mid_sentence:
            # 句末恰好写满一行：下一行以编号开头，或与缩进的首行同样缩进 → 新段落
            if starts_new:
                return False
            if prev is first and first.x0 > margins[first.page] + 0.8 * size and abs(line.x0 - first.x0) <= 0.8 * size:
                return False
        if prev is first or (mid_sentence and not starts_new):
            # 首行写满：下一行不论顶格（首行缩进）还是缩进（悬挂缩进）都是续行；句子没写完的满行同理
            return True
        # 续行之后出现缩进或回退 → 新段落
        return abs(line.x0 - prev.x0) <= 0.8 * size

    # ---------------- 标题 ----------------

    def _is_toc(self, text: str) -> bool:
        if TOC_LEADER.search(text):
            return True
        m = self._word.TOC_LINE.search(text)
        return bool(m) and self._word._regex_heading_level(text[:m.start()].strip()) is not None

    def _headingish(self, text: str) -> bool:
        return (2 <= len(text) <= self.MAX_HEADING_CHARS and text[-1] not in self.SENTENCE_END
                and not self._word.LABEL_VALUE.match(text))

    def _toc_entries(self, blocks: List[Any]) -> set:
        """
        目录页的段落 id（含「目录」标题本身）。没有页码和前导符的目录（只列章名）靠「后文再次出现」识别：
        「目录」之后的短段落，其文字在后文以段落开头的形式再次出现，直到目录循环回第一条或遇到不再出现的段落
        """
        paras = [b for b in blocks if isinstance(b, _Paragraph) and b.lines]
        if not paras:
            return set()
        last_page = paras[-1].page
        start = next((i for i, p in enumerate(paras)
                      if p.page <= max(3, last_page * 0.15) and len(p.lines) <= 2
                      and re.search(r"目录$|^目次$", _key(p.text)) and len(_key(p.text)) <= 10), None)
        if start is None:
            return set()
        keys = [_key(TOC_LEADER.sub("", re.sub(r"[\t ]+\d{1,4}\s*$", "", p.text))) for p in paras]
        first = next((i for i in range(start + 1, len(paras)) if keys[i]), None)
        if first is None:
            return set()
        # 正文再次出现第一条目录项：两者之间都是目录（个别条目与正文标题写法不同也不影响）
        end = next((i for i in range(first + 1, min(len(paras), first + 300)) if keys[i] == keys[first]), None)
        if end is not None and all(len(p.lines) <= 2 for p in paras[first:end]):
            return {id(p) for p in paras[start:end]}
        # 找不到回环：逐条确认在后文再次出现
        ids = {id(paras[start])}
        for i in range(first, len(paras)):
            k = keys[i]
            if len(paras[i].lines) > 2 or len(k) < 2 or not any(later.startswith(k) for later in keys[i + 1:]):
                break
            ids.add(id(paras[i]))
        return ids if len(ids) > 1 else set()

    def _bookmarks_cover(self, blocks: List[Any], marked: Dict[int, int], inferred: Dict[int, int]) -> bool:
        """书签是否覆盖了文档的章结构：版面上看得出的「第X章/第X部分」过半不在书签里，书签就不可信"""
        by_id = {id(b): b for b in blocks if isinstance(b, _Paragraph)}

        def parts(headings: Dict[int, int]) -> set:
            return {bid for bid in headings if bid in by_id and self._word._number_class(by_id[bid].text) == "part"}

        seen = parts(inferred)
        return len(seen) < 3 or len(seen - parts(marked)) * 2 <= len(seen)

    def _match_bookmarks(self, blocks: List[Any], toc: List[tuple], skip: set = frozenset()) -> Optional[Dict[int, int]]:
        """
        书签按文档顺序与段落比对（只比字母数字与汉字）。标题被拼进正文段（前后段落末行/首行恰好写满）时
        按行切出来；标题折成两段时合并。匹配不足（书签只是页码目录等）时返回 None 交由版面推断。
        返回 {id(段落): 层级}。
        """
        matched: Dict[int, int] = {}
        pos = 0
        for level, title, page in toc:
            tkey = _key(title)
            if not tkey:
                continue
            for j in range(pos, len(blocks)):
                block = blocks[j]
                if not isinstance(block, _Paragraph) or not block.lines or id(block) in skip:
                    continue  # 目录页的同名条目不算
                if block.page > page:  # 书签页码从 1 开始，block.page 从 0 开始：最多落到下一页
                    break
                if block.page < page - 2:
                    continue
                pkey = _key(block.text)
                heading = None
                if tkey in pkey:
                    heading = self._carve(blocks, j, tkey)
                elif pkey and tkey.startswith(pkey) and len(block.lines) <= 2:
                    heading = self._merge_wrapped(blocks, j, tkey)
                if heading is not None:
                    matched[id(heading)] = level
                    pos = blocks.index(heading) + 1
                    break
        if len(matched) < max(self.MIN_BOOKMARKS, len(toc) * 0.3):
            return None

        # 与 Word 标题样式同等对待：成句的、「标签：值」行按正文；编号体例层级归一
        by_id = {id(b): b for b in blocks if isinstance(b, _Paragraph)}
        texts = {bid: by_id[bid].text for bid in matched if bid in by_id}
        candidates = [(texts[bid], lvl) for bid, lvl in matched.items() if bid in texts and self._headingish(texts[bid])]
        class_levels = self._word._class_levels(candidates)
        return {
            bid: min(class_levels.get(self._word._number_class(texts[bid]), lvl), 6)
            for bid, lvl in matched.items() if bid in texts and self._headingish(texts[bid])
        }

    @staticmethod
    def _carve(blocks: List[Any], j: int, tkey: str) -> Optional[_Paragraph]:
        """在段落中找整行组成的标题，切成「前文 / 标题 / 后文」三段，返回标题段"""
        block = blocks[j]
        keys = [_key(l.text) for l in block.lines]
        for a in range(len(keys)):
            acc = ""
            for b in range(a, len(keys)):
                acc += keys[b]
                if acc == tkey:
                    if a == 0 and b == len(keys) - 1:
                        return block
                    parts = [block.lines[:a], block.lines[a:b + 1], block.lines[b + 1:]]
                    new_blocks = []
                    heading = None
                    for k, lines in enumerate(parts):
                        if lines:
                            para = _Paragraph(lines[0])
                            para.lines = lines
                            new_blocks.append(para)
                            if k == 1:
                                heading = para
                    blocks[j:j + 1] = new_blocks
                    return heading
                if len(acc) >= len(tkey):
                    break
        return None

    @staticmethod
    def _merge_wrapped(blocks: List[Any], j: int, tkey: str) -> Optional[_Paragraph]:
        """标题折行后被拆成相邻两三段：合并回一段"""
        acc, k = _key(blocks[j].text), j + 1
        while k < len(blocks) and k <= j + 2 and len(acc) < len(tkey):
            nxt = blocks[k]
            if not isinstance(nxt, _Paragraph) or not nxt.lines:
                return None
            acc += _key(nxt.text)
            k += 1
        if acc != tkey:
            return None
        head = blocks[j]
        for nxt in blocks[j + 1:k]:
            head.lines.extend(nxt.lines)
        del blocks[j + 1:k]
        return head

    def _infer_headings(self, blocks: List[Any], body_size: float, skip: set = frozenset()) -> Dict[int, int]:
        """
        无书签：加粗或放大字号的短段才可能是标题（正文里的「1.服务范围：……」不会被误判）。
        外观与正文相同的编号短行（Word 里只靠大纲级别标成标题）不升为标题：真实文件里同样外观的
        「1.运行期通信服务」多为正文小标签，升为标题会切断章节「※」标记对其下条款的传递（偏离表红线）。
        层级按编号体例出现的嵌套顺序推断（第X篇 → 一、 → （一） → 1. → （1）），
        字号更大的标题不会挂在字号更小的标题之下；无编号的放大字号标题按字号归类。
        """
        headings: Dict[int, int] = {}
        stack: List[Tuple[Any, float]] = []  # (编号体例或字号键, 字号)
        for block in blocks:
            if not isinstance(block, _Paragraph) or not block.lines or len(block.lines) > 2 or id(block) in skip:
                continue
            text = block.text
            if (not self._headingish(text) or COVER_DATE.match(text) or self._is_toc(text)
                    or _key(text) in ("目录", "目次")):
                continue
            enlarged = block.size >= body_size + 1
            if not (block.bold or enlarged):
                continue
            cls = self._word._number_class(text)
            if cls is None and not enlarged:
                continue  # 加粗但无编号、不放大的短行（表前说明、强调句）按正文
            key = cls or ("size", block.size)
            if cls == "part":
                stack = []
            while stack and stack[-1][1] + 0.5 < block.size:
                stack.pop()
            keys = [k for k, _ in stack]
            if key in keys:
                stack = stack[:keys.index(key)]
            stack.append((key, block.size))
            headings[id(block)] = min(len(stack), 6)
        return headings


pdf_parser = PdfDocumentParser()
