import re
import json
import time
import threading
import logging
from typing import List, Dict, Any, Optional, Generator
from openai import OpenAI, AsyncOpenAI, RateLimitError, APIConnectionError

from app.core.config import settings
from app.core.ai_settings_manager import ai_settings_manager

logger = logging.getLogger("easywrite.llm")


class LLMClient:
    """
    统一大模型客户端（OpenAI 兼容协议，支持 DeepSeek / 通义千问 / 硅基流动 / Ollama 等）：
    1. 运行时热加载配置，无需重启服务
    2. 同步 + 异步双客户端（SSE 流式端点使用 AsyncOpenAI，避免阻塞事件循环）
    3. 结构化 JSON 输出（json_object 模式 + 正则兜底）
    4. 指数退避重试
    5. 无 Key 时降级为离线模拟器，并通过 get_mode() 显式暴露真实模式，
       所有上层接口将 mode 透传给前端，杜绝"真假难辨"
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._last_mode: str = "mock"  # "llm" | "mock"，记录最近一次调用的真实执行模式
        self.reload_config()

    def reload_config(self):
        """重新读取最新 AI 配置并重新初始化 OpenAI 客户端"""
        cfg = ai_settings_manager.data
        with self._lock:
            self.api_key = (cfg.get("api_key") or "").strip()
            self.base_url = (cfg.get("base_url") or settings.LLM_BASE_URL).strip()
            self.model = cfg.get("model") or settings.LLM_MODEL
            self.temperature = float(cfg.get("temperature", settings.LLM_TEMPERATURE))
            self.max_tokens = int(cfg.get("max_tokens", 4096))

            self.is_configured = bool(self.api_key and not self.api_key.startswith("sk-placeholder"))

            if self.is_configured:
                try:
                    self.client = OpenAI(
                        api_key=self.api_key,
                        base_url=self.base_url,
                        timeout=120.0,
                        max_retries=0,
                    )
                    self.async_client = AsyncOpenAI(
                        api_key=self.api_key,
                        base_url=self.base_url,
                        timeout=180.0,
                        max_retries=0,
                    )
                except Exception as e:
                    logger.error("初始化 OpenAI 客户端失败: %s", e)
                    self.client = None
                    self.async_client = None
                    self.is_configured = False
            else:
                self.client = None
                self.async_client = None

    # ---------------- 模式信号 ----------------

    def get_mode(self) -> str:
        """返回最近一次生成调用实际使用的模式，供 API 响应透传给前端。"""
        with self._lock:
            return self._last_mode if self.is_configured else "mock"

    def _mark_mode(self, mode: str):
        with self._lock:
            self._last_mode = mode

    # ---------------- 同步调用 ----------------

    def chat_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        temp = temperature if temperature is not None else self.temperature
        tokens = max_tokens if max_tokens is not None else self.max_tokens

        if self.is_configured and self.client:
            for attempt in range(3):
                try:
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=temp,
                        max_tokens=tokens,
                    )
                    content = response.choices[0].message.content
                    if content:
                        self._mark_mode("llm")
                        return content.strip()
                except RateLimitError:
                    logger.warning("LLM 限流，退避重试 (第%d次)", attempt + 1)
                    time.sleep(2 ** attempt)
                except APIConnectionError:
                    logger.warning("LLM 网络抖动，退避重试 (第%d次)", attempt + 1)
                    time.sleep(1.5)
                except Exception as e:
                    logger.error("LLM 调用失败，切入离线模拟: %s", e)
                    break

        self._mark_mode("mock")
        return self._mock_bid_generation(system_prompt, user_prompt)

    def chat_completion_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Generator[str, None, None]:
        """同步流式生成器（供线程池中的同步代码使用）"""
        temp = temperature if temperature is not None else self.temperature
        tokens = max_tokens if max_tokens is not None else self.max_tokens

        if self.is_configured and self.client:
            try:
                stream = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temp,
                    max_tokens=tokens,
                    stream=True,
                )
                produced = False
                for chunk in stream:
                    if chunk.choices:
                        delta = chunk.choices[0].delta
                        if delta and delta.content:
                            produced = True
                            self._mark_mode("llm")
                            yield delta.content
                if produced:
                    return
            except Exception as e:
                logger.error("LLM 流式请求失败，切入本地模拟流: %s", e)

        self._mark_mode("mock")
        fallback_text = self._mock_bid_generation(system_prompt, user_prompt)
        chunk_size = 12
        for i in range(0, len(fallback_text), chunk_size):
            yield fallback_text[i : i + chunk_size]
            time.sleep(0.015)

    # ---------------- 异步调用（SSE 端点专用） ----------------

    async def chat_completion_stream_async(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        """异步流式生成器：在 FastAPI 异步端点中使用，不阻塞事件循环"""
        temp = temperature if temperature is not None else self.temperature
        tokens = max_tokens if max_tokens is not None else self.max_tokens

        if self.is_configured and self.async_client:
            try:
                stream = await self.async_client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temp,
                    max_tokens=tokens,
                    stream=True,
                )
                produced = False
                async for chunk in stream:
                    if chunk.choices:
                        delta = chunk.choices[0].delta
                        if delta and delta.content:
                            produced = True
                            self._mark_mode("llm")
                            yield delta.content
                if produced:
                    return
            except Exception as e:
                logger.error("LLM 异步流式失败，切入本地模拟流: %s", e)

        self._mark_mode("mock")
        import asyncio

        fallback_text = self._mock_bid_generation(system_prompt, user_prompt)
        chunk_size = 12
        for i in range(0, len(fallback_text), chunk_size):
            yield fallback_text[i : i + chunk_size]
            await asyncio.sleep(0.015)

    async def chat_completion_async(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """异步完整生成（供后台任务中的异步代码使用）"""
        temp = temperature if temperature is not None else self.temperature
        tokens = max_tokens if max_tokens is not None else self.max_tokens

        if self.is_configured and self.async_client:
            try:
                response = await self.async_client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temp,
                    max_tokens=tokens,
                )
                content = response.choices[0].message.content
                if content:
                    self._mark_mode("llm")
                    return content.strip()
            except Exception as e:
                logger.error("LLM 异步调用失败，切入离线模拟: %s", e)

        self._mark_mode("mock")
        return self._mock_bid_generation(system_prompt, user_prompt)

    # ---------------- 结构化 JSON 输出 ----------------

    def chat_completion_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = 0.1,
        max_tokens: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        严格结构化 JSON 输出调用。
        返回 None 表示"当前不可用结构化生成"（未配置 LLM）——调用方必须显式处理，
        不得用编造数据顶替。
        """
        if not (self.is_configured and self.client):
            self._mark_mode("mock")
            return None
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt
                        + "\n【重要指令】：必须只输出严格合法的单个 JSON 对象，不要包含任何 markdown 代码块语法或额外解释文字。",
                    },
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens or self.max_tokens,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content.strip()
            self._mark_mode("llm")
            return self._parse_json_loose(raw)
        except Exception:
            # json_object 模式不被支持时退回普通文本 + 正则提取
            try:
                raw_text = self.chat_completion(system_prompt, user_prompt, temperature=temperature)
                m = re.search(r"\{.*\}", raw_text, re.DOTALL)
                if m:
                    return self._parse_json_loose(m.group(0))
            except Exception as ex:
                logger.error("LLM 结构化输出解析失败: %s", ex)
        return None

    @staticmethod
    def _parse_json_loose(raw: str) -> Optional[Dict[str, Any]]:
        """容忍 ```json 围栏与前后杂讯的 JSON 解析"""
        if not raw:
            return None
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    return None
            return None

    # ---------------- 离线模拟器（显式降级） ----------------

    def _mock_bid_generation(self, system_prompt: str, user_prompt: str) -> str:
        """
        离线演示模拟器：仅在未配置 Key 或调用失败时使用。
        上层通过 get_mode() 将 mode="mock" 透传给前端，
        前端会显示"离线演示模式"横幅，明确告知内容非真实生成。
        """
        company_name = "投标人"
        comp_m = (
            re.search(r"投标人官方全称[：:]\s*([^\n\r]+)", user_prompt)
            or re.search(r"投标企业主体[：:]\s*([^\n\r]+)", user_prompt)
            or re.search(r"投标企业[：:]\s*([^\n\r]+)", user_prompt)
            or re.search(r"企业全称.*?[：:]([^\n\r]+)", user_prompt)
        )
        if comp_m:
            company_name = comp_m.group(1).strip().replace("【", "").replace("】", "")

        product_name = "核心技术平台"
        prod_m = (
            re.search(r"投标核心产品平台[：:]\s*([^\n\r]+)", user_prompt)
            or re.search(r"核心产品线[：:]\s*([^\n\r]+)", user_prompt)
            or re.search(r"核心产品[：:]\s*([^\n\r]+)", user_prompt)
        )
        if prod_m:
            product_name = prod_m.group(1).strip().replace("【", "").replace("】", "")

        return (
            "### 1. 方案设计思路与技术路线\n"
            f"针对本项目技术需求，投标人【{company_name}】依托成熟的企业级产品【{product_name}】，"
            "严格按照国家与行业最高标准进行总体方案设计。\n\n"
            "### 2. 总体架构拓扑图\n"
            "系统涵盖终端接入层、API 智能网关层、微服务核心应用层与持久化存储层，拓扑关系如下：\n\n"
            "```mermaid\n"
            "graph TD\n"
            "    User[终端用户 / 业务前台] --> Gateway[API智能网关 / 安全鉴权中心]\n"
            "    Gateway --> Svc1[核心业务微服务群]\n"
            "    Gateway --> Svc2[数据治理与共享服务]\n"
            "    Svc1 --> DB[(主备高可用数据库集群)]\n"
            "    Svc2 --> DB\n"
            "```\n\n"
            "### 3. 点对点技术指标响应与承诺\n"
            "- **合规性承诺**：系统完全符合国家网络安全等级保护（三级）标准，支持国密算法（SM2/SM3/SM4）加密传输与存储。\n"
            "- **技术性能指标**：系统支持并发请求 > 10,000 TPS，核心接口平均延时 < 150ms，RPO=0，RTO < 10秒。\n"
            "- **交付与售后**：我司承诺按期保质交付，并提供 7×24 小时原厂级技术支撑与免费驻场维保。\n\n"
            "（注：当前为离线演示内容，请在【AI 设置】中配置模型 API Key 后重新生成正式内容。）"
        )


llm_client = LLMClient()
