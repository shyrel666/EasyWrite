import re
import json
import time
import threading
import logging
import contextvars
from typing import List, Dict, Any, Optional, Generator
from openai import OpenAI, AsyncOpenAI, RateLimitError, APIConnectionError, BadRequestError

from app.core import llm_usage
from app.core.config import settings
from app.core.ai_settings_manager import ai_settings_manager

logger = logging.getLogger("easywrite.llm")

# 推理模型（如 deepseek-flash / deepseek-v4-pro）的思考过程同样占用 max_tokens：
# 输出被截断（finish_reason == "length"）时按以下倍数放大额度重试；超出模型上限时退一档
ESCALATION_FACTORS = (4, 2)

# 当前请求 / 后台任务上下文中最近一次调用的实际模式。同步接口与 task_manager 任务都在复制的上下文中执行，
# 批量撰写与流式生成同时进行时各自读到自己的模式，不会互相串扰
_context_mode: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("llm_context_mode", default=None)


class LLMClient:
    """
    统一大模型客户端（OpenAI 兼容协议，支持 DeepSeek / 通义千问 / 硅基流动 / Ollama 等）：
    1. 运行时热加载配置，无需重启服务
    2. 同步 + 异步双客户端（SSE 流式端点使用 AsyncOpenAI，避免阻塞事件循环）
    3. 结构化 JSON 输出（json_object 模式 + 正则兜底）
    4. 指数退避重试
    5. 无 Key 时降级为离线模拟器，并通过 get_mode() 显式暴露真实模式，
       所有上层接口将 mode 透传给前端，杜绝"真假难辨"
    6. 每次真实请求写入调用记录（llm_usage：耗时、用量、用途），purpose 标明调用用途
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._last_mode: Optional[str] = None  # "llm" | "mock"；None 表示当前配置尚未生成
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
            # 流式请求附带 stream_options.include_usage 以拿到用量；服务商不支持时自动关闭
            self._stream_usage = True

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

            # 新配置尚未执行生成；不能沿用旧配置的模拟结果，也不能默认成一次模拟调用。
            self._last_mode = None
            _context_mode.set(None)

    # ---------------- 模式信号 ----------------

    def get_mode(self) -> Optional[str]:
        """
        返回最近一次生成的实际模式：优先取当前请求/任务上下文中的调用结果，
        本上下文还没有调用时取全局最近一次 last_mode()；已配置但尚未生成时返回 None。
        """
        mode = _context_mode.get() if self.is_configured else None
        return mode if mode is not None else self.last_mode()

    def last_mode(self) -> Optional[str]:
        """全局最近一次生成的实际模式（不区分请求，状态接口用）；已配置但尚未生成时返回 None。"""
        with self._lock:
            return self._last_mode if self.is_configured else "mock"

    def _mark_mode(self, mode: str):
        _context_mode.set(mode)
        with self._lock:
            self._last_mode = mode

    # ---------------- 同步调用 ----------------

    def _create(self, purpose: str, messages: List[Dict[str, str]], temperature: float, tokens: int, **extra):
        """一次非流式模型请求，记录耗时、用量与结束原因"""
        started = time.perf_counter()
        try:
            response = self.client.chat.completions.create(
                model=self.model, messages=messages, temperature=temperature, max_tokens=tokens, **extra
            )
        except Exception as e:
            llm_usage.record(purpose=purpose, kind="chat", model=self.model, started=started, max_tokens=tokens, error=e)
            raise
        choice = response.choices[0]
        llm_usage.record(
            purpose=purpose, kind="chat", model=self.model, started=started, max_tokens=tokens,
            usage=getattr(response, "usage", None), finish_reason=choice.finish_reason or "",
            empty=not (choice.message.content or "").strip(),
        )
        return response

    def _create_escalating(
        self, messages: List[Dict[str, str]], temperature: float, tokens: int, purpose: str = "", **extra
    ) -> str:
        """调用模型；若被 max_tokens 截断则放大额度重试一次，返回最终正文（可能为空串）"""
        response = self._create(purpose, messages, temperature, tokens, **extra)
        choice = response.choices[0]
        content = choice.message.content or ""
        if choice.finish_reason != "length":
            return content
        logger.warning("模型输出被 max_tokens=%d 截断（推理模型的思考过程同样占用额度），放大额度重试", tokens)
        for factor in ESCALATION_FACTORS:
            try:
                response = self._create(purpose, messages, temperature, tokens * factor, **extra)
            except BadRequestError as e:
                logger.warning("max_tokens=%d 超出模型上限，退一档：%s", tokens * factor, e)
                continue
            choice = response.choices[0]
            if choice.finish_reason == "length":
                logger.warning("放大到 max_tokens=%d 仍被截断，请在系统设置中调高单次最大 Token", tokens * factor)
            return choice.message.content or ""
        return content

    def chat_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        purpose: str = "",
    ) -> str:
        temp = temperature if temperature is not None else self.temperature
        tokens = max_tokens if max_tokens is not None else self.max_tokens

        if self.is_configured and self.client:
            for attempt in range(3):
                try:
                    content = self._create_escalating(
                        [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                        temp, tokens, purpose=purpose,
                    )
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
        purpose: str = "",
    ) -> Generator[str, None, None]:
        """同步流式生成器（供线程池中的同步代码使用）"""
        temp = temperature if temperature is not None else self.temperature
        tokens = max_tokens if max_tokens is not None else self.max_tokens

        if self.is_configured and self.client:
            produced = False
            try:
                meter = llm_usage.StreamMeter(purpose, self.model, tokens)
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
                    for chunk in stream:
                        content = meter.on_chunk(chunk)
                        if content:
                            produced = True
                            self._mark_mode("llm")
                            yield content
                except BaseException as e:
                    meter.finish(error=e)
                    raise
                meter.finish()
                if produced:
                    return
            except Exception as e:
                if produced:
                    # 已输出部分真实内容：不得拼接模拟文本冒充完整结果，交由调用方按失败处理
                    raise RuntimeError(f"模型流式输出中断：{e}") from e
                logger.error("LLM 流式请求失败，切入本地模拟流: %s", e)

        self._mark_mode("mock")
        fallback_text = self._mock_bid_generation(system_prompt, user_prompt)
        chunk_size = 12
        for i in range(0, len(fallback_text), chunk_size):
            yield fallback_text[i : i + chunk_size]
            time.sleep(0.015)

    # ---------------- 异步调用（SSE 端点专用） ----------------

    async def _open_stream_async(self, purpose: str, messages: List[Dict[str, str]], temperature: float, budget: int):
        """发起异步流式请求；优先附带 include_usage，服务商拒绝时去掉重试并在本配置下不再携带"""
        kwargs = dict(model=self.model, messages=messages, temperature=temperature, max_tokens=budget, stream=True)
        if not self._stream_usage:
            return await self.async_client.chat.completions.create(**kwargs)
        started = time.perf_counter()
        try:
            return await self.async_client.chat.completions.create(**kwargs, stream_options={"include_usage": True})
        except BadRequestError as e:
            llm_usage.record(purpose=purpose, kind="stream", model=self.model, started=started, max_tokens=budget, error=e)
        stream = await self.async_client.chat.completions.create(**kwargs)
        logger.info("服务商不支持 stream_options.include_usage，流式调用不再统计用量")
        self._stream_usage = False
        return stream

    async def chat_completion_stream_async(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        purpose: str = "",
    ):
        """异步流式生成器：在 FastAPI 异步端点中使用，不阻塞事件循环"""
        temp = temperature if temperature is not None else self.temperature
        tokens = max_tokens if max_tokens is not None else self.max_tokens

        if self.is_configured and self.async_client:
            produced = False
            budgets = [tokens] + [tokens * f for f in ESCALATION_FACTORS]
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            try:
                for budget in budgets:
                    meter = llm_usage.StreamMeter(purpose, self.model, budget)
                    try:
                        stream = await self._open_stream_async(purpose, messages, temp, budget)
                    except BadRequestError as e:
                        meter.finish(error=e)
                        if budget == tokens:
                            raise
                        logger.warning("max_tokens=%d 超出模型上限，退一档：%s", budget, e)
                        continue
                    except Exception as e:
                        meter.finish(error=e)
                        raise
                    try:
                        async for chunk in stream:
                            content = meter.on_chunk(chunk)
                            if content:
                                produced = True
                                self._mark_mode("llm")
                                yield content
                    except BaseException as e:
                        # 含客户端断开（取消）与流中断：记录后照常向上抛
                        meter.finish(error=e)
                        raise
                    meter.finish()
                    if produced:
                        if meter.finish_reason == "length":
                            logger.warning("流式正文被 max_tokens=%d 截断，请在系统设置中调高单次最大 Token", budget)
                        return
                    if meter.finish_reason != "length":
                        break
                    logger.warning("思考过程耗尽 max_tokens=%d、未产出正文，放大额度重试", budget)
            except Exception as e:
                if produced:
                    raise RuntimeError(f"模型流式输出中断：{e}") from e
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
        purpose: str = "",
    ) -> str:
        """异步完整生成（供后台任务中的异步代码使用）"""
        temp = temperature if temperature is not None else self.temperature
        tokens = max_tokens if max_tokens is not None else self.max_tokens

        if self.is_configured and self.async_client:
            started = time.perf_counter()
            try:
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
                except Exception as e:
                    llm_usage.record(purpose=purpose, kind="chat", model=self.model, started=started,
                                     max_tokens=tokens, error=e)
                    raise
                choice = response.choices[0]
                content = choice.message.content
                llm_usage.record(
                    purpose=purpose, kind="chat", model=self.model, started=started, max_tokens=tokens,
                    usage=getattr(response, "usage", None), finish_reason=choice.finish_reason or "",
                    empty=not (content or "").strip(),
                )
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
        purpose: str = "",
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
            raw = self._create_escalating(
                [
                    {
                        "role": "system",
                        "content": system_prompt
                        + "\n【重要指令】：必须只输出严格合法的单个 JSON 对象，不要包含任何 markdown 代码块语法或额外解释文字。",
                    },
                    {"role": "user", "content": user_prompt},
                ],
                temperature, max_tokens or self.max_tokens, purpose=purpose,
                response_format={"type": "json_object"},
            ).strip()
            self._mark_mode("llm")
            return self._parse_json_loose(raw)
        except Exception:
            # json_object 模式不被支持时退回普通文本 + 正则提取
            try:
                raw_text = self.chat_completion(system_prompt, user_prompt, temperature=temperature, purpose=purpose)
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
            "### 3. 点对点技术指标响应\n"
            "- **合规性**：等级保护级别与密码应用要求按招标文件执行，具体级别【待填写】。\n"
            "- **技术性能指标**：并发能力、接口时延、RPO/RTO 等指标【待填写】。\n"
            "- **交付与售后**：工期【待填写】；售后响应时限与驻场安排【待填写】。\n\n"
            "（注：当前为离线演示内容，请在【AI 设置】中配置模型 API Key 后重新生成正式内容。）"
        )


llm_client = LLMClient()
