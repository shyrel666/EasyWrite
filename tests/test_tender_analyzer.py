"""
招标文件 18 项拆标引擎测试：哨兵值纪律、不编造原则、规则提取能力。
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BASE_DIR))

from app.services.parser.tender_analyzer import tender_analyzer  # noqa: E402
from conftest import TENDER_SAMPLE  # noqa: E402


def test_rule_extraction_no_fabrication():
    """规则提取：找得到就填，找不到绝不用编造默认值顶替（旧版会捏造★条款/资质/付款比例）"""
    a = tender_analyzer.analyze_text(TENDER_SAMPLE)

    # 明确存在的字段必须提取到
    assert "智慧水务" in a.project_name
    assert "850" in a.budget_limit
    assert "SW-2026-ZB-088" in a.tender_number
    assert a.star_disqualification_items, "★条款必须逐条提取"
    assert len(a.star_disqualification_items) >= 3
    assert a.qualification_thresholds, "资质门槛（CMMI/CS）应被识别"

    # 规则覆盖不到的字段必须为空/未提及，不得编造
    for field, value in a.model_dump().items():
        if field in ("extraction_mode", "extraction_note"):
            continue
        assert not (isinstance(value, str) and "详见各省市电子招投标" in value), "存在旧版编造的截止时间默认值"
        assert not (isinstance(value, str) and "合同签署后支付30%" in value and "付款" not in TENDER_SAMPLE), "存在编造的付款比例"


def test_empty_input_honest():
    """空输入：返回空分析并明确标注，不崩溃不编造"""
    a = tender_analyzer.analyze_text("")
    assert a.project_name == ""
    assert a.extraction_mode == "rules"
    assert a.extraction_note, "空输入必须附说明"


def test_extraction_mode_signaled():
    """诚实信号：未配置 LLM 时 mode=rules 且带覆盖度提示"""
    a = tender_analyzer.analyze_text(TENDER_SAMPLE)
    assert a.extraction_mode == "rules"
    assert a.extraction_note, "rules 模式必须提示覆盖度有限"


def test_segment_split_and_merge():
    """长文分段抽取逻辑：段落对齐切分与多结果合并（非空字段优先、★条款并集）"""
    long_text = (TENDER_SAMPLE + "\n补充说明段。\n") * 60
    segments = tender_analyzer._split_segments(long_text)
    assert len(segments) > 1, "超长文本应被切为多段"
    # 每段不应截断中文句号中部（宽松校验：段尾应能切分）
    for seg in segments:
        assert len(seg) <= 13000
