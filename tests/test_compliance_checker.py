"""
合规核查引擎测试：负偏离词快扫、企业名一致性、规则模式覆盖度（LLM 多轮路径需真实 Key）。
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BASE_DIR))

from app.models.schemas import OutlineNode, GlobalFacts  # noqa: E402
from app.services.checker.compliance_checker import compliance_checker  # noqa: E402


def _outline():
    return [
        OutlineNode(
            id="sec_1", title="第一章 总体方案", level=1, path="第一章 总体方案", status="completed",
            content="本方案支持国产信创数据库达梦 DM8，核心数据采用国密 SM4 加密存储，"
                    "系统按照等保三级标准设计并通过测评。",
            children=[
                OutlineNode(
                    id="sec_1_1", title="1.1 架构设计", level=2, status="completed",
                    content="采用微服务架构与容器化部署，实现弹性伸缩。",
                ),
            ],
        ),
    ]


def test_negative_keyword_scan():
    """负偏离敏感词前置快扫：出现'无法满足'等词必须标记 HIGH 风险"""
    outline = _outline()
    outline[0].content += "\n我方暂不支持联邦学习场景，存在负偏离。"
    report = compliance_checker.check_compliance(
        star_items=["★ 核心数据必须支持国密算法加密"],
        outline=outline, facts=GlobalFacts(company_name="北京测试科技有限公司"),
    )
    neg = [r for r in report.risk_items if "负偏离敏感词" in r["item"]]
    assert neg, "负偏离敏感词未被捕获"


def test_company_name_consistency_warning():
    """企业名一致性：正文未出现全局事实企业全称时给出提醒"""
    outline = _outline()
    report = compliance_checker.check_compliance(
        star_items=[], outline=outline,
        facts=GlobalFacts(company_name="从未出现在正文中的企业名有限公司"),
    )
    assert any("企业全称" in w for w in report.warnings), "缺失企业名一致性提醒"


def test_rules_mode_coverage_on_positive_case():
    """规则模式正向：正文覆盖★条款核心关键词时应判满足"""
    outline = _outline()
    report = compliance_checker.check_compliance(
        star_items=["★ 核心数据必须支持国产密码算法 SM4 加密存储"],
        outline=outline, facts=GlobalFacts(),
    )
    assert report.mode == "rules"
    assert report.satisfied_star_items >= 1, "关键词覆盖未识别到满足条款"


def test_no_star_items_handled():
    """无★条款时优雅降级：只做快扫并明确说明"""
    report = compliance_checker.check_compliance(star_items=[], outline=_outline(), facts=GlobalFacts())
    assert report.total_star_items == 0
    assert "未提供★号条款" in report.summary or "未提供" in report.summary
