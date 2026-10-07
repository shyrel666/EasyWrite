"""
独立 Embedding 客户端（OpenAI 兼容协议）。

设计要点：
1. 嵌入服务与生成模型供应商解耦——DeepSeek 无嵌入接口时，
   可单独配置 SiliconFlow BGE-M3 / DashScope / Ollama 等端点。
2. 未配置时 is_available=False，检索管线自动退化为 BM25 + LLM 重排，
   系统仍然完全可用（分层降级策略的语义兜底层）。
3. 批量计算 + 失败即停（不静默吞错，让入库任务显式报告失败原因）。
"""
import logging
import threading
import time
from typing import List, Optional, Tuple

import numpy as np
from openai import OpenAI, AsyncOpenAI

from app.core import llm_usage
from app.core.ai_settings_manager import ai_settings_manager

logger = logging.getLogger("easywrite.embedding")

EMBEDDING_PRESETS = {
    "none": {
        "name": "不启用（BM25 + LLM 重排兜底）",
        "base_url": "",
        "models": [],
    },
    "siliconflow": {
        "name": "硅基流动 SiliconFlow（推荐，注册送额度）",
        "base_url": "https://api.siliconflow.cn/v1",
        "models": ["BAAI/bge-m3", "Qwen/Qwen3-Embedding-8B", "Qwen/Qwen3-Embedding-4B", "Qwen/Qwen3-Embedding-0.6B"],
    },
    "dashscope": {
        "name": "阿里 DashScope",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen3.7-text-embedding", "qwen3.7-text-embedding-flash", "text-embedding-v4"],
    },
    "zhipu": {
        "name": "智谱 BigModel",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["embedding-3"],
    },
    "ollama": {
        "name": "本地 Ollama（内网离线）",
        "base_url": "http://localhost:11434/v1",
        "models": ["bge-m3", "qwen3-embedding:0.6b", "qwen3-embedding:4b", "qwen3-embedding:8b"],
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["text-embedding-3-small", "text-embedding-3-large"],
    },
    "custom": {
        "name": "自定义 OpenAI 兼容接口",
        "base_url": "",
        "models": [],
    },
}

# 单批嵌入上限（条），避免超请求体限制
BATCH_SIZE = 32
# 单条文本截断（字符），BGE-M3 支持 8192 token，中文按 2 字符/token 保守截断
MAX_TEXT_CHARS = 6000


class EmbeddingClient:
    def __init__(self):
        self._lock = threading.Lock()
        self.reload_config()

    def reload_config(self):
        cfg = ai_settings_manager.data
        with self._lock:
            self.api_key = (cfg.get("embedding_api_key") or "").strip()
            self.base_url = (cfg.get("embedding_base_url") or "").strip()
            self.model = (cfg.get("embedding_model") or "").strip()

            self.provider = cfg.get("embedding_provider") or "none"
            if self.provider == "none":
                self.is_available = False
                self.client = None
                self.async_client = None
                return

            if not self.api_key and "localhost" not in self.base_url and "127.0.0.1" not in self.base_url:
                # 云端端点必须提供 Key；本地 Ollama 无需 Key
                self.is_available = False
                self.client = None
                self.async_client = None
                return

            if not self.base_url or not self.model:
                self.is_available = False
                self.client = None
                self.async_client = None
                return

            try:
                self.client = OpenAI(api_key=self.api_key or "ollama", base_url=self.base_url, timeout=60.0, max_retries=0)
                self.async_client = AsyncOpenAI(api_key=self.api_key or "ollama", base_url=self.base_url, timeout=90.0, max_retries=0)
                self.is_available = True
            except Exception as e:
                logger.error("初始化 Embedding 客户端失败: %s", e)
                self.is_available = False
                self.client = None
                self.async_client = None

    def embed_texts_sync(self, texts: List[str]) -> Optional[np.ndarray]:
        """批量同步嵌入，返回 (N, dim) float32 矩阵；不可用或失败返回 None（不静默降级）。"""
        if not self.is_available or not self.client:
            return None
        all_vectors: List[List[float]] = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch = [t[:MAX_TEXT_CHARS] for t in texts[i : i + BATCH_SIZE]]
            started = time.perf_counter()
            try:
                resp = self.client.embeddings.create(input=batch, model=self.model)
                llm_usage.record(purpose="embedding", kind="embedding", model=self.model, started=started,
                                 usage=getattr(resp, "usage", None))
                all_vectors.extend(item.embedding for item in resp.data)
            except Exception as e:
                llm_usage.record(purpose="embedding", kind="embedding", model=self.model, started=started, error=e)
                logger.error("Embedding 批量计算失败 (batch %d): %s", i // BATCH_SIZE, e)
                return None
        if not all_vectors:
            return None
        return np.array(all_vectors, dtype=np.float32)

    def embed_query_sync(self, text: str) -> Optional[np.ndarray]:
        """单条查询嵌入，返回 (dim,) 向量"""
        mat = self.embed_texts_sync([text[:MAX_TEXT_CHARS]])
        if mat is None:
            return None
        return mat[0]

    def describe(self) -> dict:
        """当前嵌入配置摘要（供诊断与前端展示）"""
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "is_available": self.is_available,
            "presets": EMBEDDING_PRESETS,
        }

    def test_connection(self) -> Tuple[bool, str]:
        """连通性探针：嵌入一条短语并校验维度"""
        if not self.is_available:
            return False, "嵌入服务未启用或未配置完整（provider/base_url/model/Key）"
        vec = self.embed_query_sync("连通性测试")
        if vec is None:
            return False, "嵌入接口调用失败，请检查 Key、模型名与网络"
        return True, f"连接成功，向量维度 {len(vec)}"


embedding_client = EmbeddingClient()
