"""
阶段 E 效果评估：用真实招标文件对比"单次生成"（与批量撰写相同：选资料后一次写成）和"智能完善"（阶段 D 写—查—改闭环）。

  python scripts/eval_sections.py plan       只按规则拆标、生成大纲，列出将评估的章节与请求次数上限（不调用模型、不产生费用）
  python scripts/eval_sections.py run        调用模型生成并检查，报告写入 docs/eval/<日期>.md（真实模型请求会产生费用，开始前需确认）
  python scripts/eval_sections.py summarize docs/eval/<日期>.md
                                             填好人工标注列、改好 *.edited.md 后运行：计算修改量，重新汇总并判断决策门槛

- 隔离：运行数据复制到 backend/data/eval/<时间>/（数据库用 SQLite 在线备份，含知识库；企业资料与模型配置为同一份），
  不改动 backend/data 中的项目；复制来的模型配置（含密钥）在载入后立即删除，只留在本进程内存中。
- 公平：两种写法使用同一模型、同一份大纲、知识库、企业资料与全局事实（--facts）；拆标与大纲按规则生成（不调用模型，可复现）。
- 评估大纲"一个评分项一节"：每节须写到该评分项的全部要点（评分子项 / 评分标准列举的内容）。产品的规则大纲把要点拆成
  一个要点一个小节、要点即小节标题，检查时恒为已覆盖，无法区分两种写法。
- 指标：两份正文用同一套规则检查（C1）重新检查；请求次数、耗时与服务商返回的用量按运行（任务 ID）从 llm_calls 汇总，
  服务商未返回用量的请求记为未知，不估算。
- 正文与改稿保存在工作目录 texts/ 下（可能含企业资料，不入库）；报告只含指标，人工标注列留空由用户填写。
- 智能完善的预算沿用产品配置（REFINE_MAX_CALLS / REFINE_MAX_SECONDS，可用同名环境变量调整）。
"""
import argparse
import difflib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
SOURCE_DATA_DIR = BACKEND_DIR / "data"
TENDER_DIR = PROJECT_ROOT / "downloads" / "ccgp_2026-10-06"
EVAL_ROOT = SOURCE_DATA_DIR / "eval"
REPORT_DIR = PROJECT_ROOT / "docs" / "eval"

# 默认样本：重庆政务应用运维（Word）、数字审计升级与 AI 场景（Word）、中国政府网运维（代理机构发布的 PDF）；
# 三份的方案类评分项都列有评分要点，能比较要点覆盖
DEFAULT_TENDERS = ["03_招标文件", "02_（上网稿）", "01_2026年度中国政府网"]
# 与 tests/conftest.py 相同的运行时路径：全部指向工作目录
RUNTIME_PATHS = {
    "DATA_DIR": "", "DB_PATH": "easywrite.db", "UPLOAD_DIR": "uploads", "KNOWLEDGE_DIR": "knowledge_store",
    "OUTPUT_DIR": "exports", "RENDERED_DIR": "rendered_diagrams", "TEMPLATE_DIR": "templates",
}

CATEGORIES = {"proposal": "高分值方案", "service": "运维服务", "team": "团队与资质", "custom": "指定章节"}
PERSONNEL_WORDS = re.compile(r"团队|人员|项目经理|负责人|成员|配备")
CREDENTIAL_WORDS = re.compile(r"资质|业绩|证书|认证")
# "响应"不算：招标文件常说"需求响应方案"，指逐条响应需求而不是运维服务
SERVICE_WORDS = re.compile(r"运维|运行维护|售后|维保|巡检|应急|故障|服务保障|保障体系")
UNDERSTANDING_WORDS = re.compile(r"认识|理解|现状|需求分析")

METHODS = {"single": "单次生成", "refine": "智能完善"}
HUMAN_COLUMNS = ("无依据声明（人工）", "未标注待核实（人工）", "阻塞误报（人工）")
EDITED_MARKER = "<!-- 改到可以提交的程度后删除本行；保留本行表示尚未标注修改量 -->"
WORKSPACE_TAG = re.compile(r"<!-- eval-workspace: (.+?) -->")
NOTES_HEADING = "## 备注"
DEFAULT_NOTES = "（评估过程中的观察写在这里；运行 summarize 重新汇总时保留本节内容。）"

# 决策门槛（IMPROVEMENT_PLAN 阶段 E）：阻塞问题减少一半以上、无依据声明不增加、请求次数不超过单次生成的 4 倍
GATE_BLOCKING_RATIO = 0.5
GATE_CALLS_RATIO = 4
POLL_SECONDS = 0.5


# ---------------- 通用 ----------------

def date_label(d: datetime) -> str:
    """strftime 不放中文（Windows 下报错），日期统一用 f-string 拼接"""
    return f"{d.year}-{d.month:02d}-{d.day:02d}"


def time_label(d: datetime) -> str:
    return f"{date_label(d)} {d.hour:02d}:{d.minute:02d}"


def stamp(d: datetime) -> str:
    return f"{d.year}{d.month:02d}{d.day:02d}-{d.hour:02d}{d.minute:02d}{d.second:02d}"


def rel(path: Path, start: Path = PROJECT_ROOT) -> str:
    try:
        return Path(os.path.relpath(path, start)).as_posix()
    except ValueError:  # 不在同一个盘符：用绝对路径
        return Path(path).resolve().as_posix()


def normalize_text(text: str) -> str:
    return "\n".join(line.rstrip() for line in (text or "").replace("\r\n", "\n").split("\n")).strip()


def edit_ratio(original: str, edited: str) -> float:
    """修改量：把原稿改成改稿所需的字符级增删改数 ÷ 原稿字数"""
    a, b = normalize_text(original), normalize_text(edited)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    changed = sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal")
    return changed / max(len(a), 1)


def read_edited(path: Path) -> Optional[str]:
    """改稿：文件存在且已删除首行标记才算标注完成，否则返回 None"""
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    if EDITED_MARKER in text:
        return None
    return text


def parse_int(raw: str) -> Optional[int]:
    m = re.match(r"\s*(\d+)", raw or "")
    return int(m.group(1)) if m else None


# ---------------- 工作目录（导入应用之前） ----------------

def prepare_workspace(dst: Path, source: Path = SOURCE_DATA_DIR, with_model: bool = False) -> None:
    """复制运行数据：数据库（在线备份，服务运行中也能得到一致副本）与企业资料；with_model 时复制模型配置"""
    dst.mkdir(parents=True, exist_ok=True)
    db = source / "easywrite.db"
    if db.exists():
        src = sqlite3.connect(str(db))
        out = sqlite3.connect(str(dst / "easywrite.db"))
        try:
            src.backup(out)
        finally:
            out.close()
            src.close()
    names = ["enterprise_assets.json"] + (["ai_settings.json"] if with_model else [])
    for name in names:
        if (source / name).exists():
            shutil.copy2(source / name, dst / name)


def isolate_env(workspace: Path, offline: bool) -> None:
    """必须在导入应用之前调用：数据库、配置与资料库在导入时按这些路径初始化"""
    for name, relative in RUNTIME_PATHS.items():
        os.environ[name] = str(workspace / relative)
    if offline:
        os.environ["LLM_API_KEY"] = ""
        os.environ["EMBEDDING_API_KEY"] = ""
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))


def model_label(source: Path = SOURCE_DATA_DIR) -> str:
    """不导入应用、不读密钥，只显示模型名"""
    try:
        data = json.loads((source / "ai_settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "未配置（backend/data/ai_settings.json 不存在）"
    return f"{data.get('provider', '?')} / {data.get('model', '?')}"


def resolve_tenders(specs: List[str], tender_dir: Path = TENDER_DIR) -> List[Path]:
    """招标文件：路径，或 downloads/ccgp_2026-10-06 下的文件名前缀（须唯一）"""
    files = sorted(f for f in tender_dir.glob("*") if f.suffix.lower() in (".docx", ".pdf")) if tender_dir.is_dir() else []
    out = []
    for spec in specs:
        path = Path(spec)
        if path.is_file():
            out.append(path.resolve())
            continue
        matches = [f for f in files if f.name.startswith(spec)]
        if len(matches) != 1:
            found = "、".join(m.name for m in matches) if matches else f"{rel(tender_dir)} 下没有以它开头的 .docx / .pdf"
            raise SystemExit(f"招标文件 {spec}：{'匹配到多个：' if matches else ''}{found}")
        out.append(matches[0])
    return out


# ---------------- 选章节（规则，确定性） ----------------

def _flatten(nodes) -> list:
    out = []
    for n in nodes:
        out.append(n)
        out.extend(_flatten(n.children))
    return out


def section_candidates(project) -> List[Dict[str, Any]]:
    """可写叶子章节（不含逐条应答表）及其分值份额：承接评分项分值 ÷ 分担该评分项的章节数"""
    from app.services.generator import rubric_planner as rp

    items = {it.id: it for it in rp.target_items(project.tender_analysis)}
    leaves = [n for n in _flatten(project.outline) if not n.children and n.content_mode != "point_to_point"]
    carriers = Counter(x for n in leaves for x in n.scoring_item_ids if x in items)
    out = []
    for n in leaves:
        own = [items[x] for x in n.scoring_item_ids if x in items]
        out.append({
            "node": n,
            "share": round(sum((it.points or 0) / carriers[it.id] for it in own), 2),
            "proposal": any(it.response_type == "proposal" for it in own),
            "item_text": " ".join(it.name for it in own),
        })
    return out


def pick_sections(project, section_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    每个项目选三类章节各一节（互不重复，找不到的类别跳过），按以下顺序依次选：
    - 团队与资质：标题或评分项名称含人员类字样优先，其次资质 / 业绩；需撰写方案的优先，再按分值份额
    - 高分值方案：其余需撰写方案的章节中分值份额最高的（同分时"项目理解"类靠后）
    - 运维服务：标题或评分要点含运维 / 应急 / 故障等（标题为"认识""现状分析"类的不算）；需撰写方案的优先，再按分值份额
    section_ids 给定时只评估这些章节（类别记为"指定章节"）。
    """
    cands = section_candidates(project)
    if section_ids:
        by_id = {c["node"].id: c for c in cands}
        missing = [s for s in section_ids if s not in by_id]
        if missing:
            raise SystemExit(f"{project.name}：没有可评估的章节 {'、'.join(missing)}（须为叶子章节，且不是逐条应答表）")
        return [dict(by_id[s], category="custom") for s in section_ids]

    taken: set = set()

    def best(pool, key):
        pool = [c for c in pool if c["node"].id not in taken]
        return min(pool, key=key) if pool else None

    def team_text(c):
        return f"{c['node'].title} {c['item_text']}"

    team = best([c for c in cands if PERSONNEL_WORDS.search(team_text(c)) or CREDENTIAL_WORDS.search(team_text(c))],
                key=lambda c: (0 if PERSONNEL_WORDS.search(team_text(c)) else 1, not c["proposal"], -c["share"]))
    if team:
        taken.add(team["node"].id)
    proposal = best([c for c in cands if c["proposal"]],
                    key=lambda c: (-c["share"], bool(UNDERSTANDING_WORDS.search(c["node"].title))))
    if proposal:
        taken.add(proposal["node"].id)
    service = best([c for c in cands if SERVICE_WORDS.search(" ".join([c["node"].title, *c["node"].requirements]))
                    and not UNDERSTANDING_WORDS.search(c["node"].title)],
                   key=lambda c: (not c["proposal"], -c["share"]))
    picked = [(proposal, "proposal"), (service, "service"), (team, "team")]
    return [dict(c, category=cat) for c, cat in picked if c]


# ---------------- 建项目（规则拆标与大纲，不调用模型） ----------------

@contextmanager
def rules_only():
    """拆标与大纲按规则生成：暂时视为未配置模型（本脚本单线程建项目，期间没有其他模型请求）"""
    from app.core.llm_client import llm_client

    saved = llm_client.is_configured
    llm_client.is_configured = False
    try:
        yield
    finally:
        llm_client.is_configured = saved


def item_outline(analysis) -> list:
    """
    评估大纲：一级章节与产品规则大纲相同（rubric_chapters：方案类逐项成章，其余按应答方式归并），
    二级"一个评分项一节"，要求取 item_requirements（评分项 + 每个"须覆盖"要点）；字数预算沿用产品的分配规则。
    """
    from app.models.schemas import OutlineNode
    from app.services.generator import rubric_planner as rp
    from app.services.generator.outline_generator import _assign_paths, outline_generator

    items = rp.target_items(analysis)
    by_id = {it.id: it for it in items}
    modes = {"compliance": "point_to_point", "evidence": "template_fill"}
    outline, weights = [], {}
    for i, ch in enumerate(rp.rubric_chapters(items), 1):
        children = []
        for j, it in enumerate((by_id[x] for x in ch["scoring_item_ids"]), 1):
            child = OutlineNode(id=f"sec_{i}_{j}", title=f"{i}.{j} {it.name}", level=2,
                                requirements=rp.item_requirements(it), scoring_item_ids=[it.id],
                                content_mode=modes.get(it.response_type, "ai_generate"))
            weights[child.id] = it.points or 1.0
            children.append(child)
        outline.append(OutlineNode(id=f"sec_{i}", title=ch["title"], level=1, requirements=ch["requirements"],
                                   scoring_item_ids=ch["scoring_item_ids"], content_mode=ch["content_mode"],
                                   children=children))
    outline_generator._allocate_budgets(outline, 30000, by_id, weights)
    _assign_paths(outline)
    return outline


def setup_project(path: Path, facts=None):
    """建评估项目：解析招标文件 → 规则拆标（含评分细则）→ 评估大纲（一个评分项一节）"""
    from app.models.schemas import GlobalFacts, ProjectCreate
    from app.services.parser.document_parser import describe_source, parse_document
    from app.services.parser.tender_analyzer import tender_analyzer
    from app.services.project_store import project_store

    with rules_only():
        parsed = parse_document(path)
        analysis = tender_analyzer.analyze_document(parsed, filename_hint=path.name)
        analysis.source_note = describe_source(parsed)
        name = analysis.project_name or path.stem
        project = project_store.create(ProjectCreate(name=f"[效果评估] {name}", description=name,
                                                     facts=facts or GlobalFacts()))
        project_store.set_tender_text(project.id, parsed.get("full_text", ""))
        project_store.set_tender_structure(project.id, parsed)
    outline = item_outline(analysis)

    def mutate(p):
        p.tender_analysis = analysis
        p.client_name = analysis.purchaser_name or ""
        p.outline = outline
        p.stage = "outline_confirmed"
        return p

    return project_store.update(project.id, mutate)


def basis_summary() -> Dict[str, Any]:
    """两种写法共用的依据：知识库规模、企业资料（示例资料不参与生成）"""
    from sqlmodel import func, select

    from app.db.database import get_session
    from app.db.models import KBChunk, KBDocument
    from app.services.assets.asset_manager import asset_manager

    with get_session() as session:
        docs = session.exec(select(func.count()).select_from(KBDocument)).one()
        chunks = session.exec(select(func.count()).select_from(KBChunk)).one()
    rows = asset_manager.qualifications + asset_manager.personnel + asset_manager.cases + asset_manager.components
    status = Counter(r.get("status", "") for r in rows)
    return {"kb_documents": int(docs), "kb_chunks": int(chunks), "assets_confirmed": status.get("confirmed", 0),
            "assets_unverified": status.get("unverified", 0), "assets_example": status.get("example", 0)}


def max_requests(max_rounds: int, kb_chunks: int) -> Tuple[int, int]:
    """每节请求次数上限（不含截断放大重试与限流重试）：(单次生成, 智能完善)；知识库有内容时各加一次重排"""
    rerank = 1 if kb_chunks else 0
    return 1 + rerank, 1 + max_rounds + rerank


# ---------------- 运行与测量 ----------------

def _wait(task_id: str, log: Callable[[str], None]) -> Tuple[Dict[str, Any], float]:
    from app.core.task_manager import ACTIVE_STATUSES, task_manager

    start, last = time.monotonic(), ""
    try:
        while True:
            task = task_manager.get(task_id) or {}
            if task.get("status") not in ACTIVE_STATUSES:
                return task, round(time.monotonic() - start, 1)
            message = task.get("message", "")
            if message and message != last:
                log(f"      {message}")
                last = message
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        task_manager.cancel(task_id)
        raise


def measure(project_id: str, section_id: str, content: str) -> Dict[str, Any]:
    """两种写法的正文用同一套规则检查（不含模型评审）"""
    from app.services.checker.section_check import check_section
    from app.services.project_store import find_node, project_store

    project = project_store.get(project_id)
    node = find_node(project.outline, section_id)
    report = check_section(project, node, content)
    return {
        "blocking": report.blocking_count, "quality": report.quality_count,
        "points_hit": sum(1 for p in report.points if p.mentioned), "points_total": len(report.points),
        "pending": len(report.pending_verification), "chars": report.char_count,
        "blocking_issues": [i.message for i in report.issues if i.level == "blocking"],
    }


def _finish(task: Dict[str, Any], seconds: float, content: str, project_id: str, section_id: str) -> Dict[str, Any]:
    from app.core import llm_usage

    out = {"task_id": task.get("id", ""), "status": task.get("status", ""), "error": task.get("error", ""),
           "seconds": seconds, "usage": llm_usage.run_totals(task.get("id", "")), "content": content}
    if content.strip():
        out.update(measure(project_id, section_id, content))
    return out


def run_single(project_id: str, section_id: str, log: Callable[[str], None] = print) -> Dict[str, Any]:
    """现有单次生成：与批量撰写相同（选资料 → 一次写成，用途 section_write），不写入正文"""
    from app.core.task_manager import task_manager
    from app.services.generator.evidence_set import select_evidence
    from app.services.generator.section_generator import section_generator, writing_inputs
    from app.services.project_store import find_node, project_store
    from app.services.refine.runner import evidence_gaps

    def fn(ctx):
        project = project_store.get(project_id)
        node = find_node(project.outline, section_id)
        evidence_kw, prompt_kw = writing_inputs(project, node)
        ctx.report(20, "选择资料")
        evidence = select_evidence(project, node, **evidence_kw)
        ctx.report(40, "生成正文")
        res = section_generator.draft_section(evidence=evidence, **prompt_kw)
        if res.get("mode") != "llm" or not res["generated_content"].strip():
            raise RuntimeError("模型调用失败，未产出正文")
        return {"content": res["generated_content"], "kb_refs": len(evidence.refs), "assets": len(evidence.assets),
                "evidence_gaps": evidence_gaps(evidence)}

    task_id = task_manager.submit("eval_single", fn, description="效果评估：单次生成", project_id=project_id,
                                  meta={"section_id": section_id})
    task, seconds = _wait(task_id, log)
    result = task.get("result") or {}
    out = _finish(task, seconds, result.get("content", "") if task.get("status") == "completed" else "",
                  project_id, section_id)
    out["evidence_gaps"] = result.get("evidence_gaps", [])
    out["kb_refs"], out["assets"] = result.get("kb_refs", 0), result.get("assets", 0)
    return out


def run_refine(project_id: str, section_id: str, max_rounds: int, log: Callable[[str], None] = print) -> Dict[str, Any]:
    """智能完善：与"智能完善"按钮相同的后台任务（空白章节先起草），取最后一版候选稿；不写入正文"""
    from app.core.task_manager import task_manager
    from app.services.proposals import proposal_store
    from app.services.refine import runner

    if not runner.claim(project_id, section_id):
        raise RuntimeError("本节已有智能完善在进行")

    def fn(ctx):
        try:
            return runner.run_refine(ctx, project_id, section_id, max_rounds=max_rounds)
        finally:
            runner.release(project_id, section_id)

    try:
        task_id = task_manager.submit("section_refine", fn, description="效果评估：智能完善", project_id=project_id,
                                      meta={"section_id": section_id})
    except Exception:
        runner.release(project_id, section_id)
        raise
    task, seconds = _wait(task_id, log)
    result = task.get("result") or {}
    final = proposal_store.get(project_id, result["final_proposal_id"]) if result.get("final_proposal_id") else None
    out = _finish(task, seconds, final.content if final else "", project_id, section_id)
    out.update({
        "outcome": result.get("outcome", ""), "outcome_label": runner.OUTCOME_LABELS.get(result.get("outcome", ""), ""),
        "stop_reason": result.get("stop_reason", ""),
        "stop_label": runner.STOP_LABELS.get(result.get("stop_reason", ""), result.get("stop_reason", "")),
        "rounds": result.get("rounds", 0), "max_rounds": result.get("max_rounds", max_rounds),
        "history": [h.get("blocking_count") for h in result.get("history", [])],
        "evidence_gaps": result.get("evidence_gaps", []),
    })
    return out


def write_texts(workspace: Path, key: str, method: str, content: str) -> None:
    """正文原稿与待修改的改稿（首行标记删除后才计入修改量）"""
    texts = workspace / "texts"
    texts.mkdir(parents=True, exist_ok=True)
    (texts / f"{key}.{method}.md").write_text(content, encoding="utf-8")
    edited = texts / f"{key}.{method}.edited.md"
    if not edited.exists():
        edited.write_text(f"{EDITED_MARKER}\n{content}", encoding="utf-8")


# ---------------- 报告（纯函数：只读 results 与 texts，不导入应用） ----------------

def _pct(a: float, b: float) -> str:
    return f"{a / b:.0%}" if b else "—"


def _tokens(usage: Dict[str, Any]) -> str:
    calls, missing = usage.get("calls", 0), usage.get("calls_without_usage", 0)
    if not calls:
        return "—"
    if missing >= calls:
        return "未知"
    total = f"{usage.get('total_tokens', 0):,}"
    return f"{total}（{missing} 次未返回）" if missing else total


def _ok(m: Optional[Dict[str, Any]]) -> bool:
    return bool(m) and bool((m.get("content") or "").strip())


def _result_cell(method: str, m: Dict[str, Any]) -> str:
    if method == "single":
        return "完成" if _ok(m) else f"失败：{(m.get('error') or m.get('status') or '')[:40]}"
    if not m.get("outcome") and not _ok(m):
        return f"失败：{(m.get('error') or m.get('status') or '')[:40]}"
    head = m.get("outcome_label") or m.get("outcome", "")
    if m.get("stop_reason") not in ("", "goal_met"):
        head += f"（{m.get('stop_label') or m.get('stop_reason')}）"
    trail = "→".join(str(x) for x in m.get("history", []) if x is not None)
    return f"{head}；{m.get('rounds', 0)}/{m.get('max_rounds', 0)} 轮" + (f"；阻塞 {trail}" if trail else "")


def collect(results: Dict[str, Any], workspace: Path, annotations: Dict[Tuple[str, str], Dict[str, str]]) -> List[Dict[str, Any]]:
    """每行（章节 × 写法）的指标 + 人工标注 + 修改量"""
    rows = []
    for s in results.get("sections", []):
        for method in METHODS:
            m = s.get(method)
            if m is None:
                continue
            ann = annotations.get((s["key"], method), {})
            ratio = None
            if _ok(m):
                edited = read_edited(workspace / "texts" / f"{s['key']}.{method}.edited.md")
                if edited is not None:
                    ratio = edit_ratio(m["content"], edited)
            rows.append({"section": s, "method": method, "m": m, "ann": ann, "ratio": ratio,
                         "human": {c: parse_int(ann.get(c, "")) for c in HUMAN_COLUMNS}})
    return rows


def aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """只比较两种写法都产出正文的章节；人工指标只比较两种写法都已标注的章节"""
    by_key: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for r in rows:
        by_key.setdefault(r["section"]["key"], {})[r["method"]] = r
    paired = [v for v in by_key.values() if all(m in v and _ok(v[m]["m"]) for m in METHODS)]
    agg: Dict[str, Any] = {"paired": len(paired), "sections": len(by_key)}
    for method in METHODS:
        ms = [v[method]["m"] for v in paired]
        usage = [m.get("usage", {}) for m in ms]
        agg[method] = {
            "blocking": sum(m.get("blocking", 0) for m in ms),
            "quality": sum(m.get("quality", 0) for m in ms),
            "points_hit": sum(m.get("points_hit", 0) for m in ms),
            "points_total": sum(m.get("points_total", 0) for m in ms),
            "pending": sum(m.get("pending", 0) for m in ms),
            "calls": sum(u.get("calls", 0) for u in usage),
            "seconds": round(sum(m.get("seconds", 0) for m in ms), 1),
            "usage": {"calls": sum(u.get("calls", 0) for u in usage),
                      "calls_without_usage": sum(u.get("calls_without_usage", 0) for u in usage),
                      "total_tokens": sum(u.get("total_tokens", 0) for u in usage)},
        }
    for col in HUMAN_COLUMNS:
        done = [v for v in paired if all(v[m]["human"][col] is not None for m in METHODS)]
        agg[col] = {"n": len(done), **{m: sum(v[m]["human"][col] for v in done) for m in METHODS}}
    done = [v for v in paired if all(v[m]["ratio"] is not None for m in METHODS)]
    agg["edit"] = {"n": len(done), **{m: (sum(v[m]["ratio"] for v in done) / len(done) if done else None) for m in METHODS}}
    return agg


def gates(agg: Dict[str, Any]) -> List[Tuple[str, str, str, Optional[bool]]]:
    """[(门槛, 单次生成, 智能完善, 是否满足：True / False / None 待定)]"""
    s, r = agg["single"], agg["refine"]
    if not agg["paired"]:
        blocking_ok = None
    elif s["blocking"] == 0:
        blocking_ok = None if r["blocking"] == 0 else False
    else:
        blocking_ok = r["blocking"] <= s["blocking"] * GATE_BLOCKING_RATIO
    claims = agg[HUMAN_COLUMNS[0]]
    claims_ok = (claims["refine"] <= claims["single"]) if claims["n"] and claims["n"] == agg["paired"] else None
    calls_ok = (r["calls"] <= s["calls"] * GATE_CALLS_RATIO) if s["calls"] else None
    ratio = f"（{r['calls'] / s['calls']:.1f} 倍）" if s["calls"] else ""
    claim_note = f"（已标注 {claims['n']}/{agg['paired']} 节）" if claims["n"] < agg["paired"] else ""
    return [
        ("阻塞问题减少一半以上", str(s["blocking"]), str(r["blocking"]), blocking_ok),
        ("无依据企业事实声明不增加（人工）", str(claims["single"]) if claims["n"] else "—",
         (str(claims["refine"]) if claims["n"] else "—") + claim_note, claims_ok),
        (f"请求次数不超过单次生成的 {GATE_CALLS_RATIO} 倍", str(s["calls"]), f"{r['calls']}{ratio}", calls_ok),
    ]


def verdict(checks: List[Tuple[str, str, str, Optional[bool]]], paired: int) -> str:
    if not paired:
        return "没有两种写法都产出正文的章节，无法判断。"
    if any(ok is False for *_, ok in checks):
        return "未达到门槛：先调整检查规则和提示词，再考虑 F4、F5。"
    if any(ok is None for *_, ok in checks):
        return "规则指标已出，人工标注完成后再判断（填好标注列与改稿，运行 summarize）。"
    return "达到门槛：可以进入 F4（全书一致性检查）、F5（多章节编写与 Agent 化）。"


def _mark(ok: Optional[bool]) -> str:
    return {True: "是", False: "否", None: "待定"}[ok]


def render_report(results: Dict[str, Any], workspace: Path, report_path: Path,
                  annotations: Optional[Dict[Tuple[str, str], Dict[str, str]]] = None, notes: str = "") -> str:
    rows = collect(results, workspace, annotations or {})
    agg = aggregate(rows)
    checks = gates(agg)
    basis, cfg = results.get("basis", {}), results.get("config", {})
    out = [f"# 章节生成效果评估 {results.get('date', '')}", "",
           f"<!-- eval-workspace: {rel(workspace)} -->", "",
           "对比现有单次生成（选资料后一次写成，与批量撰写相同）与智能完善（起草 → 规则检查 → 定向修订，"
           f"最多 {cfg.get('max_rounds', '?')} 轮），两种写法使用同一模型、同一份大纲、知识库、企业资料与全局事实。", ""]
    kb = (f"{basis.get('kb_documents', 0)} 份文档（{basis.get('kb_chunks', 0)} 个片段）"
          + ("，没有可检索的参考资料" if not basis.get("kb_chunks") else ""))
    out += [
        f"- 模型：{cfg.get('model', '?')}；智能完善预算每节 {cfg.get('budget_calls', '?')} 次请求 / {cfg.get('budget_seconds', '?')} 秒",
        f"- 知识库：{kb}",
        f"- 企业资料：已确认 {basis.get('assets_confirmed', 0)} 条、待核实 {basis.get('assets_unverified', 0)} 条"
        f"（示例资料 {basis.get('assets_example', 0)} 条不参与生成）",
        f"- 全局事实：{cfg.get('facts', '未填写')}",
        "- 拆标与大纲：按规则生成（不调用模型）；评估大纲一个评分项一节，须写到该评分项的全部要点；"
        "章节按规则选取：高分值方案、运维服务、团队与资质各一节",
        f"- 运行：{results.get('started_at', '')} 开始" + (f"，{results['finished_at']} 结束" if results.get("finished_at") else "，未完成")
        + f"；正文与改稿在 `{rel(workspace / 'texts')}/`（可能含企业资料，不入库）",
        "",
        "## 结论", "",
        "| 门槛 | 单次生成 | 智能完善 | 是否满足 |",
        "| --- | --- | --- | --- |",
    ]
    out += [f"| {name} | {a} | {b} | {_mark(ok)} |" for name, a, b, ok in checks]
    out += ["", f"**{verdict(checks, agg['paired'])}**", ""]

    s, r = agg["single"], agg["refine"]

    def human(col: str) -> Tuple[str, str]:
        h = agg[col]
        if not h["n"]:
            return "—", "—"
        note = f"（{h['n']} 节）" if h["n"] < agg["paired"] else ""
        return f"{h['single']}{note}", f"{h['refine']}{note}"

    fp = agg[HUMAN_COLUMNS[2]]
    fp_rate = ("—", "—") if not fp["n"] else (_pct(fp["single"], s["blocking"]), _pct(fp["refine"], r["blocking"]))
    edit = agg["edit"]
    edit_cells = ("—", "—") if not edit["n"] else tuple(
        f"{edit[m]:.1%}" + (f"（{edit['n']} 节）" if edit["n"] < agg["paired"] else "") for m in METHODS)
    out += [
        f"## 汇总（{agg['paired']}/{agg['sections']} 节两种写法都产出了正文，只比较这些章节）", "",
        "| 指标 | 单次生成 | 智能完善 |",
        "| --- | --- | --- |",
        f"| 阻塞问题 | {s['blocking']} | {r['blocking']} |",
        f"| 质量问题 | {s['quality']} | {r['quality']} |",
        f"| 评分要点文字覆盖率 | {_pct(s['points_hit'], s['points_total'])}（{s['points_hit']}/{s['points_total']}） "
        f"| {_pct(r['points_hit'], r['points_total'])}（{r['points_hit']}/{r['points_total']}） |",
        f"| 待核实项（占位、全局事实外的数值、待核实资料） | {s['pending']} | {r['pending']} |",
        f"| 模型请求次数 | {s['calls']} | {r['calls']} |",
        f"| 耗时（秒） | {s['seconds']} | {r['seconds']} |",
        f"| 服务商返回的用量（tokens） | {_tokens(s['usage'])} | {_tokens(r['usage'])} |",
        f"| 无依据企业事实声明（人工） | {' | '.join(human(HUMAN_COLUMNS[0]))} |",
        f"| 未标注的待核实项（人工） | {' | '.join(human(HUMAN_COLUMNS[1]))} |",
        f"| 阻塞问题误报率（人工） | {' | '.join(fp_rate)} |",
        f"| 平均修改量（字符级差异比例） | {' | '.join(edit_cells)} |",
        "",
        "## 章节", "",
        "| 编号 | 项目 | 章节 | 类别 | 分值份额 | 字数预算 | 评分要点 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    tenders = {t["key"]: t for t in results.get("tenders", [])}
    for sec in results.get("sections", []):
        t = tenders.get(sec["tender"], {})
        out.append(f"| {sec['key']} | {t.get('name', '')} | {sec['title']} | {CATEGORIES.get(sec['category'], sec['category'])} "
                   f"| {sec.get('share', '')} | {sec.get('word_budget') or '—'} | {sec.get('points_total', 0)} |")
    columns = ["编号", "写法", "阻塞", "质量", "要点覆盖", "待核实", "字数", "请求", "耗时（秒）", "用量（tokens）", "结果",
               *HUMAN_COLUMNS, "修改量", "正文"]
    out += ["", "## 明细", "", "| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        m, sec, method = row["m"], row["section"], row["method"]
        ok = _ok(m)
        text_path = workspace / "texts" / f"{sec['key']}.{method}.md"
        cells = [
            sec["key"], METHODS[method],
            str(m.get("blocking", "—")) if ok else "—", str(m.get("quality", "—")) if ok else "—",
            (f"{m['points_hit']}/{m['points_total']}" if m.get("points_total") else "—") if ok else "—",
            str(m.get("pending", "—")) if ok else "—",
            str(m.get("chars", "—")) if ok else "—",
            str(m.get("usage", {}).get("calls", 0)), str(m.get("seconds", "")), _tokens(m.get("usage", {})),
            _result_cell(method, m),
            *[row["ann"].get(c, "") for c in HUMAN_COLUMNS],
            f"{row['ratio']:.1%}" if row["ratio"] is not None else "",
            f"[原稿]({rel(text_path, report_path.parent)})" if ok else "",
        ]
        out.append("| " + " | ".join(c.replace("|", "／").replace("\n", " ") for c in cells) + " |")

    issues = [(row, i) for row in rows if _ok(row["m"]) for i in row["m"].get("blocking_issues", [])]
    out += ["", "## 剩余阻塞问题", ""]
    if issues:
        out += [f"- {row['section']['key']} {METHODS[row['method']]}：{i}" for row, i in issues]
    else:
        out.append("（无）")
    gaps = sorted({(row["section"]["key"], g) for row in rows for g in row["m"].get("evidence_gaps", [])})
    if gaps:
        out += ["", "资料缺口（选资料时记录）：", ""] + [f"- {k}：{g}" for k, g in gaps]
    out += [
        "", "## 人工标注说明", "",
        "1. 每行的“原稿”是该写法产出的正文；同目录的 `*.edited.md` 是待修改的副本。",
        f"2. {HUMAN_COLUMNS[0]}：正文中关于投标企业的具体事实（资质、人员、业绩、承诺数值等）在全局事实、企业资料中找不到依据的条数。",
        f"3. {HUMAN_COLUMNS[1]}：应标【待核实】或【待填写】、却写成了肯定表述的条数。",
        f"4. {HUMAN_COLUMNS[2]}：“剩余阻塞问题”中实际不成立的条数（用于统计规则误报率）。",
        "5. 修改量：把 `*.edited.md` 改到可以提交的程度并删除第一行标记，汇总时自动计算（字符级增删改数 ÷ 原稿字数）。",
        f"6. 填好后运行 `python scripts/eval_sections.py summarize {rel(report_path)}` 重新汇总并判断门槛；本文件会重写，“备注”一节保留。",
        "", NOTES_HEADING, "", (notes or DEFAULT_NOTES).strip(), "",
    ]
    return "\n".join(out)


def parse_report(text: str) -> Tuple[Optional[str], Dict[Tuple[str, str], Dict[str, str]], str]:
    """(工作目录, 人工标注 {(编号, 写法): {列: 原文}}, 备注)"""
    m = WORKSPACE_TAG.search(text)
    workspace = m.group(1).strip() if m else None
    method_keys = {v: k for k, v in METHODS.items()}
    annotations: Dict[Tuple[str, str], Dict[str, str]] = {}
    header: Optional[List[str]] = None
    for line in text.splitlines():
        if not line.startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells[:2] == ["编号", "写法"]:
            header = cells
            continue
        if header is None or set("".join(cells)) <= set("-: "):
            continue
        row = dict(zip(header, cells))
        method = method_keys.get(row.get("写法", ""))
        if method:
            annotations[(row["编号"], method)] = {c: row.get(c, "") for c in HUMAN_COLUMNS}
    notes = ""
    if NOTES_HEADING in text:
        notes = text.split(NOTES_HEADING, 1)[1].strip()
    return workspace, annotations, notes


def save(results: Dict[str, Any], workspace: Path, report_path: Path,
         annotations: Optional[Dict[Tuple[str, str], Dict[str, str]]] = None, notes: str = "") -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(results, workspace, report_path, annotations, notes), encoding="utf-8")


def next_report_path(now: datetime, report_dir: Path = REPORT_DIR) -> Path:
    path = report_dir / f"{date_label(now)}.md"
    n = 2
    while path.exists():
        path = report_dir / f"{date_label(now)}-{n}.md"
        n += 1
    return path


# ---------------- 命令 ----------------

def _load_facts(path: Optional[str]):
    from app.models.schemas import GlobalFacts

    if not path:
        return GlobalFacts(), "未填写（承诺类数值应为【待填写】）"
    facts = GlobalFacts(**json.loads(Path(path).read_text(encoding="utf-8")))
    filled = [k for k, v in facts.model_dump().items() if v and k != "fact_completeness_mode"]
    return facts, f"取自 {Path(path).name}（{len(filled)} 项）"


def _parse_overrides(specs: List[str], tenders: List[Path]) -> Dict[Path, List[str]]:
    """--section 前缀=sec_1_1,sec_2_3：该招标文件只评估这些章节"""
    out: Dict[Path, List[str]] = {}
    for spec in specs:
        prefix, _, ids = spec.partition("=")
        matches = [t for t in tenders if t.name.startswith(prefix)]
        if len(matches) != 1 or not ids:
            raise SystemExit(f"--section {spec}：格式为 招标文件名前缀=章节ID[,章节ID]，前缀须唯一匹配一份待评估的招标文件")
        out[matches[0]] = [x.strip() for x in ids.split(",") if x.strip()]
    return out


def build_plan(args, log=print) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    """建评估项目并选章节：返回 (tenders, sections, 全局事实说明)"""
    from app.services.checker.section_check import section_points

    paths = resolve_tenders(args.tender or DEFAULT_TENDERS)
    overrides = _parse_overrides(args.section or [], paths)
    facts, facts_label = _load_facts(args.facts)
    tenders, sections = [], []
    for i, path in enumerate(paths, 1):
        log(f"解析 {path.name}")
        project = setup_project(path, facts)
        key = f"T{i}"
        tenders.append({"key": key, "file": path.name, "project_id": project.id,
                        "name": project.tender_analysis.project_name or path.stem,
                        "package": project.tender_analysis.target_package})
        for j, c in enumerate(pick_sections(project, overrides.get(path)), 1):
            node = c["node"]
            sections.append({"key": f"{key}-S{j}", "tender": key, "project_id": project.id, "section_id": node.id,
                             "title": node.title, "category": c["category"], "share": c["share"],
                             "word_budget": node.word_budget, "points_total": len(section_points(project, node))})
    if args.limit:
        sections = sections[:args.limit]
    return tenders, sections, facts_label


def print_plan(tenders, sections, model: str, max_rounds: int, kb_chunks: int) -> int:
    print()
    for t in tenders:
        print(f"{t['key']} {t['name']}（{t['file']}{'，' + t['package'] if t['package'] else ''}）")
        for s in (x for x in sections if x["tender"] == t["key"]):
            print(f"   {s['key']} [{CATEGORIES[s['category']]}] {s['title']}  分值份额 {s['share']}，"
                  f"字数预算 {s['word_budget'] or '—'}，评分要点 {s['points_total']} 条")
    single, refine = max_requests(max_rounds, kb_chunks)
    total = len(sections) * (single + refine)
    print(f"\n模型：{model}；共 {len(sections)} 节 × 2 种写法；每节最多 单次生成 {single} 次 + 智能完善 {refine} 次请求，"
          f"合计最多约 {total} 次（不含截断放大重试与限流重试）")
    return total


def cmd_plan(args) -> None:
    with tempfile.TemporaryDirectory(prefix="easywrite-eval-plan-", ignore_cleanup_errors=True) as tmp:
        workspace = Path(tmp)
        prepare_workspace(workspace, Path(args.data_dir), with_model=False)
        isolate_env(workspace, offline=True)
        tenders, sections, _ = build_plan(args)
        kb_chunks = basis_summary()["kb_chunks"]
        print_plan(tenders, sections, model_label(Path(args.data_dir)), args.max_rounds, kb_chunks)
        from app.db.database import engine

        engine.dispose()  # 释放 SQLite 文件，临时目录才能删除


def cmd_run(args) -> None:
    now = datetime.now()
    workspace = EVAL_ROOT / stamp(now)
    prepare_workspace(workspace, Path(args.data_dir), with_model=True)
    isolate_env(workspace, offline=False)
    from app.core.config import settings
    from app.core.llm_client import llm_client

    (workspace / "ai_settings.json").unlink(missing_ok=True)  # 密钥已载入内存，工作目录不留副本
    if not llm_client.is_configured:
        shutil.rmtree(workspace, ignore_errors=True)
        raise SystemExit("未配置模型：请先在 设置 → 模型 中配置（读取 backend/data/ai_settings.json）")

    tenders, sections, facts_label = build_plan(args)
    basis = basis_summary()
    model = model_label(Path(args.data_dir))
    total = print_plan(tenders, sections, model, args.max_rounds, basis["kb_chunks"])
    if not sections:
        raise SystemExit("没有可评估的章节")
    if not args.yes:
        if not sys.stdin.isatty():
            raise SystemExit("真实模型请求会产生费用：确认后加 --yes 运行")
        if input(f"\n将发起最多约 {total} 次真实模型请求（产生费用）。输入 yes 开始：").strip().lower() != "yes":
            shutil.rmtree(workspace, ignore_errors=True)
            raise SystemExit("已取消")

    report_path = next_report_path(now)
    results: Dict[str, Any] = {
        "version": 1, "date": date_label(now), "started_at": time_label(now), "finished_at": "",
        "config": {"model": model, "max_rounds": args.max_rounds, "budget_calls": settings.REFINE_MAX_CALLS,
                   "budget_seconds": settings.REFINE_MAX_SECONDS, "facts": facts_label},
        "basis": basis, "tenders": tenders, "sections": [],
    }
    print(f"\n报告：{rel(report_path)}；工作目录：{rel(workspace)}")
    try:
        for sec in sections:
            print(f"\n{sec['key']} {sec['title']}")
            entry = dict(sec)
            results["sections"].append(entry)
            for method in METHODS:
                print(f"   {METHODS[method]}")
                try:
                    if method == "single":
                        m = run_single(sec["project_id"], sec["section_id"])
                    else:
                        m = run_refine(sec["project_id"], sec["section_id"], args.max_rounds)
                except KeyboardInterrupt:
                    raise
                except Exception as e:  # 一节出错不影响其他章节
                    m = {"status": "failed", "error": str(e)[:200], "usage": {}, "seconds": 0, "content": ""}
                entry[method] = m
                if _ok(m):
                    write_texts(workspace, sec["key"], method, m["content"])
                blocking = f"阻塞 {m.get('blocking', '—')}，" if method == "single" and _ok(m) else ""
                print(f"      → {_result_cell(method, m)}；{blocking}请求 {m.get('usage', {}).get('calls', 0)} 次，"
                      f"{m.get('seconds', 0)} 秒")
                save(results, workspace, report_path)
        results["finished_at"] = time_label(datetime.now())
    except KeyboardInterrupt:
        print("\n已中断：已完成的章节写入报告（进行中的模型请求结束后进程才会退出）")
    save(results, workspace, report_path)
    print(f"\n报告已写入 {rel(report_path)}。填写人工标注列、修改 {rel(workspace / 'texts')}/*.edited.md 后运行：\n"
          f"  python scripts/eval_sections.py summarize {rel(report_path)}")


def cmd_summarize(args) -> None:
    report_path = Path(args.report).resolve()
    workspace_rel, annotations, notes = parse_report(report_path.read_text(encoding="utf-8"))
    if not workspace_rel:
        raise SystemExit("报告中没有工作目录标记（<!-- eval-workspace: … -->），无法汇总")
    workspace = PROJECT_ROOT / workspace_rel
    results_file = workspace / "results.json"
    if not results_file.exists():
        raise SystemExit(f"找不到 {rel(results_file)}（工作目录已删除？）")
    results = json.loads(results_file.read_text(encoding="utf-8"))
    save(results, workspace, report_path, annotations, notes)
    rows = collect(results, workspace, annotations)
    agg = aggregate(rows)
    checks = gates(agg)
    for name, a, b, ok in checks:
        print(f"{name}：单次生成 {a}，智能完善 {b} → {_mark(ok)}")
    print(verdict(checks, agg["paired"]))
    print(f"已更新 {rel(report_path)}")


def main(argv: Optional[List[str]] = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="阶段 E 效果评估：单次生成 vs 智能完善（真实招标文件）")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("plan", "列出将评估的章节与请求次数上限（不调用模型）"),
                            ("run", "调用模型生成、检查并写报告（产生费用）")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--tender", action="append",
                       help=f"招标文件路径或 {rel(TENDER_DIR)} 下的文件名前缀，可重复；默认 {'、'.join(DEFAULT_TENDERS)}")
        p.add_argument("--section", action="append", help="只评估指定章节：文件名前缀=sec_1_1,sec_2_3（可重复）")
        p.add_argument("--max-rounds", type=int, default=2, choices=range(0, 4), help="智能完善的修订轮数上限（默认 2）")
        p.add_argument("--facts", help="全局事实 JSON（GlobalFacts 字段），应用到每个评估项目；默认不填")
        p.add_argument("--limit", type=int, default=0, help="只评估前 N 节（先小范围试跑）")
        p.add_argument("--data-dir", default=str(SOURCE_DATA_DIR), help="复制运行数据的来源目录（默认 backend/data）")
        if name == "run":
            p.add_argument("--yes", action="store_true", help="不再询问，直接开始（会产生费用）")
    p = sub.add_parser("summarize", help="读取人工标注与改稿，重新汇总并判断门槛")
    p.add_argument("report", help="docs/eval/<日期>.md")
    args = parser.parse_args(argv)
    {"plan": cmd_plan, "run": cmd_run, "summarize": cmd_summarize}[args.command](args)


if __name__ == "__main__":
    main()
