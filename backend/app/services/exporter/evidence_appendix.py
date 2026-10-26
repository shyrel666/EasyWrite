"""仅解析服务端登记的证明原件，限制资源消耗，不合成或裁切证明内容。"""
import hashlib
import io
import math
from dataclasses import dataclass

import fitz
from PIL import Image, ImageOps

from app.models.schemas import EvidenceAppendixEntry, EvidenceAppendixPlan
from app.services.assets.asset_manager import asset_manager
from app.services.assets.evidence import parse_key, resolve_links
from app.services.assets.material_check import BLOCKING_CODES, asset_name, bid_deadline, check_material
from app.services.exporter.scoring_index import bookmark_name, scoring_index_rows

MAX_PAGES = 100
MAX_FILE_PAGES = 40
MAX_PIXELS = 24_000_000
MAX_TOTAL_BYTES = 80 * 1024 * 1024


class AppendixError(ValueError):
    pass


@dataclass
class PreparedAppendix:
    entry: EvidenceAppendixEntry
    images: list
    bookmark: str
    attachment_keys: list
    item_ids: list
    warnings: list


def _read_file(key, attachment_id, expected_sha=""):
    kind, asset_id = parse_key(key)
    try:
        path, meta = asset_manager.attachment_file(kind, asset_id, attachment_id)
    except (KeyError, ValueError) as e:
        raise AppendixError("证明附件缺失，请重新选择") from e
    path = path.resolve()
    root = asset_manager.files_dir.resolve()
    if root not in path.parents or path.stat().st_size > 20 * 1024 * 1024:
        raise AppendixError("附件路径越界或文件超过 20MB")
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if sha != meta.sha256 or expected_sha and sha != expected_sha:
        raise AppendixError("附件指纹已变化，请重新核对附件计划")
    return raw, meta


def _page_count(raw):
    if raw.startswith(b"%PDF-"):
        with fitz.open(stream=raw, filetype="pdf") as doc:
            if doc.is_encrypted:
                raise AppendixError("加密 PDF 不能编入附件")
            return doc.page_count
    with Image.open(io.BytesIO(raw)) as im:
        return getattr(im, "n_frames", 1)


def appendix_options(project):
    rows = scoring_index_rows(project)
    options = {}
    deadline, _ = bid_deadline(project.tender_analysis)
    for row in rows:
        found, missing = resolve_links(project.evidence_links.get(row["id"], []))
        for key in missing:
            options.setdefault(key, {"key": key, "caption": "关联资料已删除", "problems": ["关联失效"], "available": False})
        for kind, asset in found:
            key = f"{kind}:{asset['id']}"
            check = check_material(kind, asset, project.facts, deadline)
            for att in asset.get("attachments", []):
                identity = f"{key}:{att['id']}"
                if identity in options:
                    options[identity]["item_ids"].append(row["id"])
                    continue
                entry = {"section_id": row["sections"][0]["id"] if row["sections"] else "", "asset_key": key,
                         "attachment_id": att["id"], "pages": [], "caption": f"{asset_name(kind, asset)} — {att['filename']}",
                         "placement": "appendix", "sha256": att.get("sha256", "")}
                item = {"key": identity, **entry, "item_ids": [row["id"]], "problems": [p.message for p in check.problems],
                        "available": not any(p.code in BLOCKING_CODES | {"example"} for p in check.problems)}
                try:
                    raw, _ = _read_file(key, att["id"])
                    item["page_count"] = _page_count(raw)
                    if not 1 <= item["page_count"] <= MAX_FILE_PAGES:
                        raise AppendixError("单个附件页数必须在 1 至 40 页内")
                except Exception as e:
                    item["available"] = False
                    item["problems"].append(str(e) if isinstance(e, AppendixError) else "附件损坏或格式无法读取")
                options[identity] = item
            if not asset.get("attachments"):
                options.setdefault(key, {"key": key, "caption": asset_name(kind, asset), "problems": ["缺少证明附件"], "available": False})
    return list(options.values())


def prepare_appendices(project, entries):
    entries = EvidenceAppendixPlan(entries=entries).entries
    if len(entries) > MAX_PAGES:
        raise AppendixError("附件计划条目过多")
    options = {o["key"]: o for o in appendix_options(project)}
    prepared, by_content = [], {}
    total_bytes = total_pages = 0
    for entry in entries:
        identity = f"{entry.asset_key}:{entry.attachment_id}"
        option = options.get(identity)
        if not option or not option["available"]:
            raise AppendixError("所选附件未关联当前分包或不可用：" + "；".join((option or {}).get("problems", [])))
        if entry.placement == "section":
            from app.services.project_store import find_node
            node = find_node(project.outline, entry.section_id)
            if not node or not set(node.scoring_item_ids) & set(option["item_ids"]):
                raise AppendixError("附件放置章节未承接对应评分项")
        raw, meta = _read_file(entry.asset_key, entry.attachment_id, entry.sha256)
        pages = entry.pages or list(range(1, option["page_count"] + 1))
        if len(set(pages)) != len(pages) or any(p < 1 or p > option["page_count"] for p in pages):
            raise AppendixError("附件页序重复或包含不存在的页")
        signature = (meta.sha256, tuple(pages))
        if signature in by_content:
            previous = by_content[signature]
            previous.attachment_keys.append(identity)
            previous.item_ids = list(dict.fromkeys(previous.item_ids + option["item_ids"]))
            continue
        total_bytes += len(raw)
        total_pages += len(pages)
        if total_bytes > MAX_TOTAL_BYTES or total_pages > MAX_PAGES:
            raise AppendixError("附件总量超过 80MB 或 100 页")
        images = []
        def add_image(number, data, images=images):
            nonlocal total_bytes
            total_bytes += len(data)
            if total_bytes > MAX_TOTAL_BYTES:
                raise AppendixError("附件渲染后总量超过 80MB")
            images.append((number, data))

        try:
            if raw.startswith(b"%PDF-"):
                with fitz.open(stream=raw, filetype="pdf") as doc:
                    for number in pages:
                        page = doc[number - 1]
                        scale = min(200 / 72, math.sqrt(MAX_PIXELS / max(1, page.rect.width * page.rect.height)))
                        add_image(number, page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png"))
            else:
                with Image.open(io.BytesIO(raw)) as im:
                    for number in pages:
                        im.seek(number - 1)
                        if im.width * im.height > MAX_PIXELS:
                            raise AppendixError("扫描件超过 2400 万像素")
                        page = ImageOps.exif_transpose(im).convert("RGB")
                        buf = io.BytesIO()
                        page.save(buf, format="PNG")
                        add_image(number, buf.getvalue())
        except AppendixError:
            raise
        except Exception as e:
            raise AppendixError("附件损坏或无法渲染") from e
        warnings = list(option["problems"])
        omitted = sorted(set(range(1, option["page_count"] + 1)) - set(pages))
        if omitted:
            warnings.append("未编入原件页：" + "、".join(map(str, omitted)))
        rendered = PreparedAppendix(entry, images, bookmark_name("a", str(signature)), [identity], option["item_ids"], warnings)
        by_content[signature] = rendered
        prepared.append(rendered)
    return prepared
