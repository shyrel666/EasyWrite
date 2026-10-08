"""
单章节"写—查—改"闭环（智能完善，后台任务 section_refine）：
读取依据 → 选择资料 → 起草（正文为空时；否则以当前正文为原稿）→ 检查 → 定向修订（默认最多 2 轮）→ 候选稿

- 每一版（起草稿、每轮修订稿）都存为带规则检查报告的候选稿（origin refine_draft / refine，parent_id 串成链），
  本任务不直接写正文。唯一例外：勾选"空白章节完成后直接写入"且目标达成时，走候选稿采纳流程（幂等，校验正文与依据）
- 修订的输入：上一版、问题清单、同一份资料集合、全局事实；问题是否解决以规则检查（C1）对修订稿的重新检查为准，不看模型自述
- 下一步由 policy.decide 决定：继续（逐条 / 换方法 / 只处理阻塞问题）、接受或停止
- 运行预算（run_budget）：模型请求次数与时长上限；超限以"部分完成 / 预算用尽"结束，已有候选稿保留
- 继续执行：从最后一版候选稿接着修订，已用轮数与轮数上限取自候选稿链，不会重新获得修订额度
"""
import json
import logging
import threading
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.core import llm_usage
from app.core.config import settings
from app.core.run_budget import BudgetExceeded, RunBudget, current_run
from app.models.schemas import SectionCheckReport
from app.db.models import SectionProposal
from app.services.checker.section_check import check_section, revision_issues
from app.services.generator.evidence_set import EvidenceSet, select_evidence
from app.services.generator.section_generator import section_generator, writing_inputs
from app.services.project_store import find_node, project_store
from app.services.proposals import REFINE_ORIGINS, apply_proposal, proposal_store, propose
from app.services.refine.policy import DEFAULT_MAX_ROUNDS, MODE_LABELS, Action, counts, decide

logger = logging.getLogger("easywrite.refine")

OUTCOME_LABELS = {"goal_met": "已达成检查目标", "partial": "部分完成", "no_output": "未产出候选稿"}
STOP_LABELS = {
    "goal_met": "目标达成", "no_improvement": "修订没有进展", "rounds_exhausted": "修订轮数用完",
    "insufficient_evidence": "资料不足", "budget_exhausted": "预算用尽", "cancelled": "已取消", "error": "运行出错",
}

# 同一章节同时只运行一个智能完善（各版候选稿会互相取代）
_running: set = set()
_running_lock = threading.Lock()


def claim(project_id: str, section_id: str) -> bool:
    with _running_lock:
        if (project_id, section_id) in _running:
            return False
        _running.add((project_id, section_id))
        return True


def release(project_id: str, section_id: str) -> None:
    with _running_lock:
        _running.discard((project_id, section_id))


def resume_tip(project_id: str, section_id: str) -> Optional[SectionProposal]:
    """可继续执行的候选稿：本节最新一份待处理的智能完善候选稿（已采纳、放弃或被其他候选稿取代的不能继续）"""
    for row in proposal_store.list(project_id, section_id, open_only=True):
        if row.origin in REFINE_ORIGINS:
            return row
    return None


def evidence_gaps(evidence: EvidenceSet) -> List[str]:
    """资料缺口：如实告诉用户哪些依据缺失（正文中相应位置应为【待填写】【待核实】）"""
    gaps = []
    if not evidence.refs:
        gaps.append("知识库中没有本节的高置信参考")
    if "尚未录入" in (evidence.asset_context or ""):
        gaps.append("本节需要的企业资料（人员、资质或业绩）尚未录入")
    if evidence.excluded_assets:
        gaps.append(f"{len(evidence.excluded_assets)} 条企业资料因证书过期或所属主体不符被排除，未写入")
    return gaps


class _Cancelled(Exception):
    pass


def _loads(raw: str) -> Dict[str, Any]:
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


class RefineRun:
    def __init__(self, ctx, project_id: str, section_id: str, budget: RunBudget):
        self.ctx = ctx
        self.project_id = project_id
        self.section_id = section_id
        self.budget = budget
        self.base = ""                       # draft 起草 / current 当前正文 / resume 继续执行
        self.started_blank = False
        self.instruction = ""
        self.max_rounds = DEFAULT_MAX_ROUNDS
        self.apply_if_blank = False
        self.rounds_used = 0
        self.base_revision: Optional[int] = None
        self.base_counts: Optional[Dict[str, int]] = None
        self.reports: List[Any] = []
        self.last_report: Optional[SectionCheckReport] = None
        self.history: List[Dict[str, Any]] = []
        self.proposals: List[str] = []       # 本次运行产生的候选稿
        self.final_id: Optional[str] = None  # 最后一版候选稿（继续执行时可能是此前运行产生的）
        self.parent = ""
        self.base_text = ""
        self.evidence: Optional[EvidenceSet] = None
        self.project = None
        self.node = None
        self.prompt_kw: Dict[str, Any] = {}
        self.outcome = "no_output"
        self.stop_reason = ""
        self.message = ""
        self.applied = False
        self.apply_error = ""

    # ---------------- 步骤 ----------------

    def check_cancel(self):
        if self.ctx.cancelled():
            raise _Cancelled()

    def _progress(self, round_no: int, phase: float) -> int:
        """起草与检查占 0–45%，各轮修订平分 45–95%"""
        span = 50 / max(1, self.max_rounds)
        return int(45 + span * (round_no - 1 + phase))

    def _record(self, label: str, report: Any, round_no: int, mode: str, proposal_id: Optional[str]):
        blocking, quality = counts(report)
        self.history.append({"round": round_no, "label": label, "mode": mode, "proposal_id": proposal_id,
                             "blocking_count": blocking, "quality_count": quality})

    def _save(self, content: str, round_no: int, mode: str, origin: str):
        """存为候选稿（自动做规则检查）；本版成为下一轮修订的原稿"""
        meta = {"round": round_no, "max_rounds": self.max_rounds, "mode": mode,
                "instruction": self.instruction, "apply_if_blank": self.apply_if_blank}
        if origin == "refine" and not self.parent and self.base_counts:
            meta["base"] = self.base_counts  # 以当前正文为原稿：记下原稿的检查结果，继续执行时据此判断进展
        proposal = propose(self.project, self.node, content, self.evidence, origin=origin,
                           task_id=self.ctx.task_id, parent_id=self.parent, refine=meta,
                           base_revision=self.base_revision)
        self.proposals.append(proposal["id"])
        self.final_id = self.parent = proposal["id"]
        self.base_text = content
        if proposal["status"] != "checked" or not proposal.get("report"):
            raise RuntimeError("候选稿规则检查失败，已保存为未检查的候选稿")
        report = SectionCheckReport.model_validate(proposal["report"])
        self.reports.append(report)
        self.last_report = report
        if origin == "refine":
            self.rounds_used = round_no
        label = "起草稿" if origin == "refine_draft" else f"第 {round_no} 轮修订"
        self._record(label, report, round_no, mode, proposal["id"])

    def _load_resume(self, instruction: str):
        tip = resume_tip(self.project_id, self.section_id)
        if tip is None:
            raise RuntimeError("没有可继续的智能完善候选稿（已采纳、放弃或被取代），请重新发起")
        chain = proposal_store.chain(self.project_id, tip.id)
        meta = _loads(tip.refine_json)
        self.base = "resume"
        self.max_rounds = int(meta.get("max_rounds", self.max_rounds))
        self.rounds_used = int(meta.get("round", 0))
        self.instruction = meta.get("instruction", "") or instruction
        self.apply_if_blank = bool(meta.get("apply_if_blank", False))
        self.base_revision = tip.base_revision
        self.started_blank = chain[0].origin == "refine_draft"
        root_meta = _loads(chain[0].refine_json)
        if chain[0].origin == "refine" and root_meta.get("base"):
            self.base_counts = root_meta["base"]
            self.reports.append(self.base_counts)
            self._record("当前正文", self.base_counts, 0, "", None)
        for row in chain:
            report = _loads(row.report_json)
            row_meta = _loads(row.refine_json)
            if not report:
                continue
            self.reports.append(report)
            label = "起草稿" if row.origin == "refine_draft" else f"第 {row_meta.get('round', '?')} 轮修订"
            self._record(label, report, int(row_meta.get("round", 0)), row_meta.get("mode", ""), row.id)
        self.final_id = self.parent = tip.id
        self.base_text = tip.content
        if tip.report_json:
            self.last_report = SectionCheckReport.model_validate_json(tip.report_json)
        return tip

    def execute(self, instruction: str, max_rounds: int, apply_if_blank: bool, resume: bool):
        ctx = self.ctx
        ctx.report(5, "读取依据")
        project = project_store.get(self.project_id)
        node = find_node(project.outline, self.section_id) if project else None
        if node is None:
            raise RuntimeError("章节已删除，未执行智能完善")
        self.project, self.node = project, node
        self.instruction, self.max_rounds, self.apply_if_blank = instruction.strip(), max_rounds, apply_if_blank
        pending_instruction = False
        tip = None
        if resume:
            tip = self._load_resume(self.instruction)
            # 继续执行时另填了新的补充要求：本次至少按它修订一轮（轮数仍受原上限约束）
            pending_instruction = bool(instruction.strip()) and instruction.strip() != _loads(tip.refine_json).get("instruction", "")
            if pending_instruction:
                self.instruction = instruction.strip()
        else:
            self.base_revision = node.revision
            self.started_blank = not node.content.strip()

        self.check_cancel()
        ctx.report(12, "选择资料")
        evidence_kw, self.prompt_kw = writing_inputs(project, node, custom_instruction=self.instruction)
        self.evidence = select_evidence(project, node, **evidence_kw)
        self.check_cancel()

        if resume:
            if self.last_report is None:  # 最后一版此前未完成检查：先检查
                ctx.report(30, "检查最后一版候选稿")
                self.last_report = check_section(project, node, tip.content, self.evidence)
                self.reports.append(self.last_report)
            ctx.report(40, f"继续执行：已用 {self.rounds_used}/{self.max_rounds} 轮修订")
        elif self.started_blank:
            self.base = "draft"
            ctx.report(25, "起草")
            res = section_generator.draft_section(evidence=self.evidence, purpose="section_refine_draft", **self.prompt_kw)
            if res.get("mode") != "llm" or not res["generated_content"].strip():
                raise RuntimeError("模型调用失败，未能起草")
            self.check_cancel()
            ctx.report(40, "检查起草稿")
            self._save(res["generated_content"], 0, "draft", "refine_draft")
        else:
            self.base = "current"
            ctx.report(30, "检查当前正文")
            report = check_section(project, node, node.content, self.evidence)
            self.base_counts = {"blocking_count": report.blocking_count, "quality_count": report.quality_count}
            self.reports.append(report)
            self.last_report = report
            self._record("当前正文", report, 0, "", None)
            self.base_text = node.content
            pending_instruction = bool(self.instruction)

        while True:
            self.check_cancel()
            action = decide(self.reports, self.rounds_used, self.max_rounds)
            forced = pending_instruction and action.kind == "accept" and self.rounds_used < self.max_rounds
            if action.kind != "revise" and not forced:
                self._finish(action)
                return
            mode = action.mode if action.kind == "revise" else "normal"
            issues = revision_issues(self.last_report, self.instruction if pending_instruction else "",
                                     blocking_only=mode == "minimal")
            pending_instruction = False
            if not issues:
                self._finish(action)
                return
            round_no = self.rounds_used + 1
            ctx.report(self._progress(round_no, 0),
                       f"第 {round_no} 轮修订（共 {self.max_rounds} 轮，{MODE_LABELS[mode]}）：处理 {len(issues)} 个问题")
            res = section_generator.revise_section(evidence=self.evidence, base_text=self.base_text, issues=issues,
                                                   revision_mode=mode, **self.prompt_kw)
            if res.get("mode") != "llm" or not res["generated_content"].strip():
                raise RuntimeError(f"模型调用失败，第 {round_no} 轮修订未完成")
            self.check_cancel()
            ctx.report(self._progress(round_no, 0.8), f"检查第 {round_no} 轮修订稿")
            self._save(res["generated_content"], round_no, mode, "refine")

    # ---------------- 结束 ----------------

    def _finish(self, action: Action):
        self.outcome, self.stop_reason, self.message = action.outcome, action.stop_reason, action.message
        if self.final_id is None:
            if action.outcome == "goal_met":
                self.message = "当前正文已达成检查目标，无需修订"
            else:
                self.outcome = "no_output"
            return
        blocking_missing = any(i.level == "blocking" and i.code == "missing_point"
                               for i in (self.last_report.issues if self.last_report else []))
        gaps = evidence_gaps(self.evidence) if self.evidence else []
        if (self.outcome == "partial" and self.stop_reason in ("no_improvement", "rounds_exhausted")
                and blocking_missing and not self.evidence.refs and not self.evidence.assets):
            self.stop_reason = "insufficient_evidence"
            self.message += "；资料不足：" + "、".join(gaps) + "，未写到的评分要点需补充资料后再完善"
        if self.outcome == "goal_met" and self.apply_if_blank and self.started_blank:
            self._apply_blank()

    def _apply_blank(self):
        """空白章节、目标达成：走候选稿采纳流程写入正文（幂等；用户期间写了正文或依据有变化时不写入）"""
        try:
            apply_proposal(self.project_id, self.section_id, self.final_id)
            self.applied = True
        except HTTPException as e:
            detail = e.detail if isinstance(e.detail, dict) else {"message": str(e.detail)}
            self.apply_error = f"未直接写入：{detail.get('message', '')}；候选稿已保留，可查看后采纳"

    def stop(self, reason: str, message: str):
        self.stop_reason, self.message = reason, message
        self.outcome = "partial" if self.final_id else "no_output"

    def result(self) -> Dict[str, Any]:
        report = self.last_report
        return {
            "section_id": self.section_id,
            "outcome": self.outcome,
            "stop_reason": self.stop_reason,
            "message": self.message,
            "final_proposal_id": self.final_id,
            "proposal_ids": self.proposals,
            "base": self.base,
            "rounds": self.rounds_used,
            "max_rounds": self.max_rounds,
            "history": self.history,
            "blocking_count": report.blocking_count if report else None,
            "quality_count": report.quality_count if report else None,
            "unresolved": [i.model_dump() for i in report.issues] if report and self.final_id else [],
            "pending_verification": len(report.pending_verification) if report and self.final_id else 0,
            "evidence_gaps": evidence_gaps(self.evidence) if self.evidence else [],
            "budget": self.budget.snapshot(),
            "usage": llm_usage.run_totals(self.ctx.task_id),
            "applied": self.applied,
            "apply_error": self.apply_error,
        }


def run_refine(
    ctx, project_id: str, section_id: str, *,
    instruction: str = "", max_rounds: int = DEFAULT_MAX_ROUNDS, apply_if_blank: bool = False, resume: bool = False,
) -> Dict[str, Any]:
    """智能完善的任务函数（task_manager.submit 的 fn）。返回结果中 outcome / stop_reason 说明是否达成目标及原因"""
    budget = RunBudget(ctx.task_id, settings.REFINE_MAX_CALLS, settings.REFINE_MAX_SECONDS)
    token = current_run.set(budget)
    run = RefineRun(ctx, project_id, section_id, budget)
    try:
        run.execute(instruction, max_rounds, apply_if_blank, resume)
    except BudgetExceeded as e:
        run.stop("budget_exhausted", f"预算用尽：{e.message}，已产生的候选稿保留，可点\"继续\"接着执行")
    except _Cancelled:
        run.stop("cancelled", "已取消，已产生的候选稿保留")
    except Exception as e:
        if run.final_id is None:
            raise  # 什么都没产出：任务按失败结束
        logger.exception("智能完善中途出错 %s/%s", project_id, section_id)
        run.stop("error", f"运行出错：{e}；已产生的候选稿保留")
    finally:
        current_run.reset(token)
    result = run.result()
    head = OUTCOME_LABELS.get(result["outcome"], result["outcome"])
    ctx.report(99, f"{head}：{result['message']}" if result["message"] else head)
    return result
