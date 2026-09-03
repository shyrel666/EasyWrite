import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BASE_DIR))

from app.models.schemas import GlobalFacts, OutlineNode
from app.services.parser.tender_analyzer import tender_analyzer
from app.services.generator.bid_generator import bid_generator
from app.services.checker.compliance_checker import compliance_checker
from app.services.exporter.docx_generator import docx_exporter, DocxStyleConfig

def _get_test_facts_and_content():
    custom_facts = GlobalFacts(
        company_name="北京华泰智联数智科技有限公司",
        credit_code="91110108MA99887766",
        legal_rep="李四",
        registered_capital="8000万元人民币",
        core_product_name="HT-AquaCloud 智慧水务调度中枢v6.0",
        architecture_stack="云原生微服务 + Kubernetes 多活集群",
        database_selection="国产信创达梦数据库 DM8",
        sla_commitment="7×24小时全天候响应，10分钟内出具处置预案",
        delivery_guarantee="100个日历日内保质完成高分交付"
    )

    draft = bid_generator.draft_section(
        section_title="2.1 系统总体技术架构设计",
        section_path="第二章 总体技术架构与方案设计 > 2.1 系统总体技术架构设计",
        requirements=["阐述架构分层", "绘制总体拓扑流向图"],
        custom_instruction="突出智慧水务实时调度场景",
        facts=custom_facts
    )
    return custom_facts, draft["generated_content"]

def test_18_item_tender_analysis():
    print("[1] 测试招标文件 18 项要素结构化提取...")
    sample_rfp_text = """
    项目名称：2026年市级智慧水务一体化综合调度系统建设项目
    项目编号：SW-2026-ZB-088
    采购人：某市水务环境集团有限公司
    最高限价：850.00万元人民币
    工期要求：合同签订后120个日历日内完成系统终验上线
    质保要求：免费提供5年驻场质保及7x24小时运维
    评分办法：综合评分法
    商务分：20分，技术分：50分，价格分：30分
    资质门槛：具备CMMI5级认证或国家信息系统建设和服务能力CS3级
    ★条款1：★ 投标人所投核心调度引擎必须具备国家版权局颁发的独立软件著作权。
    ★条款2：★ 核心数据层必须全面支持国产信创数据库（达梦或人大金仓），并支持国密SM4加密。
    ★条款3：★ 承诺系统发生一级重大调度故障时，10分钟内响应，30分钟内工程师到达现场。
    """
    analysis = tender_analyzer.analyze_text(sample_rfp_text)
    assert "智慧水务" in analysis.project_name
    assert "850" in analysis.budget_limit
    assert len(analysis.star_disqualification_items) >= 2
    print("    -> 18项拆标提取验证通过！")

def test_global_facts_and_mermaid():
    print("\n[2] 测试全局事实约束（Global Facts）与 Mermaid 架构图生成...")
    facts, content = _get_test_facts_and_content()
    assert facts.company_name in content, "未包含全局事实设定的企业全称"
    assert facts.core_product_name in content, "未包含全局事实设定的核心产品名称"
    assert "```mermaid" in content, "未生成规范的 Mermaid 架构图"
    print("    -> 全局事实与 Mermaid 图表验证通过！")

def test_compliance_and_disqualification():
    print("\n[3] 测试废标项与负偏离合规检查引擎（ComplianceChecker）...")
    custom_facts, drafted_content = _get_test_facts_and_content()
    star_items = [
        "★ 投标人所投软件系统必须具备自主知识产权与软件著作权。",
        "★ 系统必须支持国家网络安全等级保护（三级）标准要求。",
        "★ 核心数据存储必须支持国产密码算法（SM2/SM3/SM4）加密。"
    ]

    test_outline = [
        OutlineNode(
            id="sec_1",
            title="第一章 建设方案",
            level=1,
            path="第一章 建设方案",
            content=drafted_content
        )
    ]

    # 正向测试（合规）
    report = compliance_checker.check_compliance(star_items, test_outline, custom_facts)
    assert report.total_star_items == 3

    # 反向风险测试：注入负偏离敏感词
    risky_outline = [
        OutlineNode(
            id="sec_2",
            title="第二章 偏离说明",
            level=1,
            path="第二章 偏离说明",
            content="我司针对第三项要求暂无法满足，存在负偏离。"
        )
    ]
    bad_report = compliance_checker.check_compliance(star_items, risky_outline, custom_facts)
    assert bad_report.passed is False
    assert len(bad_report.risk_items) > 0
    print("    -> 废标项红线合规审查引擎验证通过！")

def test_advanced_word_export():
    print("\n[4] 测试融合全局事实与 Mermaid 题注的高保真 Word 导出...")
    custom_facts, drafted_content = _get_test_facts_and_content()
    test_outline = [
        OutlineNode(
            id="sec_1",
            title="第一章 智慧水务调度平台总体方案",
            level=1,
            path="第一章 智慧水务调度平台总体方案",
            content=drafted_content,
            children=[
                OutlineNode(
                    id="sec_1_1",
                    title="1.1 技术指标偏离响应表",
                    level=2,
                    path="第一章 智慧水务调度平台总体方案 > 1.1 技术指标偏离响应表",
                    content=(
                        "| 条款序号 | 招标文件要求 | 响应状态 | 响应说明 |\n"
                        "| --- | --- | --- | --- |\n"
                        "| ★01 | 具备自主可控独立软著 | 完全满足 | 具备 12 项国家版权局颁发的软著证书 |\n"
                        "| ★02 | 支持国产达梦数据库 | 完全满足 | 已通过达梦原厂兼容互认认证 |\n"
                        "| 03 | 7x24小时响应 | 正偏离 | 提供驻场架构师并在10分钟内响应 |\n"
                    )
                )
            ]
        )
    ]

    style_cfg = DocxStyleConfig(
        template_name="水务集团党政红模板",
        theme_rgb=(192, 0, 0),
        primary_font="宋体",
        heading_font="黑体"
    )

    out_file = docx_exporter.export_project_to_docx(
        project_name="市级智慧水务一体化综合调度系统",
        client_name="某市水务环境集团有限公司",
        outline=test_outline,
        facts=custom_facts,
        style_cfg=style_cfg,
        output_filename="测试输出_智慧水务技术标书(含全局事实与Mermaid).docx"
    )
    assert out_file.exists()
    assert out_file.stat().st_size > 15000
    print("    -> Word 高保真导出验证通过！")

def run_all():
    test_18_item_tender_analysis()
    test_global_facts_and_mermaid()
    test_compliance_and_disqualification()
    test_advanced_word_export()
    print("\n[SUCCESS] 全部高级增强功能端到端验证通过！")

if __name__ == "__main__":
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    run_all()
