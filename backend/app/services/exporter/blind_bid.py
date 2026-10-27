"""受限暗标副本：已知标识脱敏、纯文本技术册、统一样式；不替代人工逐页验收。"""
import io
import re
import zipfile

from fastapi import HTTPException
from lxml import etree

from app.models.schemas import BlindBidConfig, NOT_MENTIONED
from app.services.assets.asset_manager import asset_manager
from app.services.assets.evidence import parse_key
from app.services.project_store import _walk

# 与 docx_generator 一致：行首（可缩进、可在引用内）info 以 mermaid 开头的 ``` / ~~~ 围栏都会出图，
# 未闭合的围栏延续到正文末尾；行中间的反引号是行内代码，不算围栏。
IMAGES = re.compile(r'^[ \t>]*(?P<fence>`{3,}|~{3,})[ \t]*mermaid[^\n]*\n.*?(?:^[ \t>]*(?P=fence)|\Z)'
                    r'|!\[[^\]]*\]\([^)]*\)|!\[[^\]]*\]\[[^\]]*\]|<img\b[^>]*>', re.S | re.I | re.M)
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS = {'w': W}
XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'
MARK = '【已隐去】'
# 自定义事实按键名识别身份信息；“公司员工数”“公司成立年份”之类不是标识。
IDENTITY_KEYS = re.compile(r'地址|电话|手机|邮件|邮箱|账号|账户|联系人|代表|姓名|网址|品牌|简称|别名|(?:公司|企业|单位)(?:名称|全称)')
# 只改写承载文字的节点与属性；缩进、行距、编号等数值属性被替换后 Word 会判定文件损坏。
TEXT_TAGS = {'t', 'instrText', 'delText', 'delInstrText'}
TEXT_ATTRS = {'descr', 'title', 'tooltip', 'instr', 'author', 'initials'}
VAL_TEXT_TAGS = {'alias', 'tag', 'tblCaption', 'tblDescription'}


def effective_config(project, template=None):
    """项目显式开启或关闭时以项目为准；未设置（enabled 为 None）时沿用模板默认。"""
    config = project.blind_bid
    if config.enabled is None:
        config = BlindBidConfig(**((template or {}).get('blind_bid') or {}))
    return config.model_copy(update={'enabled': bool(config.enabled)})


def _usable(value):
    """过短的值或短数字（员工数 360、年份 2008）不是身份标识，自动收集时跳过以免误改正文。"""
    return len(value) >= 2 and not (value.isdigit() and len(value) < 6)


def known_identifiers(project, config):
    facts = project.facts
    values = [facts.company_name, facts.legal_rep, facts.credit_code, facts.core_product_name]
    values += [v for k, v in facts.custom_facts.items() if IDENTITY_KEYS.search(k)]
    keys = {k for keys in project.evidence_links.values() for k in keys}
    for node in _walk(project.outline):
        # 自动匹配写入章节的资料只记录在 last_refs；资料删除后仍用记录中的姓名。
        for ref in node.last_refs:
            if ref.get('ref_type') == 'asset':
                keys.add(f"{ref.get('kind', '')}:{ref.get('asset_id', '')}")
                if ref.get('kind') == 'personnel':
                    values.append(ref.get('name'))
    for key in keys:
        kind, ident = parse_key(key)
        asset = asset_manager.get_asset(kind, ident)
        if asset:
            values.append(asset.get('holder'))
            if kind == 'personnel':
                values.append(asset.get('name'))
    found = {v.strip() for v in values if isinstance(v, str)}
    # 用户补充的标识按原样采用，不做长度过滤。
    found = {v for v in found if _usable(v)} | {v.strip() for v in config.identifiers if v.strip()}
    return sorted(found - {NOT_MENTIONED, '待填写'}, key=lambda v: (-len(v), v))


def identifier_pattern(identifiers):
    """全部标识合成一个前瞻模式，一次扫描即可得到每个位置上最长的匹配；允许字间有空白或断行。"""
    if not identifiers:
        return None
    ordered = sorted(set(identifiers), key=lambda v: (-len(v), v))
    return re.compile('(?=(' + '|'.join(r'\s*'.join(map(re.escape, v)) for v in ordered) + '))', re.I)


def _spans(text, pattern):
    """合并重叠区间：两个标识首尾交叠时整段隐去，避免先替换一个后另一个残留半截。"""
    spans = []
    for m in pattern.finditer(text):
        start, end = m.span(1)
        if spans and start <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([start, end])
    return spans


def redact(text, pattern):
    if not text or pattern is None:
        return text
    for start, end in reversed(_spans(text, pattern)):
        text = text[:start] + MARK + text[end:]
    return text


def _norm(value):
    return re.sub(r'\s+', '', value).lower()


def image_sections(project):
    return [{'section_id': n.id, 'section_title': n.title} for n in _walk(project.outline) if IMAGES.search(n.title + '\n' + n.content)]


def inspect_blind(project, config):
    identifiers = known_identifiers(project, config)
    pattern = identifier_pattern(identifiers)
    names = {_norm(v): v for v in identifiers}
    hits = []
    if pattern:
        for node in _walk(project.outline):
            found = dict.fromkeys(names.get(_norm(m.group(1)), m.group(1)) for m in pattern.finditer(node.title + '\n' + node.content))
            hits += [{'section_id': node.id, 'section_title': node.title, 'identifier': v} for v in found]
    images = image_sections(project)
    return {'config': config, 'hits': hits, 'images': images,
            'blocked': bool(config.enabled) and bool(images) and not config.omit_images,
            'checklist': ['对照招标原文核对字体、字号、编号和页边距', '逐页检查别名、人员、地址、账号及间接身份线索',
                          '确认技术册不含明标证明、印章及未核验图片', '在最终 Word/PDF 中复核目录、分页及文件属性']}


def prepare_blind(project, config):
    if image_sections(project) and not config.omit_images:
        raise HTTPException(400, '暗标技术册含未经核验的图片或图代码；请核对后明确选择排除全部图片，或调整章节')
    identifiers = known_identifiers(project, config)
    pattern = identifier_pattern(identifiers)
    copy = project.model_copy(deep=True)
    for node in _walk(copy.outline):
        node.title = redact(node.title, pattern)
        node.content = redact(IMAGES.sub('', node.content), pattern)
    copy.name = redact(copy.name, pattern)
    return copy, identifiers


def scrub_docx(path, config, identifiers):
    """对最终包逐个 XML 部件处理：跨 run 文本、承载文字的属性、页眉页脚及外链；结构属性不动。"""
    pattern = identifier_pattern(identifiers)
    output = io.BytesIO()
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as target:
        for name in source.namelist():
            data = source.read(name)
            if name.startswith('word/media/'):
                raise HTTPException(400, '暗标副本仍含未核验图像，已停止导出')
            if name.endswith(('.xml', '.rels')):
                root = etree.fromstring(data, parser)
                if name.startswith('docProps/'):
                    for element in list(root):
                        root.remove(element)
                # 合并同段的文本处理可防止姓名被分为多个格式 run 而漏扫。
                for paragraph in root.findall('.//w:p', NS):
                    texts = paragraph.findall('.//w:t', NS)
                    full = ''.join(t.text or '' for t in texts)
                    clean = redact(full, pattern)
                    if clean != full and texts:
                        texts[0].text = clean
                        texts[0].set(XML_SPACE, 'preserve')
                        for t in texts[1:]:
                            t.text = ''
                for element in root.iter(etree.Element):
                    tag = etree.QName(element).localname
                    if element.text and tag in TEXT_TAGS:
                        element.text = redact(element.text, pattern)
                    for key, value in list(element.attrib.items()):
                        attr = etree.QName(key).localname
                        if attr in TEXT_ATTRS or (attr == 'val' and tag in VAL_TEXT_TAGS):
                            element.set(key, redact(value, pattern))
                    if element.get('TargetMode') == 'External':
                        element.set('Target', 'about:blank')
                for props in root.findall('.//w:rPr', NS):
                    for tag, attrs in [('rFonts', {'ascii': config.font_name, 'hAnsi': config.font_name, 'eastAsia': config.font_name, 'cs': config.font_name}),
                                       ('sz', {'val': str(round(config.font_size * 2))}), ('szCs', {'val': str(round(config.font_size * 2))}),
                                       ('color', {'val': '000000'})]:
                        element = props.find(f'{{{W}}}{tag}')
                        if element is None:
                            element = etree.SubElement(props, f'{{{W}}}{tag}')
                        element.attrib.clear()
                        for key, value in attrs.items():
                            element.set(f'{{{W}}}{key}', value)
                for level in root.findall('.//w:lvl', NS):
                    for tag, value in [('numFmt', 'decimal'), ('lvlText', f'%{int(level.get(f"{{{W}}}ilvl", "0")) + 1}.')]:
                        element = level.find(f'{{{W}}}{tag}')
                        if element is not None:
                            element.set(f'{{{W}}}val', value)
                if name.startswith('word/header'):
                    for t in root.findall('.//w:t', NS):
                        t.text = ''
                data = etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
            target.writestr(name, data)
    path.write_bytes(output.getvalue())
