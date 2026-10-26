"""压缩计划只给出可审阅的编辑指令，不以截断正文满足预算。"""
from collections import Counter


def compression_plan(report, text):
    points = [p.point for p in report.points]
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip() and '```' not in p]
    repeated = [p[:100] for p, count in Counter(paragraphs).items() if count > 1]
    return [
        f"压缩计划：正文当前 {report.char_count} 字，目标 {report.word_budget} 字，超出 {report.excess_chars} 字；图表代码 {report.diagram_chars} 字符单独计量。",
        "必须保留评分要点：" + ("；".join(points) or "大纲要求中的全部响应内容"),
        "必须保留企业已确认事实、分级承诺的主体/级别/场景、证明引用和待核实标注。",
        "先删除无关背景、套话和重复总结，再合并重复段与相同流程；保留具体措施、责任和验收方式。不得截断句子、删除评分点或以删图抵消正文超限。",
        "优先合并的重复段：" + ("；".join(repeated) or "逐段检查同义重复与重复列表"),
    ]
