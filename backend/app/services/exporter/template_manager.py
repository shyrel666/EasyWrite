import json
from typing import List, Dict, Any, Optional
from app.core.config import settings
from app.services.exporter.docx_generator import DocxStyleConfig

class TemplateManager:
    """
    Word 模板管理服务（借鉴 YuduBid 独立 Word 模板管理体系）：
    1. 管理内置的政企标准模版（科技蓝、党政红、简约黑白）
    2. 支持用户动态创建、保存、编辑、删除自定义 Word 样式模板
    3. 支持配置页边距、主题色、正文字体、标题字体、页眉文字
    4. 本地持久化至 data/templates.json
    """

    DEFAULT_TEMPLATES = [
        {
            "id": "gov_standard",
            "name": "政企标准科技蓝模板",
            "primary_font": "宋体",
            "heading_font": "黑体",
            "theme_color": "#003366",
            "theme_rgb": [0, 51, 102],
            "margin_top": 2.54,
            "margin_bottom": 2.54,
            "margin_left": 3.0,
            "margin_right": 2.54,
            "header_text": "技术投标文件",
            "description": "国内政企与信息化招标最常用的标准商务科技蓝格式"
        },
        {
            "id": "gov_red",
            "name": "国家部委党政红模板",
            "primary_font": "仿宋",
            "heading_font": "黑体",
            "theme_color": "#C00000",
            "theme_rgb": [192, 0, 0],
            "margin_top": 2.54,
            "margin_bottom": 2.54,
            "margin_left": 3.0,
            "margin_right": 2.54,
            "header_text": "投标文件 - 技术方案",
            "description": "适合党政机关、公检法司、事业单位及国有特大型企业招标"
        },
        {
            "id": "minimalist",
            "name": "商务简约黑白经典模板",
            "primary_font": "宋体",
            "heading_font": "黑体",
            "theme_color": "#1F2328",
            "theme_rgb": [31, 35, 40],
            "margin_top": 2.54,
            "margin_bottom": 2.54,
            "margin_left": 2.8,
            "margin_right": 2.54,
            "header_text": "TECHNICAL PROPOSAL",
            "description": "简约清爽，适合外资、互联网企业及咨询采购项目"
        }
    ]

    def __init__(self):
        self.storage_file = settings.DATA_DIR / "templates.json"
        self.templates: Dict[str, Dict[str, Any]] = {}
        self._init_templates()

    def _init_templates(self):
        for t in self.DEFAULT_TEMPLATES:
            self.templates[t["id"]] = t

        if self.storage_file.exists():
            try:
                with open(self.storage_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    self.templates.update(saved)
            except Exception as e:
                print(f"[TemplateManager] 加载自定义模板失败: {e}")

    def _save(self):
        try:
            with open(self.storage_file, "w", encoding="utf-8") as f:
                json.dump(self.templates, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[TemplateManager] 保存模板失败: {e}")

    def list_templates(self) -> List[Dict[str, Any]]:
        return list(self.templates.values())

    def get_template(self, template_id: str) -> Optional[DocxStyleConfig]:
        t_data = self.templates.get(template_id) or self.templates.get("gov_standard")
        if not t_data:
            return DocxStyleConfig()

        rgb = tuple(t_data.get("theme_rgb", [0, 51, 102]))
        return DocxStyleConfig(
            template_name=t_data.get("name", "标准模板"),
            primary_font=t_data.get("primary_font", "宋体"),
            heading_font=t_data.get("heading_font", "黑体"),
            theme_rgb=rgb,
            margin_top=t_data.get("margin_top", 2.54),
            margin_bottom=t_data.get("margin_bottom", 2.54),
            margin_left=t_data.get("margin_left", 3.0),
            margin_right=t_data.get("margin_right", 2.54),
            header_text=t_data.get("header_text", "技术投标文件")
        )

    def create_or_update_template(self, template_data: Dict[str, Any]) -> Dict[str, Any]:
        t_id = template_data.get("id") or f"tpl_{len(self.templates) + 1}"
        template_data["id"] = t_id

        # 转换 HEX 为 RGB
        hex_col = template_data.get("theme_color", "#003366").lstrip("#")
        if len(hex_col) == 6:
            r = int(hex_col[0:2], 16)
            g = int(hex_col[2:4], 16)
            b = int(hex_col[4:6], 16)
            template_data["theme_rgb"] = [r, g, b]

        self.templates[t_id] = template_data
        self._save()
        return template_data

    def delete_template(self, template_id: str) -> bool:
        # 不允许删除默认核心模板
        default_ids = {"gov_standard", "gov_red", "minimalist"}
        if template_id in default_ids or template_id not in self.templates:
            return False
        del self.templates[template_id]
        self._save()
        return True

template_manager = TemplateManager()
