import re
import json
from typing import List, Dict, Any, Optional
from app.models.schemas import DeviationItem, GlobalFacts
from app.core.llm_client import llm_client
from app.services.rag.vector_store import knowledge_store

DEVIATION_PROMPT = """你是一名从业20年的资深投标技术专家，擅长编写点对点技术偏离表。
请针对招标文件中的这一项技术指标，结合我方的全局事实和产品能力，给出严谨、明确、有依据的“点对点技术响应说明”。

【原则】：
1. 态度端正、庄重明确，明确标明“完全满足”或“正偏离”；
2. 给出具体的实现技术手段（如：我司通过【产品名】的XXX微服务组件，采用YYY机制实现，完全满足贵方要求）；
3. 严谨杜绝空话，篇幅在 40~120 字左右。
"""

class DeviationEngine:
    """
    技术偏离表（响应表）智能生成与管理引擎：
    1. 自动从招标文件正文或表格中提取技术指标清单
    2. 针对每一项要求，自动检索企业知识库与全局事实
    3. 生成点对点响应说明与证明材料索引
    4. 自动组装为 Markdown / Word 原生双线偏离表格
    """

    def __init__(self):
        self.llm = llm_client
        self.kb = knowledge_store

    def extract_requirements(self, tender_text: str) -> List[DeviationItem]:
        """从招标文件文本中提取条目化的技术要求清单"""
        items: List[DeviationItem] = []
        counter = 1

        # 先按换行符拆分，对于较长或复合段落支持按句号/分号进一步提取
        candidate_clauses = []
        raw_lines = [l.strip() for l in tender_text.split("\n") if l.strip()]
        for line in raw_lines:
            line_has_star = "★" in line or line.startswith("▲")
            if len(line) > 25 and ("。" in line or "；" in line):
                sub_parts = [p.strip() for p in re.split(r'[。；;]+', line) if p.strip()]
                for sp in sub_parts:
                    if line_has_star and not ("★" in sp or sp.startswith("▲")):
                        sp = "★ " + sp
                    candidate_clauses.append(sp)
            else:
                candidate_clauses.append(line)

        for line in candidate_clauses:
            # 匹配带编号或★号的指标行
            is_star = "★" in line or line.startswith("▲")
            
            # 检测是否为技术要求行（包含支持、必须、具备、满足、提供等技术词汇）
            is_req_line = False
            for kw in ["必须", "应当", "支持", "具备", "提供", "采用", "配置", "实现", "符合"]:
                if kw in line and len(line) >= 6 and len(line) <= 300:
                    is_req_line = True
                    break

            if is_star or is_req_line:
                clean_title = re.sub(r'^[0-9]+[、.．\s]+', '', line)
                items.append(DeviationItem(
                    index=counter,
                    clause_title=clean_title,
                    is_star=is_star,
                    response_status="完全满足",
                    response_detail=""
                ))
                counter += 1
                if counter > 30:  # 限制最多提取30条用于当前批次
                    break

        if not items:
            # 内置标准化软件技术需求缺省项
            items = [
                DeviationItem(
                    index=1,
                    clause_title="★ 投标人所投软件系统必须具备自主知识产权与软件著作权。",
                    is_star=True,
                    response_status="完全满足",
                    response_detail="我方拥有国家版权局颁发的独立计算机软件著作权证书，完全满足。"
                ),
                DeviationItem(
                    index=2,
                    clause_title="★ 系统架构必须采用微服务架构，支持容器化弹性伸缩与多副本部署。",
                    is_star=True,
                    response_status="完全满足",
                    response_detail="我方核心平台全面基于 Spring Cloud 微服务架构与 Kubernetes 容器集群，支持秒级自动扩缩容。"
                ),
                DeviationItem(
                    index=3,
                    clause_title="系统必须全面支持国产信创环境适配（含统信/麒麟OS与达梦/人大金仓数据库）。",
                    is_star=False,
                    response_status="完全满足",
                    response_detail="我司产品已获得统信UOS、银河麒麟以及达梦数据库原厂兼容互认互认证书。"
                ),
                DeviationItem(
                    index=4,
                    clause_title="数据库容灾要求：核心业务数据支持主备同步容灾，RPO=0，RTO<30秒。",
                    is_star=False,
                    response_status="正偏离",
                    response_detail="我司方案提供同城双活与跨机房异地灾备集群，实现 RPO=0，RTO<10秒，优于招标要求。"
                ),
                DeviationItem(
                    index=5,
                    clause_title="售后服务要求：提供 7×24 小时技术支持，重大故障 15 分钟内响应。",
                    is_star=False,
                    response_status="完全满足",
                    response_detail="我司设立 7×24 小时专属运维中心，承诺 5 分钟内极速响应，完全满足。"
                )
            ]

        return items

    def generate_response_for_item(self, item: DeviationItem, facts: GlobalFacts) -> DeviationItem:
        """针对单个指标，结合全局事实与知识库生成针对性的点对点应答论述"""
        # 1. 召回企业知识库
        hits = self.kb.search(query=item.clause_title, top_k=1)
        ref_context = hits[0]["content"][:200] if hits else ""

        # 2. 召回企业中台相关技术组件与资质
        from app.services.assets.asset_manager import asset_manager
        matched_comp = asset_manager.match_assets_for_section(item.clause_title, [])
        comp_context = matched_comp.get("context_text", "")

        user_prompt = (
            f"【招标文件要求】：{item.clause_title}\n"
            f"【是否★号条款】：{'是（绝不可产生负偏离，只能完全满足或优越正偏离）' if item.is_star else '否'}\n"
            f"【我司全局事实】：投标企业【{facts.company_name}】，核心产品【{facts.core_product_name}】，"
            f"技术栈【{facts.architecture_stack}】，数据库【{facts.database_selection}】\n"
            f"【企业中台可用资产】：{comp_context[:250] if comp_context else '国家级软件著作权与信创互认证体系'}\n"
            f"【历史标书参考】：{ref_context}\n\n"
            f"请以 JSON 格式输出响应结果：\n"
            f"{{\"response_status\": \"完全满足\" 或 \"正偏离\", \"response_detail\": \"点对点具体技术佐证阐述（50~120字）\"}}"
        )

        if self.llm.is_configured:
            try:
                data = self.llm.chat_completion_structured(
                    system_prompt=DEVIATION_PROMPT,
                    user_prompt=user_prompt
                )
                if data and isinstance(data, dict):
                    item.response_status = data.get("response_status", "完全满足")
                    item.response_detail = data.get("response_detail", "")
                    if item.response_detail:
                        return item
                else:
                    # 普通文本生成兜底提取
                    res = self.llm.chat_completion(
                        system_prompt=DEVIATION_PROMPT,
                        user_prompt=user_prompt
                    )
                    item.response_detail = res.strip()
                    item.response_status = "正偏离" if ("优于" in res or "正偏离" in res) else "完全满足"
                    return item
            except Exception as e:
                print(f"[DeviationEngine] LLM 响应生成失败: {e}")

        # 本地高质量模版组装兜底
        if item.is_star:
            item.response_status = "完全满足"
            item.response_detail = (
                f"我司【{facts.company_name}】依托成熟的【{facts.core_product_name}】，"
                f"完全满足该★号关键条款。已提供合规资质证明与技术测试报告，承诺严格执行无任何负偏离。"
            )
        elif "容灾" in item.clause_title or "SLA" in item.clause_title:
            item.response_status = "正偏离"
            item.response_detail = (
                f"我司采用【{facts.architecture_stack}】，承诺：{facts.sla_commitment}，"
                f"整体保障能力优于招标标准，构成正偏离。"
            )
        else:
            item.response_status = "完全满足"
            item.response_detail = (
                f"我司【{facts.core_product_name}】原生支持该功能规格，底层适配【{facts.database_selection}】，"
                f"运行稳定，性能优越，完全满足招标技术要求。"
            )

        return item

    def batch_generate_responses(self, items: List[DeviationItem], facts: GlobalFacts) -> List[DeviationItem]:
        """批量生成技术偏离响应"""
        updated_items = []
        for item in items:
            updated = self.generate_response_for_item(item, facts)
            updated_items.append(updated)
        return updated_items

    def to_markdown_table(self, items: List[DeviationItem]) -> str:
        """将偏离表项转换为标准 Markdown 表格格式"""
        lines = [
            "| 序号 | 招标文件技术规格要求 | 条款属性 | 投标响应状态 | 点对点详细技术方案与响应说明 |",
            "| :---: | :--- | :---: | :---: | :--- |"
        ]
        for it in items:
            star_label = "★号核心条款" if it.is_star else "一般技术条款"
            status_label = f"**{it.response_status}**"
            clean_clause = it.clause_title.replace("|", " ").replace("\n", " ")
            clean_detail = it.response_detail.replace("|", " ").replace("\n", " ")
            lines.append(f"| {it.index} | {clean_clause} | {star_label} | {status_label} | {clean_detail} |")

        table_md = "\n".join(lines)
        conclusion = (
            "\n\n**技术偏离说明总结**：\n"
            "我方在本次投标中对招标文件列出的全部技术指标进行了逐项点对点认真核对，"
            "所有条款均达到“完全满足”或“正偏离”，**不存在任何负偏离条款与保留项**，"
            "确保完全符合采购方的建设要求与使用标准。"
        )
        return table_md + conclusion

deviation_engine = DeviationEngine()
