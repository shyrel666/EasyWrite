import re
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Tuple
import matplotlib
matplotlib.use('Agg')  # 无头模式
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from app.core.config import settings

# 中文字体兼容配置
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'SimSun', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

class DiagramRenderer:
    """
    标书架构图高保真离线渲染引擎（融合 YuduBid 架构图渲染要求）：
    1. 解析 Mermaid 流程图与架构拓扑代码 (graph TD / graph LR)
    2. 自动分层与拓扑节点排布
    3. 基于 Matplotlib 渲染高清晰度矢量质感图（抗锯齿、带框阴影、公文专业色系）
    4. 导出为高分辨率 300 DPI PNG 图片以供 Word 嵌入
    """

    THEME_COLORS = [
        {"box": "#EBF3FE", "border": "#2563EB", "text": "#1E3A8A"},  # 科技蓝 (应用/网关)
        {"box": "#F0FDF4", "border": "#16A34A", "text": "#14532D"},  # 翡翠绿 (服务/微服务)
        {"box": "#FEF3C7", "border": "#D97706", "text": "#78350F"},  # 典雅金 (数据/DB)
        {"box": "#F8FAFC", "border": "#64748B", "text": "#0F172A"},  # 玄青灰 (用户/终端)
        {"box": "#FEE2E2", "border": "#DC2626", "text": "#7F1D1D"},  # 警戒红 (安全/等保)
    ]

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or settings.DATA_DIR / "rendered_diagrams"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _parse_mermaid(self, mermaid_code: str) -> Tuple[Dict[str, str], List[Tuple[str, str, str]]]:
        """
        解析简化的 Mermaid 拓扑：
        提取节点: node_id -> label
        提取边: (from_id, to_id, label)
        """
        nodes: Dict[str, str] = {}
        edges: List[Tuple[str, str, str]] = []

        lines = [l.strip() for l in mermaid_code.split("\n") if l.strip() and not l.strip().startswith("%%")]
        # 只支持流程图（graph / flowchart）；时序图、甘特图等其他类型交给前端真实 Mermaid 渲染
        if not lines or not re.match(r"^(graph|flowchart)\b", lines[0]):
            return nodes, edges
        for line in lines:
            if line.startswith("graph") or line.startswith("flowchart") or line.startswith("```"):
                continue

            # 匹配带连线的模式 A[User] --> B[Gateway] 或 A --> B
            # 先找连线符号
            if "-->" in line or "---" in line:
                parts = re.split(r'-->|---', line)
                if len(parts) >= 2:
                    left = parts[0].strip()
                    right = parts[1].strip()

                    # 提取左节点定义
                    left_id, left_label = self._extract_node_info(left)
                    if left_id:
                        nodes[left_id] = left_label or nodes.get(left_id) or left_id

                    # 提取右节点定义
                    right_id, right_label = self._extract_node_info(right)
                    if right_id:
                        nodes[right_id] = right_label or nodes.get(right_id) or right_id

                    if left_id and right_id:
                        edges.append((left_id, right_id, ""))
            elif not re.match(r"^(subgraph|end|style|classDef|class|linkStyle|click|direction)\b", line):
                # 独立单节点定义（跳过子图、样式等指令行）
                nid, nlabel = self._extract_node_info(line)
                if nid:
                    nodes[nid] = nlabel or nodes.get(nid) or nid

        # 解析不出节点时返回空，由调用方降级为代码插槽——绝不画一张与正文无关的"缺省架构图"
        return nodes, edges

    def _extract_node_info(self, text: str) -> Tuple[str, str]:
        text = text.strip()
        # 匹配 node_id[Label] 或 node_id[(Label)]
        m = re.search(r'([A-Za-z0-9_]+)[\(\[\{]+(.*?)[\)\]\}]+', text)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        # 纯 ID
        m_id = re.match(r'^[A-Za-z0-9_]+$', text)
        if m_id:
            return m_id.group(0), ""  # 只引用节点 ID：不能用 ID 覆盖此前定义的中文标签
        return "", ""

    def render_to_image(self, mermaid_code: str, diagram_title: str = "系统总体技术逻辑架构与数据流向图") -> Optional[Path]:
        """
        将 Mermaid 代码渲染为一张高保真技术拓扑图片并返回文件路径
        """
        try:
            nodes, edges = self._parse_mermaid(mermaid_code)
            if not nodes:
                return None

            # 简易分层排布 (Top-Down BFS 层级分配)
            node_ids = list(nodes.keys())
            in_degrees = {nid: 0 for nid in node_ids}
            for _u, v, _ in edges:
                if v in in_degrees:
                    in_degrees[v] += 1

            # 拓扑分层
            layers: List[List[str]] = []
            visited = set()

            # 第一层：入度为0的节点
            current_layer = [nid for nid in node_ids if in_degrees[nid] == 0]
            if not current_layer:
                current_layer = [node_ids[0]]

            layers.append(current_layer)
            visited.update(current_layer)

            # 后续层级
            while len(visited) < len(node_ids):
                next_layer = []
                for u in layers[-1]:
                    for from_id, to_id, _ in edges:
                        if from_id == u and to_id not in visited and to_id not in next_layer:
                            next_layer.append(to_id)
                if not next_layer:
                    # 放入未被访问的孤立节点
                    remaining = [nid for nid in node_ids if nid not in visited]
                    next_layer = remaining[:3]
                layers.append(next_layer)
                visited.update(next_layer)

            # 绘图初始化
            fig_width = max(8.5, len(max(layers, key=len)) * 2.8)
            fig_height = max(5.5, len(layers) * 1.8)
            fig, ax = plt.subplots(figsize=(fig_width, fig_height), dpi=200)
            ax.set_facecolor("#FFFFFF")
            fig.patch.set_facecolor("#FFFFFF")

            # 计算各节点绘制中心坐标 (x, y)
            positions: Dict[str, Tuple[float, float]] = {}
            total_layers = len(layers)

            for l_idx, layer in enumerate(layers):
                y = 0.90 - (l_idx / max(1, total_layers - 0.5)) * 0.75
                layer_count = len(layer)
                for n_idx, nid in enumerate(layer):
                    x = (n_idx + 1) / (layer_count + 1)
                    positions[nid] = (x, y)

            # 绘制连线与箭头
            for u, v, _ in edges:
                if u in positions and v in positions:
                    x1, y1 = positions[u]
                    x2, y2 = positions[v]
                    arrow = patches.FancyArrowPatch(
                        (x1, y1 - 0.05), (x2, y2 + 0.05),
                        arrowstyle='-|>',
                        mutation_scale=14,
                        linewidth=1.8,
                        color='#475569',
                        connectionstyle="arc3,rad=0.0"
                    )
                    ax.add_patch(arrow)

            # 绘制节点卡片
            for i, nid in enumerate(node_ids):
                if nid not in positions:
                    continue
                x, y = positions[nid]
                label = nodes[nid]
                col = self.THEME_COLORS[i % len(self.THEME_COLORS)]

                # 绘制阴影底框
                shadow = patches.FancyBboxPatch(
                    (x - 0.12 + 0.003, y - 0.04 - 0.003), 0.24, 0.08,
                    boxstyle="round,pad=0.015,rounding_size=0.02",
                    fc="#E2E8F0", ec="none", zorder=2
                )
                ax.add_patch(shadow)

                # 绘制主卡片框
                rect = patches.FancyBboxPatch(
                    (x - 0.12, y - 0.04), 0.24, 0.08,
                    boxstyle="round,pad=0.015,rounding_size=0.02",
                    fc=col["box"], ec=col["border"], lw=1.6, zorder=3
                )
                ax.add_patch(rect)

                # 填入文字（自动换行）
                if len(label) > 12:
                    display_text = label[:10] + "\n" + label[10:22]
                else:
                    display_text = label

                ax.text(
                    x, y, display_text,
                    ha='center', va='center',
                    fontsize=10.5,
                    fontweight='bold',
                    color=col["text"],
                    zorder=4
                )

            # 边框与标题
            ax.set_xlim(0, 1)
            ax.set_ylim(0, 1)
            ax.axis('off')

            if diagram_title:  # Word 导出时图题由题注承担，图内不再重复
                plt.title(
                    f"【{diagram_title}】",
                    fontsize=13, fontweight='bold', color='#0F172A',
                    pad=14
                )

            img_id = uuid.uuid4().hex[:8]
            output_file = self.output_dir / f"topology_{img_id}.png"
            plt.tight_layout()
            plt.savefig(str(output_file), dpi=200, bbox_inches='tight')
            plt.close(fig)

            return output_file
        except Exception as e:
            print(f"[DiagramRenderer] 绘图失败: {e}")
            return None

diagram_renderer = DiagramRenderer()
