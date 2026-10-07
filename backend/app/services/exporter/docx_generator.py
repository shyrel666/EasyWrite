import io
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Cm, Emu, Pt, RGBColor
from markdown_it import MarkdownIt

from app.core.config import settings
from app.models.schemas import DeviationItem, GlobalFacts, OutlineNode
from app.services.exporter.diagram_renderer import diagram_renderer

# 与前端预览（markdown-it，breaks: true）同一套解析规则；不解析内联 HTML
_MD = MarkdownIt("commonmark", {"html": False, "breaks": True}).enable(["table", "strikethrough"])
_BOLD_FALLBACK = re.compile(r"(\*\*[^*\n]+?\*\*)")  # 「**「中文」**」这类 CommonMark 不认的加粗，按字面兜底
_SENTENCE_END = "。；;！!？?"
_EMU_PER_INCH = 914400


class DocxStyleConfig:
    """
    Word 导出模板配置对象（借鉴 YuduBid 独立 Word 模板管理体系）：
    支持可配置页边距、主题色、正文字体、三级大纲字号、页眉页脚与图表题注样式。
    """
    def __init__(
        self,
        template_name: str = "政企标准标书模板",
        primary_font: str = "宋体",
        heading_font: str = "黑体",
        theme_rgb: tuple = (0, 51, 102),  # 经典科技/政企深蓝
        margin_top: float = 2.54,
        margin_bottom: float = 2.54,
        margin_left: float = 3.0,
        margin_right: float = 2.54,
        header_text: str = "技术投标文件"
    ):
        self.template_name = template_name
        self.primary_font = primary_font
        self.heading_font = heading_font
        self.theme_rgb = theme_rgb
        self.margin_top = margin_top
        self.margin_bottom = margin_bottom
        self.margin_left = margin_left
        self.margin_right = margin_right
        self.header_text = header_text


# ---------------- 原生多级编号 ----------------

# 有序列表逐级：1. →（1）→ ① → 1)；无序列表沿用 Word 默认的 ● / o / ■ 符号
_ORDERED_LEVELS = [("decimal", "%{n}."), ("decimal", "（%{n}）"), ("decimalEnclosedCircle", "%{n}"), ("decimal", "%{n})")]
_BULLET_LEVELS = [(chr(0xF0B7), "Symbol"), ("o", "Courier New"), (chr(0xF0A7), "Wingdings")]
_LIST_INDENT = 480  # 编号起点与正文首行缩进对齐（两个中文字符，twips）
_LIST_HANG = 420


def _list_left(ilvl: int) -> int:
    """第 ilvl 级列表项文字的左缩进（twips）"""
    return _LIST_INDENT + _LIST_HANG * (ilvl + 1)


class _Numbering:
    """在 numbering.xml 中为每个列表块新建 abstractNum + num：每个列表各自从头编号"""

    def __init__(self, doc):
        self.root = doc.part.numbering_part.element
        abstract_ids = [int(el.get(qn("w:abstractNumId"))) for el in self.root.findall(qn("w:abstractNum"))]
        num_ids = [int(el.get(qn("w:numId"))) for el in self.root.findall(qn("w:num"))]
        self.next_abstract = max(abstract_ids, default=0) + 1
        self.next_num = max(num_ids, default=0) + 1

    def new_list(self, kinds: Dict[int, str], starts: Dict[int, int]) -> int:
        abstract_id, num_id = self.next_abstract, self.next_num
        self.next_abstract += 1
        self.next_num += 1
        levels = []
        kind = "bullet"
        for ilvl in range(9):
            kind = kinds.get(ilvl, kind)
            levels.append(self._level_xml(ilvl, kind, starts.get(ilvl, 1)))
        abstract = parse_xml(
            f'<w:abstractNum {nsdecls("w")} w:abstractNumId="{abstract_id}">'
            f'<w:multiLevelType w:val="multilevel"/>{"".join(levels)}</w:abstractNum>'
        )
        first_num = self.root.find(qn("w:num"))  # 架构要求 abstractNum 全部排在 num 之前
        if first_num is not None:
            first_num.addprevious(abstract)
        else:
            self.root.append(abstract)
        self.root.append(parse_xml(
            f'<w:num {nsdecls("w")} w:numId="{num_id}"><w:abstractNumId w:val="{abstract_id}"/></w:num>'
        ))
        return num_id

    @staticmethod
    def _level_xml(ilvl: int, kind: str, start: int) -> str:
        indent = f'<w:pPr><w:ind w:left="{_list_left(ilvl)}" w:hanging="{_LIST_HANG}"/></w:pPr>'
        if kind == "ordered":
            fmt, text = _ORDERED_LEVELS[min(ilvl, len(_ORDERED_LEVELS) - 1)]
            return (f'<w:lvl w:ilvl="{ilvl}"><w:start w:val="{start}"/><w:numFmt w:val="{fmt}"/>'
                    f'<w:lvlText w:val="{text.format(n=ilvl + 1)}"/><w:lvlJc w:val="left"/>{indent}</w:lvl>')
        char, font = _BULLET_LEVELS[ilvl % len(_BULLET_LEVELS)]
        return (f'<w:lvl w:ilvl="{ilvl}"><w:start w:val="1"/><w:numFmt w:val="bullet"/>'
                f'<w:lvlText w:val="{char}"/><w:lvlJc w:val="left"/>{indent}'
                f'<w:rPr><w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:hint="default"/></w:rPr></w:lvl>')


# ---------------- 导出上下文 ----------------

def _normalize_mermaid(code: str) -> str:
    """前后端对同一张图的代码做相同归一（逐行去空白、去空行），用作预渲染图片的匹配键"""
    return "\n".join(line.strip() for line in (code or "").strip().splitlines() if line.strip())


def _split_mermaid_title(code: str) -> Tuple[str, str]:
    """Mermaid 前置元数据 ---\\ntitle: xxx\\n--- 中的标题作图题，其余为图代码"""
    m = re.match(r"^\s*---\s*\n(.*?)\n\s*---\s*\n", code or "", re.S)
    if not m:
        return "", code
    title = re.search(r"^\s*title\s*:\s*(.+?)\s*$", m.group(1), re.M)
    return (title.group(1).strip("'\" ") if title else ""), code[m.end():]


class _ExportContext:
    """单次导出的状态（导出器是多线程共享的单例，不能把计数挂在实例上）"""

    def __init__(self, doc, cfg: DocxStyleConfig, diagrams: Optional[Dict[str, bytes]] = None):
        self.doc = doc
        self.cfg = cfg
        self.diagrams = {_normalize_mermaid(k): v for k, v in (diagrams or {}).items()}
        self.numbering = _Numbering(doc)
        self.figure_no = 0
        self.table_no = 0

    @property
    def text_width_emu(self) -> int:
        section = self.doc.sections[-1]
        return int(section.page_width - section.left_margin - section.right_margin)


class DocxBidExporter:
    """
    融合中国政企信息化招投标规范的高保真 Word (.docx) 导出引擎：
    1. 可定制模板样式库（支持科技蓝、党政红、传统公文等预设），A4 版面
    2. 全局事实自动注入封面（投标人名称、法定代表人、日期）
    3. 正文 Markdown 按 markdown-it 语法树渲染：正文里的小标题排到所属章节下一级（真实标题样式，
       不会与大纲章节平级或越级，Word 目录层次不乱）；编号/项目符号列表用 Word 原生多级编号；
       表格为原生表格（按内容分配列宽、表头跨页重复、加粗等内联格式生效）
    4. Mermaid 架构图优先使用前端用真实 Mermaid 渲染好的图片，图题按全文顺序编号
    5. 页眉页脚与标准化公文间距
    """

    def __init__(self):
        self.default_style = DocxStyleConfig()

    # ---------------- 基础样式工具 ----------------

    def _set_cell_background(self, cell, fill_hex="F2F2F2"):
        """设置单元格底纹颜色"""
        shading = parse_xml(f'<w:shd {nsdecls("w")} w:val="clear" w:color="auto" w:fill="{fill_hex}"/>')
        cell._tc.get_or_add_tcPr().append(shading)

    @staticmethod
    def _set_run_font(run, name: str, size: float, bold: bool = False, color=None):
        """统一设置字体（含中文 eastAsia 字体，否则中文字符不生效）"""
        run.font.name = name
        run.font.size = Pt(size)
        run.bold = bold
        if color is not None:
            run.font.color.rgb = color
        rPr = run._element.get_or_add_rPr()
        rFonts = rPr.find(qn("w:rFonts"))
        if rFonts is None:
            rFonts = OxmlElement("w:rFonts")
            rPr.append(rFonts)
        rFonts.set(qn("w:eastAsia"), name)

    def _add_toc(self, doc: Document):
        """插入 Word 原生目录域（打开文档后 F9 或右键更新域即可刷新页码）"""
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run("目    录")
        self._set_run_font(run, "黑体", 16, bold=True)

        p_field = doc.add_paragraph()
        fldChar_begin = OxmlElement("w:fldChar")
        fldChar_begin.set(qn("w:fldCharType"), "begin")
        instr = OxmlElement("w:instrText")
        instr.set(qn("xml:space"), "preserve")
        instr.text = r'TOC \o "1-3" \h \z \u'
        fldChar_sep = OxmlElement("w:fldChar")
        fldChar_sep.set(qn("w:fldCharType"), "separate")
        placeholder = OxmlElement("w:t")
        placeholder.text = "（目录将在 Word 中打开后自动更新：右键目录 → 更新域 → 更新整个目录）"
        fldChar_end = OxmlElement("w:fldChar")
        fldChar_end.set(qn("w:fldCharType"), "end")

        run_field = p_field.add_run()
        run_field._element.append(fldChar_begin)
        run_field._element.append(instr)
        run_field._element.append(fldChar_sep)
        run_field._element.append(placeholder)
        run_field._element.append(fldChar_end)

        doc.add_page_break()

    def _add_page_number_footer(self, section):
        """页脚插入"第 X 页 共 Y 页"页码域"""
        footer_p = section.footer.paragraphs[0]
        footer_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        footer_p.text = ""

        def _append_field(paragraph, instr_text: str):
            run = paragraph.add_run()
            fld_begin = OxmlElement("w:fldChar")
            fld_begin.set(qn("w:fldCharType"), "begin")
            instr = OxmlElement("w:instrText")
            instr.set(qn("xml:space"), "preserve")
            instr.text = instr_text
            fld_end = OxmlElement("w:fldChar")
            fld_end.set(qn("w:fldCharType"), "end")
            run._element.append(fld_begin)
            run._element.append(instr)
            run._element.append(fld_end)
            self._set_run_font(run, "宋体", 9, color=RGBColor(128, 128, 128))

        r1 = footer_p.add_run("第 ")
        self._set_run_font(r1, "宋体", 9, color=RGBColor(128, 128, 128))
        _append_field(footer_p, "PAGE")
        r2 = footer_p.add_run(" 页  共 ")
        self._set_run_font(r2, "宋体", 9, color=RGBColor(128, 128, 128))
        _append_field(footer_p, "NUMPAGES")
        r3 = footer_p.add_run(" 页")
        self._set_run_font(r3, "宋体", 9, color=RGBColor(128, 128, 128))

    def _set_table_borders(self, table, color: str = "595959"):
        """标书表格：全框细实线（偏离表、参数表评审时需逐格对照）"""
        tblPr = table._tbl.tblPr
        edges = "".join(
            f'<w:{edge} w:val="single" w:sz="4" w:space="0" w:color="{color}"/>'
            for edge in ("top", "left", "bottom", "right", "insideH", "insideV")
        )
        tblPr.append(parse_xml(f'<w:tblBorders {nsdecls("w")}>{edges}</w:tblBorders>'))

    def _apply_styles(self, doc: Document, style_cfg: DocxStyleConfig):
        """配置文档页面布局与中文排版样式"""
        for section in doc.sections:
            # python-docx 默认模板是 Letter 纸，国内标书统一 A4
            section.page_width = Cm(21.0)
            section.page_height = Cm(29.7)
            section.top_margin = Cm(style_cfg.margin_top)
            section.bottom_margin = Cm(style_cfg.margin_bottom)
            section.left_margin = Cm(style_cfg.margin_left)
            section.right_margin = Cm(style_cfg.margin_right)

            # 页眉设置
            header = section.header
            header_p = header.paragraphs[0]
            header_p.text = style_cfg.header_text
            header_p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            if header_p.runs:
                self._set_run_font(header_p.runs[0], "楷体", 9, color=RGBColor(128, 128, 128))

            # 页脚页码（第 X 页 共 Y 页）
            self._add_page_number_footer(section)

        normal_style = doc.styles['Normal']
        normal_style.font.name = style_cfg.primary_font
        normal_style.font.size = Pt(12)  # 小四号
        normal_style.font.color.rgb = RGBColor(30, 30, 30)
        # Normal 样式的 eastAsia 字体
        rPr = normal_style.element.get_or_add_rPr()
        rFonts = rPr.find(qn("w:rFonts"))
        if rFonts is None:
            rFonts = OxmlElement("w:rFonts")
            rPr.append(rFonts)
        rFonts.set(qn("w:eastAsia"), style_cfg.primary_font)

    def _add_cover_page(self, doc: Document, project_name: str, client_name: str, facts: GlobalFacts):
        """添加标准标书封面（融合全局事实）"""
        for _ in range(4):
            doc.add_paragraph()

        p_proj = doc.add_paragraph()
        p_proj.alignment = WD_ALIGN_PARAGRAPH.CENTER
        self._set_run_font(p_proj.add_run(project_name), "黑体", 24, bold=True)

        p_sub = doc.add_paragraph()
        p_sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
        self._set_run_font(p_sub.add_run("技 术 投 标 文 件"), "黑体", 30, bold=True, color=RGBColor(192, 0, 0))

        for _ in range(5):
            doc.add_paragraph()

        # 底部信息栏（从 GlobalFacts 自动填充；未配置的字段留待填写，不编造）
        now = datetime.now()
        info_items = [
            ("招 标 单 位 ：", client_name or "（待填写）"),
            ("投 标 单 位 ：", facts.company_name or "（待填写）"),
            ("法定代表人  ：", facts.legal_rep or "（待填写）"),
            ("统一信用代码：", facts.credit_code or "（待填写）"),
            ("编 制 日 期 ：", f"{now.year} 年 {now.month:02d} 月"),
        ]
        for label, val in info_items:
            p_info = doc.add_paragraph()
            p_info.alignment = WD_ALIGN_PARAGRAPH.CENTER
            self._set_run_font(p_info.add_run(f"{label}  "), "黑体", 13, bold=True)
            self._set_run_font(p_info.add_run(val), "宋体", 13)

        doc.add_page_break()

    # ---------------- 标题 ----------------

    def _add_heading(self, ctx: _ExportContext, text: str, level: int):
        """真实 Word 标题样式（目录域与导航窗格依赖大纲级别）；1-3 级字号与主题色沿用模板，更深层级用小四黑体"""
        level = max(1, level)
        cfg = ctx.cfg
        if level <= 9:
            p = ctx.doc.add_paragraph(style=f"Heading {level}")
        else:
            p = ctx.doc.add_paragraph()
        fmt = p.paragraph_format
        fmt.first_line_indent = Pt(0)
        fmt.keep_with_next = True
        size, color, before, after = {
            1: (16, RGBColor(*cfg.theme_rgb), 14, 8),
            2: (14, RGBColor(*cfg.theme_rgb), 10, 5),
            3: (12, None, 6, 3),
        }.get(level, (12, None, 5, 2))
        fmt.space_before, fmt.space_after = Pt(before), Pt(after)
        run = p.add_run(text)
        self._set_run_font(run, cfg.heading_font, size, bold=True, color=color or RGBColor(30, 30, 30))
        run.italic = False  # 默认模板的 4 级以下标题样式是斜体
        return p

    # ---------------- 内联格式 ----------------

    def _add_runs(self, ctx: _ExportContext, paragraph, children, size: float, bold: bool = False,
                  soft_break: str = "break"):
        """
        把 markdown-it 内联 token 写成 runs：加粗/斜体/删除线/行内代码；链接只保留文字；
        soft_break="break" 时软换行为行内换行，"split" 时返回新段落起点（由调用方处理）
        """
        state = {"strong": bold, "em": False, "s": False}
        font = ctx.cfg.primary_font

        def emit(text: str, code: bool = False):
            if not text:
                return
            parts = [text] if code else _BOLD_FALLBACK.split(text)
            for part in parts:
                if not part:
                    continue
                fallback_bold = not code and part.startswith("**") and part.endswith("**") and len(part) > 4
                run = paragraph.add_run(part[2:-2] if fallback_bold else part)
                self._set_run_font(run, "Consolas" if code else font, size - 1 if code else size,
                                   bold=state["strong"] or fallback_bold)
                run.italic = state["em"]
                if state["s"]:
                    run.font.strike = True

        for child in children or []:
            t = child.type
            if t == "text":
                emit(child.content)
            elif t == "code_inline":
                emit(child.content, code=True)
            elif t in ("strong_open", "strong_close"):
                state["strong"] = t == "strong_open" or bold
            elif t in ("em_open", "em_close"):
                state["em"] = t == "em_open"
            elif t in ("s_open", "s_close"):
                state["s"] = t == "s_open"
            elif t in ("softbreak", "hardbreak"):
                paragraph.add_run().add_break(WD_BREAK.LINE)
            elif t == "image":
                emit(f"[图片：{child.content or '未命名'}]")
            elif t == "html_inline":
                emit(re.sub(r"<br\s*/?>", "\n", child.content, flags=re.I))

    @staticmethod
    def _split_softbreaks(children) -> List[list]:
        """普通段落里的单换行：中文正文一行即一段（与预览 breaks: true 一致），拆成多个 Word 段落"""
        groups: List[list] = [[]]
        for child in children or []:
            if child.type == "softbreak":
                groups.append([])
            else:
                groups[-1].append(child)
        return [g for g in groups if any(c.type != "text" or c.content.strip() for c in g)]

    @staticmethod
    def _plain_text(children) -> str:
        return "".join(c.content for c in children or [] if c.type in ("text", "code_inline"))

    @classmethod
    def _is_bold_title(cls, children) -> bool:
        """整段加粗的短句（提示词约定的「**1.1.1 xxx**」小标题）"""
        meaningful = [c for c in children if not (c.type == "text" and not c.content.strip())]
        text = cls._plain_text(children).strip()
        if not text or len(text) > 40 or text[-1] in _SENTENCE_END:
            return False
        if meaningful and meaningful[0].type == "strong_open" and meaningful[-1].type == "strong_close":
            return sum(1 for c in meaningful if c.type == "strong_open") == 1
        return bool(re.fullmatch(r"\*\*[^*]+\*\*", text))

    # ---------------- 段落 / 列表 / 表格 / 图 ----------------

    def _body_paragraph(self, ctx: _ExportContext, children, quote: bool = False):
        for group in self._split_softbreaks(children):
            p = ctx.doc.add_paragraph()
            fmt = p.paragraph_format
            fmt.line_spacing = 1.3
            fmt.space_after = Pt(4)
            if self._is_bold_title(group):
                fmt.first_line_indent = Pt(0)
                fmt.keep_with_next = True
                fmt.space_before = Pt(4)
                self._add_runs(ctx, p, group, 12, bold=True)
                continue
            fmt.first_line_indent = Pt(24)
            if quote:
                fmt.left_indent = Pt(24)
            self._add_runs(ctx, p, group, 12)
            if quote:
                for run in p.runs:
                    run.font.color.rgb = RGBColor(90, 90, 90)

    def _list_paragraph(self, ctx: _ExportContext, children, num_id: int, ilvl: int, numbered: bool):
        p = ctx.doc.add_paragraph()
        fmt = p.paragraph_format
        fmt.line_spacing = 1.3
        fmt.space_after = Pt(2)
        if numbered:
            pPr = p._p.get_or_add_pPr()
            pPr.append(parse_xml(
                f'<w:numPr {nsdecls("w")}><w:ilvl w:val="{ilvl}"/><w:numId w:val="{num_id}"/></w:numPr>'
            ))
        else:  # 同一列表项的后续段落：与该项文字对齐、不带编号
            fmt.left_indent = Emu(_list_left(ilvl) * 635)
            fmt.first_line_indent = Pt(0)
        self._add_runs(ctx, p, children, 12)

    @staticmethod
    def _scan_list(tokens, start: int) -> Tuple[Dict[int, str], Dict[int, int]]:
        """预扫描一个顶层列表块：每一级是有序还是无序、有序列表的起始序号"""
        kinds: Dict[int, str] = {}
        starts: Dict[int, int] = {}
        depth = 0
        for tok in tokens[start:]:
            if tok.type in ("bullet_list_open", "ordered_list_open"):
                kinds.setdefault(depth, "ordered" if tok.type == "ordered_list_open" else "bullet")
                if tok.type == "ordered_list_open" and depth not in starts:
                    try:
                        starts[depth] = int(tok.attrGet("start") or 1)
                    except (TypeError, ValueError):
                        starts[depth] = 1
                depth += 1
            elif tok.type in ("bullet_list_close", "ordered_list_close"):
                depth -= 1
                if depth == 0:
                    break
        return kinds, starts

    @staticmethod
    def _display_width(text: str) -> float:
        """估算显示宽度（中文 1，西文约 0.55）"""
        return sum(1 if ord(ch) > 0x2E80 else 0.55 for ch in text)

    def _column_widths(self, rows, header_rows: int, n_cols: int, total: int, font_size: float) -> List[int]:
        """
        列宽：短列（序号、状态、数量等，内容不超过 8 个字）按内容给足、表头不折行；
        长列（条款、说明）按内容长度分剩余宽度。放不下时退回按内容比例分配。
        """
        char = int(font_size * 12700)  # 一个中文字符宽（EMU）
        pad = 140000  # 单元格左右内边距合计约 0.39cm
        natural, header = [], []
        for c in range(n_cols):
            cells = [self._display_width(self._plain_text(r[c][0])) for r in rows if c < len(r)]
            heads = [self._display_width(self._plain_text(r[c][0])) for r in rows[:header_rows] if c < len(r)]
            natural.append(max(cells, default=2))
            header.append(min(max(heads, default=0), 8))
        widths = [0] * n_cols
        short = [c for c in range(n_cols) if natural[c] <= 8]
        long_cols = [c for c in range(n_cols) if c not in short]
        for c in short:
            widths[c] = int((max(natural[c], header[c], 2) + 0.6) * char + pad)  # 留半个字余量，避免恰好折行
        rest = total - sum(widths)
        floor = [int(max(header[c], 4) * char + pad) for c in long_cols]
        if long_cols and rest >= sum(floor):
            weights = [min(natural[c], 60) for c in long_cols]
            for c, w, f in zip(long_cols, weights, floor):
                widths[c] = max(f, int(rest * w / sum(weights)))
            scale = total / sum(widths)
        elif not long_cols:
            scale = total / sum(widths)  # 全是短列：等比放大铺满版心
        else:
            weights = [min(max(n, 2), 60) for n in natural]
            return [int(total * w / sum(weights)) for w in weights]
        return [int(w * scale) for w in widths]

    def _add_table(self, ctx: _ExportContext, rows: List[List[Tuple[list, str]]], header_rows: int,
                   font_size: float = 10.5):
        """
        原生 Word 表格：rows[r][c] = (内联 token, 对齐方式)。按内容估算列宽（短列不会被均分撑宽），
        表头跨页重复，全框细线。
        """
        if not rows:
            return None
        n_cols = max(len(r) for r in rows)
        table = ctx.doc.add_table(rows=len(rows), cols=n_cols)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False
        self._set_table_borders(table)

        col_widths = self._column_widths(rows, header_rows, n_cols, ctx.text_width_emu, font_size)

        tblPr = table._tbl.tblPr
        tblPr.append(parse_xml(f'<w:tblLayout {nsdecls("w")} w:type="fixed"/>'))
        for c, col in enumerate(table.columns):
            col.width = Emu(col_widths[c])

        align_map = {"center": WD_ALIGN_PARAGRAPH.CENTER, "right": WD_ALIGN_PARAGRAPH.RIGHT}
        for r_idx, row in enumerate(rows):
            tr = table.rows[r_idx]
            if r_idx < header_rows:
                trPr = tr._tr.get_or_add_trPr()
                trPr.append(parse_xml(f'<w:tblHeader {nsdecls("w")}/>'))  # 表头跨页重复
            for c in range(n_cols):
                cell = tr.cells[c]
                cell.width = Emu(col_widths[c])
                cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                children, align = row[c] if c < len(row) else ([], "")
                p = cell.paragraphs[0]
                p.paragraph_format.first_line_indent = Pt(0)
                p.paragraph_format.line_spacing = 1.15
                p.paragraph_format.space_after = Pt(0)
                if r_idx < header_rows:
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    self._set_cell_background(cell, "E7ECF2")
                    self._add_runs(ctx, p, children, font_size, bold=True)
                else:
                    if align in align_map:
                        p.alignment = align_map[align]
                    self._add_runs(ctx, p, children, font_size)
        ctx.doc.add_paragraph().paragraph_format.space_after = Pt(2)
        return table

    def _markdown_table(self, ctx: _ExportContext, tokens) -> None:
        rows: List[List[Tuple[list, str]]] = []
        header_rows = 0
        in_head = False
        for i, tok in enumerate(tokens):
            if tok.type == "thead_open":
                in_head = True
            elif tok.type == "thead_close":
                in_head = False
            elif tok.type == "tr_open":
                rows.append([])
                if in_head:
                    header_rows += 1
            elif tok.type in ("th_open", "td_open"):
                style = tok.attrGet("style") or ""
                align = re.search(r"text-align:\s*(\w+)", style)
                inline = tokens[i + 1] if i + 1 < len(tokens) and tokens[i + 1].type == "inline" else None
                rows[-1].append((inline.children if inline else [], align.group(1) if align else ""))
        self._add_table(ctx, rows, header_rows)

    def _code_block(self, ctx: _ExportContext, code: str):
        p = ctx.doc.add_paragraph()
        p.paragraph_format.left_indent = Pt(18)
        p.paragraph_format.first_line_indent = Pt(0)
        lines = code.rstrip("\n").split("\n")
        for i, line in enumerate(lines):
            run = p.add_run(line)
            self._set_run_font(run, "Consolas", 9.5, color=RGBColor(60, 60, 60))
            if i < len(lines) - 1:
                run.add_break(WD_BREAK.LINE)

    def _add_caption(self, ctx: _ExportContext, text: str):
        p = ctx.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.first_line_indent = Pt(0)
        p.paragraph_format.space_after = Pt(8)
        self._set_run_font(p.add_run(text), "楷体", 10.5, color=RGBColor(80, 80, 80))

    def _picture_size(self, ctx: _ExportContext, image: bytes) -> Tuple[int, Optional[int]]:
        """按图片像素尺寸缩放（前端以 2 倍像素渲染）：不超过版心宽度与 2/3 页高，小图不放大"""
        try:
            from PIL import Image
            with Image.open(io.BytesIO(image)) as im:
                px_w, px_h = im.size
        except Exception:
            return ctx.text_width_emu, None
        width = px_w / 192 * _EMU_PER_INCH
        height = px_h / 192 * _EMU_PER_INCH
        section = ctx.doc.sections[-1]
        max_h = (section.page_height - section.top_margin - section.bottom_margin) * 2 / 3
        scale = min(1.0, ctx.text_width_emu / width, max_h / height)
        return int(width * scale), int(height * scale)

    def _render_mermaid_block(self, ctx: _ExportContext, mermaid_code: str, section_title: str = ""):
        """
        架构图：优先用前端真实 Mermaid 渲染的图片；没有时用本地简化渲染（仅流程图）；
        仍不行则放代码插槽，绝不画与代码无关的图。图题按全文顺序编号。
        """
        title, body = _split_mermaid_title(mermaid_code)
        # 图题：元数据 title 优先，否则用所属章节标题（去掉「第一章」「1.1」等编号）
        section_name = re.sub(r"^(?:第[一二三四五六七八九十百\d]+[章节篇部]|\d+(?:\.\d+)*[.、]?)\s*", "", section_title or "")
        caption_title = title or section_name or "系统架构图"
        image = ctx.diagrams.get(_normalize_mermaid(mermaid_code)) or ctx.diagrams.get(_normalize_mermaid(body))
        if image is None:
            path = diagram_renderer.render_to_image(body, diagram_title="")
            if path and path.exists():
                image = path.read_bytes()
        ctx.figure_no += 1
        caption = f"图 {ctx.figure_no}  {caption_title}"
        if image:
            p_img = ctx.doc.add_paragraph()
            p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p_img.paragraph_format.first_line_indent = Pt(0)
            p_img.paragraph_format.space_before = Pt(8)
            p_img.paragraph_format.space_after = Pt(2)
            p_img.paragraph_format.keep_with_next = True
            width, height = self._picture_size(ctx, image)
            p_img.add_run().add_picture(io.BytesIO(image), width=Emu(width), height=Emu(height) if height else None)
            self._add_caption(ctx, caption)
            return

        # 降级：带底纹的代码插槽，提示在 Word 中替换为正式图
        table = ctx.doc.add_table(rows=1, cols=1)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        cell = table.cell(0, 0)
        self._set_cell_background(cell, "F8F9FA")
        p = cell.paragraphs[0]
        p.paragraph_format.first_line_indent = Pt(0)
        self._set_run_font(p.add_run("【架构图未能自动渲染，请在此处插入正式图片】"), "黑体", 11, bold=True,
                           color=RGBColor(*ctx.cfg.theme_rgb))
        p.add_run().add_break(WD_BREAK.LINE)
        clean = [line.strip() for line in body.strip().split("\n") if line.strip()]
        self._set_run_font(p.add_run("\n".join(clean[:12])), "Consolas", 9, color=RGBColor(80, 80, 80))
        self._add_caption(ctx, caption)

    # ---------------- Markdown 正文 ----------------

    def _render_content(self, ctx: _ExportContext, text_content: str, base_level: int = 1, section_title: str = ""):
        """
        正文 Markdown → Word。正文里的 # 标题按相对深度排到所属章节之下（最浅的一级 = base_level + 1），
        不会与大纲章节平级或越级；整段加粗短句作为不进目录的小标题。
        """
        if not text_content or not text_content.strip():
            return
        tokens = _MD.parse(text_content)
        heading_levels = [int(t.tag[1]) for t in tokens if t.type == "heading_open"]
        top = min(heading_levels, default=1)

        lists: List[Tuple[int, int]] = []  # 列表栈：(numId, 层级)
        pending_number = False  # 当前列表项的第一段带编号，后续段落只缩进
        quote = 0
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            t = tok.type
            if t == "heading_open":
                inline = tokens[i + 1]
                level = base_level + int(tok.tag[1]) - top + 1
                self._add_heading(ctx, self._plain_text(inline.children).strip().strip("*"), level)
                i += 3
                continue
            if t in ("bullet_list_open", "ordered_list_open"):
                if not lists:
                    kinds, starts = self._scan_list(tokens, i)
                    num_id = ctx.numbering.new_list(kinds, starts)
                else:
                    num_id = lists[-1][0]
                lists.append((num_id, len(lists)))
            elif t in ("bullet_list_close", "ordered_list_close"):
                lists.pop()
            elif t == "list_item_open":
                pending_number = True
            elif t == "paragraph_open":
                inline = tokens[i + 1]
                if lists:
                    num_id, ilvl = lists[-1]
                    self._list_paragraph(ctx, inline.children, num_id, ilvl, pending_number)
                    pending_number = False
                else:
                    self._body_paragraph(ctx, inline.children, quote=quote > 0)
                i += 3
                continue
            elif t == "table_open":
                end = i
                while end < len(tokens) and tokens[end].type != "table_close":
                    end += 1
                self._markdown_table(ctx, tokens[i:end + 1])
                i = end + 1
                continue
            elif t == "fence":
                if (tok.info or "").strip().lower().startswith("mermaid"):
                    self._render_mermaid_block(ctx, tok.content, section_title)
                else:
                    self._code_block(ctx, tok.content)
            elif t == "code_block":
                self._code_block(ctx, tok.content)
            elif t == "blockquote_open":
                quote += 1
            elif t == "blockquote_close":
                quote -= 1
            elif t == "html_block":
                p = ctx.doc.add_paragraph()
                p.paragraph_format.first_line_indent = Pt(24)
                self._set_run_font(p.add_run(re.sub(r"<[^>]+>", "", tok.content).strip()), ctx.cfg.primary_font, 12)
            i += 1

    # ---------------- 整本导出 ----------------

    def _new_document(self, cfg: DocxStyleConfig):
        doc = Document()
        self._apply_styles(doc, cfg)
        return doc

    def export_project_to_docx(
        self,
        project_name: str,
        client_name: str,
        outline: List[OutlineNode],
        facts: Optional[GlobalFacts] = None,
        style_cfg: Optional[DocxStyleConfig] = None,
        output_filename: Optional[str] = None,
        diagrams: Optional[Dict[str, bytes]] = None,
    ) -> Path:
        """
        导出整本技术标书为标准 Word 文档。
        diagrams：前端用真实 Mermaid 渲染好的图片 {mermaid 代码: PNG 字节}，按归一化代码匹配。
        """
        cfg = style_cfg or self.default_style
        doc = self._new_document(cfg)
        ctx = _ExportContext(doc, cfg, diagrams)
        self._add_cover_page(doc, project_name, client_name, facts or GlobalFacts())
        self._add_toc(doc)

        def render_node(node: OutlineNode):
            self._add_heading(ctx, node.title, node.level)
            if node.content:
                self._render_content(ctx, node.content, base_level=node.level, section_title=node.title)
            for child in node.children:
                render_node(child)

        for root_node in outline:
            render_node(root_node)

        safe_name = re.sub(r'[\\/*?:"<>|]', '_', project_name)
        out_path = settings.OUTPUT_DIR / (output_filename or f"{safe_name}_技术标书.docx")
        doc.save(str(out_path))
        return out_path

    def export_deviation_table(
        self,
        project_name: str,
        items: List[DeviationItem],
        style_cfg: Optional[DocxStyleConfig] = None,
        output_filename: Optional[str] = None,
    ) -> Path:
        """
        单独导出技术偏离表（Word 原生表格）：逐条列出招标要求、条款属性、响应结果与说明，
        废标红线条款加粗，「待生成」条款原样保留以便核对。
        """
        from app.services.parser.deviation_engine import LEVEL_LABELS

        cfg = style_cfg or self.default_style
        doc = self._new_document(cfg)
        ctx = _ExportContext(doc, cfg)

        title = doc.add_paragraph()
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        self._set_run_font(title.add_run("技术偏离表"), cfg.heading_font, 16, bold=True)
        info = doc.add_paragraph()
        info.paragraph_format.first_line_indent = Pt(0)
        self._set_run_font(info.add_run(f"项目名称：{project_name}"), cfg.primary_font, 12)

        def cell(text: str, bold: bool = False):
            # 复用表格渲染：构造与 markdown-it 内联 token 同形的最小对象（文本不经 Markdown 解析）
            tokens = [_Tok("text", text)]
            return ([_Tok("strong_open"), *tokens, _Tok("strong_close")] if bold else tokens), ""

        rows = [[cell(h) for h in ("序号", "招标文件条款要求", "条款属性", "响应结果", "响应说明")]]
        for it in items:
            clause = it.clause_title if not it.section else f"{it.clause_title}\n（{it.section}）"
            redline = it.level == "redline"
            rows.append([
                (cell(str(it.index))[0], "center"),
                cell(clause, bold=redline),
                (cell(LEVEL_LABELS.get(it.level, "一般条款"), bold=redline)[0], "center"),
                (cell(it.response_status)[0], "center"),
                cell(it.response_detail),
            ])
        self._add_table(ctx, rows, header_rows=1)

        negative = sum(1 for it in items if it.response_status == "负偏离")
        pending = sum(1 for it in items if it.response_status == "待生成")
        note = doc.add_paragraph()
        note.paragraph_format.first_line_indent = Pt(0)
        summary = f"共 {len(items)} 条"
        if negative:
            summary += f"，其中负偏离 {negative} 条"
        if pending:
            summary += f"，尚有 {pending} 条未填写响应"
        self._set_run_font(note.add_run(summary + "。"), cfg.primary_font, 10.5, color=RGBColor(90, 90, 90))

        safe_name = re.sub(r'[\\/*?:"<>|]', '_', project_name)
        out_path = settings.OUTPUT_DIR / (output_filename or f"{safe_name}_技术偏离表.docx")
        doc.save(str(out_path))
        return out_path


class _Tok:
    """与 markdown-it 内联 token 同形的最小节点（偏离表单元格直接写入，不经 Markdown 解析）"""

    def __init__(self, type_: str, content: str = ""):
        self.type = type_
        self.content = content


docx_exporter = DocxBidExporter()
