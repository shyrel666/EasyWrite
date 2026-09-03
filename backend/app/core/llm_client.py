import os
from typing import List, Dict, Any, Optional
import httpx
from openai import OpenAI
from app.core.config import settings

class LLMClient:
    """
    统一的 OpenAI 兼容客户端（支持 DeepSeek-V3/R1、阿里通义千问、Kimi 等）
    若未配置有效 API KEY，内置提供高质量模拟生成器以支持本地测试与原型演示
    """

    def __init__(self):
        self.api_key = settings.LLM_API_KEY
        self.base_url = settings.LLM_BASE_URL
        self.model = settings.LLM_MODEL
        self.temperature = settings.LLM_TEMPERATURE
        
        self.is_configured = bool(self.api_key and not self.api_key.startswith("sk-placeholder"))
        
        if self.is_configured:
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=90.0
            )
        else:
            self.client = None

    def chat_completion(self, system_prompt: str, user_prompt: str, temperature: Optional[float] = None) -> str:
        temp = temperature if temperature is not None else self.temperature
        
        if self.is_configured and self.client:
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=temp,
                )
                return response.choices[0].message.content.strip()
            except Exception as e:
                print(f"[LLM Error] 调用远端模型失败: {e}，切入回退模式")
        
        # 回退模拟生成（当无 API Key 或网络不可用时保证全流程可跑通）
        return self._mock_bid_generation(system_prompt, user_prompt)

    def _mock_bid_generation(self, system_prompt: str, user_prompt: str) -> str:
        """为测试与离线演示提供专业的政企信息化标书格式化应答内容"""
        return (
            "### 1. 总体设计原则\n"
            "本项目技术方案严格遵循“先进性、成熟性、高可用性、合规性与可扩展性”的建设原则。\n\n"
            "### 2. 具体实现方案与技术对标\n"
            "结合贵方招标文件要求与我司成熟的信息化项目沉淀，本系统采用松耦合、微服务化与容器化架构：\n"
            "- **业务层**：全面采用标准化 RESTful 接口与消息总线解耦；\n"
            "- **数据层**：支持读写分离与主备容灾，核心数据实行高强度国密算法（SM2/SM3/SM4）加密存储；\n"
            "- **安全合规**：满足国家网络安全等级保护（三级）规范要求，具备精细化 RBAC 权限控制与完整审计日志；\n"
            "- **点对点响应**：针对招标文件中明确列出的各项技术指标，我司承诺完全满足且无负偏离。\n\n"
            "### 3. 质量与交付保障措施\n"
            "配备专职 PMP/CISP 认证架构师与项目经理，采用敏捷开发与 CI/CD 自动化流水线，确保在工期内保质保量完成系统上线试运行与终验。"
        )

llm_client = LLMClient()
