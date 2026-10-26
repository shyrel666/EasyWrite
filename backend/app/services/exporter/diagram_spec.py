"""简单流程图按显式逻辑组拆图，保留跨图连接；复杂 Mermaid 原样交给真实渲染器。"""
import re

from app.models.schemas import DiagramSpec

MAX_NODES = 10
PART_NODES = 6
DIAGRAM_DISCIPLINE = """【图表规范】每图须有 title 和图例，节点标签简短但不得截断语义；每图不超过 10 个节点，复杂图按 subgraph 逻辑层拆分。
架构图至少包含业务入口、应用服务、数据层及连接方向；网络拓扑至少包含网络区域、设备、边界和链路；
部署图至少包含运行节点、组件归属和通信关系；流程图至少包含起点、动作、判断/异常分支和终点。
统一使用中性配色，图例说明线条含义；无依据的设备数量、厂商和性能指标不得编造。"""
NODE = re.compile(r'^([A-Za-z_][\w-]*)(?:\[([^\]]*)\]|\(([^)]*)\)|\{([^}]*)\})?$')


def parse_spec(code):
    text = re.sub(r'^\s*---\s*\n.*?\n---\s*\n', '', code, flags=re.S)
    lines = [s.strip() for s in text.splitlines() if s.strip() and not s.strip().startswith('%%')]
    if not lines or not re.fullmatch(r'(?:graph|flowchart)\s+(?:TD|TB|LR|RL|BT)\s*;?', lines[0]):
        return None
    spec = DiagramSpec()
    stack = []

    def node(raw):
        m = NODE.fullmatch(raw.strip().rstrip(';').strip())
        if not m:
            raise ValueError('复杂节点语法保留原图')
        ident = m.group(1)
        label = next((g for g in m.groups()[1:] if g is not None), spec.nodes.get(ident, ident))
        spec.nodes[ident] = label.strip('"')
        if stack and ident not in spec.groups.setdefault(stack[-1], []):
            spec.groups[stack[-1]].append(ident)
        return ident

    try:
        for line in lines[1:]:
            if line.startswith('subgraph '):
                stack.append(line[9:].strip())
            elif line == 'end':
                if not stack:
                    return None
                stack.pop()
            elif re.match(r'^(style|classDef|class|click|linkStyle|direction)\b', line):
                return None
            elif '-->' in line:
                parts = line.split('-->')
                left = node(parts[0])
                for raw in parts[1:]:
                    label = ''
                    if raw.strip().startswith('|'):
                        _, label, raw = raw.strip().split('|', 2)
                    right = node(raw)
                    spec.edges.append([left, right, label])
                    left = right
            else:
                node(line)
    except ValueError:
        return None
    return spec if not stack and spec.nodes else None


def diagram_parts(code):
    spec = parse_spec(code)
    if not spec or len(spec.nodes) <= MAX_NODES:
        return []
    assigned = set()
    groups = list(spec.groups.items()) + [('其他节点', list(spec.nodes))]
    parts = []
    for label, identifiers in groups:
        identifiers = [n for n in identifiers if n not in assigned]
        assigned.update(identifiers)
        for offset in range(0, len(identifiers), PART_NODES):
            ids = identifiers[offset:offset + PART_NODES]
            lines = ['graph TD'] + [f'{n}["{spec.nodes[n]}"]' for n in ids]
            connections = []
            for a, b, edge in spec.edges:
                if a in ids and b in ids:
                    lines.append(f'{a} -->' + (f'|{edge}|' if edge else '') + f' {b}')
                elif a in ids or b in ids:
                    connections.append(f'{spec.nodes[a]} → {spec.nodes[b]}' + (f'（{edge}）' if edge else ''))
            parts.append({'title': f'{label} {offset // PART_NODES + 1}', 'code': '\n'.join(lines),
                          'connections': list(dict.fromkeys(connections)), 'legend': spec.legend})
    return parts


def diagram_checks(outline):
    rows = []
    def walk(nodes):
        for n in nodes:
            for m in re.finditer(r'```mermaid\s*\n(.*?)```', n.content, re.S | re.I):
                spec = parse_spec(m.group(1))
                reasons = []
                if not spec:
                    reasons.append('复杂语法须经 Mermaid 实际渲染核验；失败时保留插槽')
                elif len(spec.nodes) > MAX_NODES:
                    reasons.append(f'{len(spec.nodes)} 个节点，将按逻辑组分图并保留跨图连接')
                if spec and any(len(label) > 24 for label in spec.nodes.values()):
                    reasons.append('节点长标签可能导致文字过小，请简化措辞并复核')
                if reasons:
                    rows.append({'section_id': n.id, 'title': n.title, 'detail': '；'.join(reasons)})
            walk(n.children)
    walk(outline)
    return rows
