import os
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
import docx
from docx import Document
from docx.shared import Pt, Inches, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import parse_xml, OxmlElement
from docx.oxml.ns import nsdecls, qn

from app.core.config import settings
from app.models.schemas import OutlineNode, GlobalFacts

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

class DocxBidExporter:
    """
    融合中国政企信息化招投标规范的高保真 Word (.docx) 导出引擎：
    1. 可定制模板样式库（支持科技蓝、党政红、传统公文等预设）
    2. 全局事实自动注入封面（投标人名称、法定代表人、日期）
    3. Markdown 文本、原生带框表格转换与 Mermaid 架构图视化题注渲染
    4. 页眉页脚与标准化公文间距
    """

    def __init__(self):
        self.default_style = DocxStyleConfig()

    def _set_cell_background(self, cell, fill_hex="F2F2F2"):
        """设置单元格底纹颜色"""
        shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{fill_hex}"/>')
        cell._tc.get_or_add_tcPr().append(shading)

    def _set_table_borders(self, table):
        """设置标准的工程标书细边框"""
        tblPr = table._tbl.tblPr
        borders = parse_xml(
            f'<w:tblBorders {nsdecls("w")}>'
            f'<w:top w:val="single" w:sz="6" w:space="0" w:color="A6A6A6"/>'
            f'<w:bottom w:val="single" w:sz="6" w:space="0" w:color="A6A6A6"/>'
            f'<w:insideH w:val="single" w:sz="4" w:space="0" w:color="D9D9D9"/>'
            f'<w:insideV w:val="none"/>'
            f'<w:left w:val="none"/>'
            f'<w:right w:val="none"/>'
            f'</w:tblBorders>'
        )
        tblPr.append(borders)

    def _apply_styles(self, doc: Document, style_cfg: DocxStyleConfig):
        """配置文档页面布局与中文排版样式"""
        for section in doc.sections:
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
                header_p.runs[0].font.name = '楷体'
                header_p.runs[0].font.size = Pt(9)
                header_p.runs[0].font.color.rgb = RGBColor(128, 128, 128)

        normal_style = doc.styles['Normal']
        normal_style.font.name = style_cfg.primary_font
        normal_style.font.size = Pt(12)  # 小四号
        normal_style.font.color.rgb = RGBColor(30, 30, 30)

    def _add_cover_page(self, doc: Document, project_name: str, client_name: str, facts: GlobalFacts):
        """添加标准标书封面（融合全局事实）"""
        for _ in range(4):
            doc.add_paragraph()

        p_proj = doc.add_paragraph()
        p_proj.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r_proj = p_proj.add_run(project_name)
        r_proj.font.name = '黑体'
        r_proj.font.size = Pt(24)
        r_proj.bold = True

        p_sub = doc.add_paragraph()
        p_sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r_sub = p_sub.add_run("技 术 投 标 文 件")
        r_sub.font.name = '黑体'
        r_sub.font.size = Pt(30)
        r_sub.font.color.rgb = RGBColor(192, 0, 0)  # 标书红
        r_sub.bold = True

        for _ in range(5):
            doc.add_paragraph()

        # 底部信息栏（从 GlobalFacts 自动填充）
        info_items = [
            ("招 标 单 位 ：", client_name or "招标单位"),
            ("投 标 单 位 ：", facts.company_name or "投标供应商"),
            ("法定代表人  ：", facts.legal_rep or "法人"),
            ("统一信用代码：", facts.credit_code or "91110108MA00000000"),
            ("编 制 日 期 ：", "2026 年 09 月"),
        ]
        for label, val in info_items:
            p_info = doc.add_paragraph()
            p_info.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r_label = p_info.add_run(f"{label}  ")
            r_label.font.name = '黑体'
            r_label.font.size = Pt(13)
            r_label.bold = True
            
            r_val = p_info.add_run(f"{val}\n")
            r_val.font.name = '宋体'
            r_val.font.size = Pt(13)

        doc.add_page_break()

    def _render_mermaid_block(self, doc: Document, mermaid_code: str):
        """将 Mermaid 架构图渲染为带有规范框线和题注的高清插槽"""
        table = doc.add_table(rows=1, cols=1)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        cell = table.cell(0, 0)
        self._set_cell_background(cell, "F8F9FA")
        
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.first_line_indent = Pt(0)
        r_title = p.add_run("【系统架构 / 业务流程拓扑图 (Mermaid 绘图)】\n")
        r_title.font.name = '黑体'
        r_title.font.size = Pt(11)
        r_title.bold = True
        r_title.font.color.rgb = RGBColor(0, 51, 102)

        # 填入结构流代码预览
        clean_lines = [l.strip() for l in mermaid_code.strip().split("\n") if l.strip() and not l.startswith("```")]
        r_code = p.add_run("\n".join(clean_lines[:10]))
        r_code.font.name = 'Consolas'
        r_code.font.size = Pt(9.5)
        r_code.font.color.rgb = RGBColor(80, 80, 80)

        # 题注
        p_caption = doc.add_paragraph()
        p_caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_caption.paragraph_format.first_line_indent = Pt(0)
        p_caption.paragraph_format.space_after = Pt(8)
        r_cap = p_caption.add_run("图：系统逻辑架构与业务数据流向图")
        r_cap.font.name = '楷体'
        r_cap.font.size = Pt(10)
        r_cap.font.color.rgb = RGBColor(100, 100, 100)

    def _render_markdown_table(self, doc: Document, table_lines: List[str]):
        """渲染原生 Word 表格"""
        if len(table_lines) < 2:
            return
        rows_data = []
        for line in table_lines:
            line = line.strip()
            if not line or set(line.replace("|", "").replace(" ", "").replace("-", "")) == set():
                continue
            cells = [c.strip() for c in line.split("|")]
            if len(cells) > 2:
                rows_data.append(cells[1:-1])
        if not rows_data:
            return

        col_count = len(rows_data[0])
        table = doc.add_table(rows=len(rows_data), cols=col_count)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        self._set_table_borders(table)

        for r_idx, row in enumerate(rows_data):
            for c_idx, val in enumerate(row):
                if c_idx < col_count:
                    cell = table.cell(r_idx, c_idx)
                    cell.text = val
                    p = cell.paragraphs[0]
                    p.paragraph_format.line_spacing = 1.15
                    p.paragraph_format.first_line_indent = Pt(0)
                    if p.runs:
                        p.runs[0].font.name = '宋体'
                        p.runs[0].font.size = Pt(10.5)
                        if r_idx == 0:
                            p.runs[0].font.name = '黑体'
                            p.runs[0].bold = True
                            self._set_cell_background(cell, "EAECEF")
        doc.add_paragraph()

    def _render_content(self, doc: Document, text_content: str):
        if not text_content:
            return

        lines = text_content.strip().split("\n")
        in_table = False
        table_lines = []
        in_mermaid = False
        mermaid_lines = []

        for line in lines:
            trimmed = line.strip()
            if not trimmed:
                continue

            # 处理 Mermaid 代码块
            if trimmed.startswith("```mermaid"):
                in_mermaid = True
                mermaid_lines = []
                continue
            elif in_mermaid:
                if trimmed.startswith("```"):
                    in_mermaid = False
                    self._render_mermaid_block(doc, "\n".join(mermaid_lines))
                    mermaid_lines = []
                else:
                    mermaid_lines.append(trimmed)
                continue

            # 处理 Markdown 表格
            if trimmed.startswith("|") and trimmed.endswith("|"):
                in_table = True
                table_lines.append(trimmed)
                continue
            elif in_table:
                self._render_markdown_table(doc, table_lines)
                table_lines = []
                in_table = False

            # 处理 Markdown 标题
            if trimmed.startswith("### "):
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(6)
                p.paragraph_format.space_after = Pt(3)
                p.paragraph_format.first_line_indent = Pt(0)
                run = p.add_run(trimmed[4:].strip())
                run.font.name = '黑体'
                run.font.size = Pt(12)
                run.bold = True
            elif trimmed.startswith("## "):
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(8)
                p.paragraph_format.space_after = Pt(4)
                p.paragraph_format.first_line_indent = Pt(0)
                run = p.add_run(trimmed[3:].strip())
                run.font.name = '黑体'
                run.font.size = Pt(14)
                run.bold = True
            elif trimmed.startswith("- ") or trimmed.startswith("* "):
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Pt(24)
                p.paragraph_format.first_line_indent = Pt(0)
                p.paragraph_format.line_spacing = 1.3
                run_bullet = p.add_run("●  ")
                run_bullet.font.size = Pt(8)
                run_text = p.add_run(trimmed[2:].strip())
                run_text.font.name = '宋体'
                run_text.font.size = Pt(12)
            else:
                p = doc.add_paragraph()
                p.paragraph_format.first_line_indent = Pt(24)
                p.paragraph_format.line_spacing = 1.3
                p.paragraph_format.space_after = Pt(4)

                parts = re.split(r'(\*\*.*?\*\*)', trimmed)
                for part in parts:
                    if part.startswith('**') and part.endswith('**'):
                        r = p.add_run(part[2:-2])
                        r.font.name = '宋体'
                        r.font.size = Pt(12)
                        r.bold = True
                    else:
                        r = p.add_run(part)
                        r.font.name = '宋体'
                        r.font.size = Pt(12)

        if in_table and table_lines:
            self._render_markdown_table(doc, table_lines)
        if in_mermaid and mermaid_lines:
            self._render_mermaid_block(doc, "\n".join(mermaid_lines))

    def export_project_to_docx(
        self,
        project_name: str,
        client_name: str,
        outline: List[OutlineNode],
        facts: Optional[GlobalFacts] = None,
        style_cfg: Optional[DocxStyleConfig] = None,
        output_filename: Optional[str] = None
    ) -> Path:
        """导出整本技术标书为标准 Word 文档"""
        doc = Document()
        cfg = style_cfg or self.default_style
        facts_obj = facts or GlobalFacts()

        self._apply_styles(doc, cfg)
        self._add_cover_page(doc, project_name, client_name, facts_obj)

        def render_node(node: OutlineNode):
            p_head = doc.add_paragraph()
            p_head.paragraph_format.first_line_indent = Pt(0)

            if node.level == 1:
                p_head.paragraph_format.space_before = Pt(14)
                p_head.paragraph_format.space_after = Pt(8)
                run = p_head.add_run(node.title)
                run.font.name = cfg.heading_font
                run.font.size = Pt(16)
                run.bold = True
                run.font.color.rgb = RGBColor(*cfg.theme_rgb)
            elif node.level == 2:
                p_head.paragraph_format.space_before = Pt(10)
                p_head.paragraph_format.space_after = Pt(5)
                run = p_head.add_run(node.title)
                run.font.name = cfg.heading_font
                run.font.size = Pt(14)
                run.bold = True
            else:
                p_head.paragraph_format.space_before = Pt(6)
                p_head.paragraph_format.space_after = Pt(3)
                run = p_head.add_run(node.title)
                run.font.name = cfg.heading_font
                run.font.size = Pt(12)
                run.bold = True

            if node.content:
                self._render_content(doc, node.content)

            for child in node.children:
                render_node(child)

        for root_node in outline:
            render_node(root_node)

        safe_name = re.sub(r'[\\/*?:"<>|]', '_', project_name)
        out_name = output_filename or f"{safe_name}_技术标书.docx"
        out_path = settings.OUTPUT_DIR / out_name
        doc.save(str(out_path))
        return out_path

docx_exporter = DocxBidExporter()
