"""
真实模型联调暴露的问题回归（deepseek-flash 推理模型 + 真实招标文件）：
- 推理模型思考过程耗尽 max_tokens → 正文为空：放大额度重试（同步/结构化/流式），超出上限退一档
- 模型输出「未提及（本片段仅…）」带解释的哨兵值、字段类型不符 → 归一与规整，不丢整段
- 模型没给出的单值字段用规则结果补齐
- 生成正文首行重复章节标题 → 去掉
"""
import asyncio
from types import SimpleNamespace

import httpx
from openai import BadRequestError

from app.core.llm_client import llm_client
from app.services.generator.section_generator import strip_title_heading
from app.services.parser.tender_analyzer import coerce_llm_fields, tender_analyzer


def _resp(content, finish_reason="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish_reason,
                                                    message=SimpleNamespace(content=content))])


def _bad_request():
    request = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    return BadRequestError("max_tokens too large", response=httpx.Response(400, request=request), body=None)


def _fake_sync(monkeypatch, plan):
    """plan: max_tokens → 返回值（响应或异常）"""
    seen = []

    def create(**kw):
        seen.append(kw["max_tokens"])
        result = plan[kw["max_tokens"]]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(llm_client, "is_configured", True)
    monkeypatch.setattr(llm_client, "client", SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(llm_client, "max_tokens", 1000)
    return seen


def test_truncated_reasoning_output_is_retried_with_bigger_budget(monkeypatch):
    seen = _fake_sync(monkeypatch, {1000: _resp("", "length"), 4000: _resp("完整正文")})
    assert llm_client.chat_completion("sys", "user") == "完整正文"
    assert seen == [1000, 4000]
    assert llm_client.get_mode() == "llm"


def test_escalation_falls_back_when_model_limit_exceeded(monkeypatch):
    seen = _fake_sync(monkeypatch, {
        1000: _resp('{"a": ', "length"), 4000: _bad_request(), 2000: _resp('{"project_name": "X"}'),
    })
    assert llm_client.chat_completion_structured("sys", "user") == {"project_name": "X"}
    assert seen == [1000, 4000, 2000]


class _Stream:
    def __init__(self, chunks):
        self._chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration:
            raise StopAsyncIteration from None


def _chunk(content=None, finish_reason=None):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish_reason, delta=SimpleNamespace(content=content))])


def test_stream_retries_when_thinking_exhausts_budget(monkeypatch):
    budgets = []

    async def create(**kw):
        budgets.append(kw["max_tokens"])
        if kw["max_tokens"] == 1000:
            return _Stream([_chunk(None), _chunk(None, "length")])  # 只有思考、没有正文
        return _Stream([_chunk("正文"), _chunk("完成", "stop")])

    monkeypatch.setattr(llm_client, "is_configured", True)
    monkeypatch.setattr(llm_client, "max_tokens", 1000)
    monkeypatch.setattr(llm_client, "async_client",
                        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))

    async def collect():
        return [t async for t in llm_client.chat_completion_stream_async("sys", "user")]

    assert asyncio.run(collect()) == ["正文", "完成"], "不得退回离线演示文本"
    assert budgets == [1000, 4000]


def test_coerce_llm_fields_normalizes_sentinels_and_types():
    data = coerce_llm_fields({
        "submission_deadline": "未提及（本片段仅提及投标有效期）",
        "budget_limit": 850,
        "payment_milestones": ["签订后30%", "验收后70%"],
        "qualification_thresholds": "具备CS3级",
        "star_disqualification_items": ["未提及", "★ 须提供原厂授权"],
        "scoring_items": "模型不得写入",
    })
    assert data == {
        "submission_deadline": "未提及",
        "budget_limit": "850",
        "payment_milestones": "签订后30%；验收后70%",
        "qualification_thresholds": ["具备CS3级"],
        "star_disqualification_items": ["★ 须提供原厂授权"],
    }


def test_rule_values_fill_fields_the_model_left_empty(monkeypatch):
    monkeypatch.setattr(llm_client, "is_configured", True)
    monkeypatch.setattr(llm_client, "get_mode", lambda: "llm")
    monkeypatch.setattr(llm_client, "chat_completion_structured", lambda **kw: {
        "project_name": "", "submission_deadline": "未提及（本片段未载明）", "scoring_method": "综合评分法",
    })
    result = tender_analyzer.analyze_text("项目名称：智慧水务运维项目\n投标截止时间：2026年9月30日09:30\n")
    assert result.extraction_mode == "llm"
    assert result.project_name == "智慧水务运维项目"
    assert result.submission_deadline == "2026年9月30日09:30"
    assert result.scoring_method == "综合评分法"


def test_strip_title_heading():
    assert strip_title_heading("#### 1.1 对数字审计的认识\n\n正文", "1.1 对数字审计的认识") == "正文"
    assert strip_title_heading("**第一章 项目理解**\n正文", "第一章 项目理解") == "正文"
    assert strip_title_heading("## 对数字审计的认识\n正文", "1.1 对数字审计的认识") == "正文"
    kept = "#### 1.1.1 审计范式的实质变革\n正文"
    assert strip_title_heading(kept, "1.1 对数字审计的认识") == kept, "子标题不应被删"
    assert strip_title_heading("正文第一行", "1.1 对数字审计的认识") == "正文第一行"


def test_item_points_shared_across_chapters():
    """真实联调：模型把「技术服务方案（20分）」拆成 5 个一级章节，每章都按 20 分计权，挤占了其他评分项的篇幅"""
    from app.models.schemas import OutlineNode, ScoringItem
    from app.services.generator.outline_generator import OutlineGenerator
    items = {
        "x": ScoringItem(id="x", name="技术服务方案", points=20, response_type="proposal"),
        "y": ScoringItem(id="y", name="项目的认识", points=10, response_type="proposal"),
    }
    outline = [OutlineNode(id="sec_1", title="第一章 项目的认识", scoring_item_ids=["y"])] + [
        OutlineNode(id=f"sec_{i}", title=f"第{i}章 方案{i}", scoring_item_ids=["x"]) for i in (2, 3)
    ]
    OutlineGenerator._allocate_budgets(outline, 10000, items, {})
    assert [n.word_budget for n in outline] == [2700, 2700, 2700]


def test_marker_legend_sentence_not_kept_as_red_line(monkeypatch):
    monkeypatch.setattr(llm_client, "is_configured", True)
    monkeypatch.setattr(llm_client, "get_mode", lambda: "llm")
    monkeypatch.setattr(llm_client, "chat_completion_structured", lambda **kw: {"star_disqualification_items": [
        "第二篇：本篇“※”标注的服务需求为符合性审查中的实质性要求，投标文件若不满足按无效投标处理。",
        "※ 须提供7×24小时驻场服务",
    ]})
    result = tender_analyzer.analyze_text("※ 须提供7×24小时驻场服务")
    assert result.star_disqualification_items == ["※ 须提供7×24小时驻场服务"]
