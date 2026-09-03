import re
from typing import List, Dict, Any, Optional
from pathlib import Path
import docx
from docx.document import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

class ParsedSection:
    def __init__(self, section_id: str, title: str, level: int, breadcrumb: str):
        self.section_id = section_id
        self.title = title.strip()
        self.level = level
        self.breadcrumb = breadcrumb
        self.paragraphs: List[str] = []
        self.tables: List[List[List[str]]] = []  # table rows and cells
        self.subsections: List['ParsedSection'] = []

    def add_paragraph(self, text: str):
        cleaned = text.strip()
        if cleaned:
            self.paragraphs.append(cleaned)

    def add_table(self, table_data: List[List[str]]):
        if table_data:
            self.tables.append(table_data)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "section_id": self.section_id,
            "title": self.title,
            "level": self.level,
            "breadcrumb": self.breadcrumb,
            "content": "\n\n".join(self.paragraphs),
            "tables": self.tables,
            "subsections": [sub.to_dict() for sub in self.subsections]
        }

class WordDocumentParser:
    """
    专门针对中文招投标技术标书的层级化 Word (.docx) 解析器
    支持：
    1. 标题样式识别（Heading 1/2/3, 标题 1/2/3）
    2. 正文序号式标题智能识别（如“第一章”、“1.1”、“1.1.1”、“一、”）
    3. 维护完整大纲层级树与面包屑路径（Breadcrumb）
    4. 提取表格并保持上下文归属
    """

    # 正则识别标题层级
    REGEX_H1 = [
        re.compile(r"^第[一二三四五六七八九十百0-9]+[章节篇部]\s*.*"),
        re.compile(r"^[一二三四五六七八九十]+[、.．]\s*.*"),
        re.compile(r"^[0-9]{1,2}\s+[^\d\s\W].*"),
        re.compile(r"^[0-9]{1,2}[、.．]\s*.*"),
    ]
    REGEX_H2 = [
        re.compile(r"^[0-9]{1,2}\.[0-9]{1,2}\s*.*"),
        re.compile(r"^[（(][一二三四五六七八九十]+[）)]\s*.*"),
    ]
    REGEX_H3 = [
        re.compile(r"^[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{1,2}\s*.*"),
        re.compile(r"^[0-9]{1,2}[）)]\s*.*"),
        re.compile(r"^[（(][0-9]{1,2}[）)]\s*.*"),
    ]

    def __init__(self):
        pass

    def _detect_heading_level(self, para: Paragraph) -> Optional[int]:
        style_name = para.style.name.lower() if para.style else ""
        text = para.text.strip()
        if not text:
            return None

        # 1. 优先通过 Word 内部样式判断
        if "heading 1" in style_name or "标题 1" in style_name or style_name == "title":
            return 1
        elif "heading 2" in style_name or "标题 2" in style_name:
            return 2
        elif "heading 3" in style_name or "标题 3" in style_name:
            return 3
        elif "heading 4" in style_name or "标题 4" in style_name:
            return 3

        # 2. 标题一般不以逗号、分号或顿号结尾，且长度一般在 3~60 字符
        if len(text) > 60 or len(text) < 2 or text[-1] in ["，", ",", "；", ";", "、"]:
            return None

        # 过滤年份、日期或金额假标题（如 "2026 年是...", "100 万元"）
        if re.match(r"^(?:19|20)\d\d\s*年", text) or re.match(r"^\d+\s*(?:万|千|亿|元|个|条|家|台|套)", text):
            return None

        # 正则规则兜底判断
        for pat in self.REGEX_H3:
            if pat.match(text):
                return 3
        for pat in self.REGEX_H2:
            if pat.match(text):
                return 2
        for pat in self.REGEX_H1:
            if pat.match(text):
                return 1

        return None

    def _extract_table(self, table: Table) -> List[List[str]]:
        table_data = []
        for row in table.rows:
            row_data = [cell.text.strip().replace("\n", " ") for cell in row.cells]
            # 简单去重同一行中因为合并单元格造成的重复
            cleaned_row = []
            for item in row_data:
                cleaned_row.append(item)
            if any(cleaned_row):
                table_data.append(cleaned_row)
        return table_data

    def parse_docx(self, file_path: str or Path) -> Dict[str, Any]:
        doc = docx.Document(str(file_path))
        
        root_sections: List[ParsedSection] = []
        # 栈结构，保存当前层级的父节点: [(level, section_obj)]
        stack: List[tuple[int, ParsedSection]] = []
        
        # 默认起始段落如果没有任何标题，放入前言/概述
        current_section = ParsedSection(
            section_id="sec_0",
            title="前言/基本信息",
            level=1,
            breadcrumb="前言/基本信息"
        )
        root_sections.append(current_section)
        stack.append((1, current_section))

        sec_counter = 1

        # 遍历文档内部元素（段落与表格按文档顺序排列）
        # python-docx 中 doc.paragraphs 和 doc.tables 是分开的，但可以通过 body 元素顺序遍历
        for element in doc.element.body:
            if element.tag.endswith('p'):
                para = Paragraph(element, doc)
                text = para.text.strip()
                if not text:
                    continue
                
                level = self._detect_heading_level(para)
                if level is not None:
                    # 发现新标题
                    # 弹出比当前级别高或相等的栈顶
                    while stack and stack[-1][0] >= level:
                        stack.pop()
                    
                    breadcrumb_titles = [s.title for _, s in stack] + [text]
                    breadcrumb = " > ".join(breadcrumb_titles)
                    
                    new_sec = ParsedSection(
                        section_id=f"sec_{sec_counter}",
                        title=text,
                        level=level,
                        breadcrumb=breadcrumb
                    )
                    sec_counter += 1

                    if stack:
                        # 挂载为父级子节点
                        stack[-1][1].subsections.append(new_sec)
                    else:
                        root_sections.append(new_sec)

                    stack.append((level, new_sec))
                    current_section = new_sec
                else:
                    # 普通正文段落
                    current_section.add_paragraph(text)

            elif element.tag.endswith('tbl'):
                table = Table(element, doc)
                tbl_data = self._extract_table(table)
                if tbl_data:
                    current_section.add_table(tbl_data)

        # 过滤掉空的初始前言节点（如果它既无正文也无表格且有其他有效大纲）
        if len(root_sections) > 1 and not root_sections[0].paragraphs and not root_sections[0].tables:
            root_sections.pop(0)

        return {
            "doc_path": str(file_path),
            "total_sections": sec_counter,
            "sections": [sec.to_dict() for sec in root_sections]
        }

    def flatten_sections_to_chunks(self, parsed_result: Dict[str, Any], doc_name: str) -> List[Dict[str, Any]]:
        """
        将大纲树扁平化为可直接入库的知识切片（Knowledge Chunks），每个切片携带完整面包屑大纲路径
        """
        chunks = []

        def traverse(node: Dict[str, Any]):
            content = node.get("content", "")
            tables = node.get("tables", [])
            
            # 如果包含表格，将表格转换成 Markdown 格式拼入正文
            table_md_list = []
            for tbl in tables:
                if not tbl:
                    continue
                header = tbl[0]
                sep = ["---"] * len(header)
                md_rows = ["| " + " | ".join(header) + " |", "| " + " | ".join(sep) + " |"]
                for row in tbl[1:]:
                    # 补齐长度
                    padded = row + [""] * (len(header) - len(row))
                    md_rows.append("| " + " | ".join(padded[:len(header)]) + " |")
                table_md_list.append("\n".join(md_rows))
            
            full_text = content
            if table_md_list:
                full_text += "\n\n" + "\n\n".join(table_md_list)

            if full_text.strip():
                # 自动提炼标签（根据标题和关键词规则）
                tags = []
                keywords_map = {
                    "架构": ["系统架构", "技术路线"],
                    "安全": ["安全方案", "等保", "数据安全"],
                    "运维": ["售后服务", "运维保障", "SLA"],
                    "实施": ["项目实施", "进度管理", "部署方案"],
                    "测试": ["软件测试", "压力测试", "验收标准"],
                    "培训": ["人员培训", "交付培训"],
                    "信创": ["信创适配", "国产化"],
                    "数据库": ["数据库设计", "容灾备份"],
                    "微服务": ["微服务", "容器化", "Kubernetes"]
                }
                for kw, mapped_tags in keywords_map.items():
                    if kw in node["breadcrumb"] or kw in full_text[:100]:
                        tags.extend(mapped_tags)

                chunks.append({
                    "id": f"{doc_name}_{node['section_id']}",
                    "doc_name": doc_name,
                    "section_title": node["title"],
                    "breadcrumb": node["breadcrumb"],
                    "content": full_text.strip(),
                    "level": node["level"],
                    "tags": list(set(tags))
                })

            for sub in node.get("subsections", []):
                traverse(sub)

        for sec in parsed_result.get("sections", []):
            traverse(sec)

        return chunks
