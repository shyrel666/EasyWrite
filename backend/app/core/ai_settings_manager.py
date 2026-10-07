import json
import time
from typing import Dict, Any, Optional
import httpx
from openai import OpenAI
from app.core.config import settings

AI_SETTINGS_FILE = settings.DATA_DIR / "ai_settings.json"

# 预设模型核对日期：各家官方模型列表 / 停用公告（联网核对），只列当前在售的型号
PRESETS_REVIEWED = "2026-10-07"

PROVIDER_PRESETS = {
    "deepseek": {
        "name": "DeepSeek 深度求索",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-flash",
        "available_models": ["deepseek-flash", "deepseek-v4-pro"],
        "supports_embedding": False,
    },
    "qwen": {
        "name": "阿里通义千问 DashScope",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen3.8-flash",
        "available_models": ["qwen3.8-max", "qwen3.8-flash", "qwen3.7-plus"],
        "supports_embedding": True,
        "default_embedding_model": "qwen3.7-text-embedding",
    },
    "siliconflow": {
        "name": "硅基流动 SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "default_model": "deepseek-ai/DeepSeek-V4-Flash",
        "available_models": [
            "deepseek-ai/DeepSeek-V4-Flash",
            "deepseek-ai/DeepSeek-V4-Pro",
            "zai-org/GLM-5.2",
            "moonshotai/Kimi-K2.6",
            "Qwen/Qwen3.5-122B-A10B",
            "Qwen/Qwen3.6-35B-A3B",
            "MiniMaxAI/MiniMax-M2.5",
        ],
        "supports_embedding": True,
        "default_embedding_model": "BAAI/bge-m3",
    },
    "doubao": {
        "name": "字节跳动 豆包",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "doubao-seed-2-1-pro-260628",
        "available_models": [
            "doubao-seed-2-1-pro-260628",
            "doubao-seed-2-1-turbo-260628",
            "doubao-seed-evolving",
        ],
        "supports_embedding": False,
    },
    "zhipu": {
        "name": "智谱清言 BigModel",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-5.3",
        "available_models": ["glm-5.3", "glm-5.3-flash", "glm-5.2", "glm-5-turbo", "glm-4.7-flash"],
        "supports_embedding": True,
        "default_embedding_model": "embedding-3",
    },
    "moonshot": {
        "name": "月之暗面 Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "kimi-k3",
        "available_models": ["kimi-k3", "kimi-k2.6"],
        "supports_embedding": False,
    },
    "ollama": {
        "name": "本地私有化 Ollama",
        "base_url": "http://localhost:11434/v1",
        "default_model": "qwen3.8:27b",
        "available_models": ["qwen3.8:27b", "qwen3.6:35b", "qwen3.6:27b", "qwen3.5:9b", "gpt-oss:20b"],
        "supports_embedding": True,
        "default_embedding_model": "bge-m3",
    },
    "openai": {
        "name": "OpenAI 官方协议",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-6.1-sol",
        "available_models": ["gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna"],
        "supports_embedding": True,
        "default_embedding_model": "text-embedding-3-small",
    },
    "claude_proxy": {
        "name": "Anthropic Claude（OpenAI 兼容）",
        "base_url": "https://api.anthropic.com/v1",
        "default_model": "claude-sonnet-5-5",
        "available_models": ["claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1", "claude-haiku-4-5-20251001"],
        "supports_embedding": False,
        "note": "官方 OpenAI 兼容层主要用于测试评估；Claude 4.7 起的新模型可能拒绝非默认温度，报错时把温度调到 1。",
    },
    "custom": {
        "name": "自定义 OpenAI 兼容接口",
        "base_url": "",
        "default_model": "",
        "available_models": [],
        "supports_embedding": False,
    },
}

# 已停用 / 即将停用的模型名 → 建议替换（来自各家停用公告）。
# 预设里不出现这些名字；联网拉取的列表会过滤掉；已保存的配置若用到，设置页提示一键替换。
RETIRED_MODELS: Dict[str, Dict[str, str]] = {
    "deepseek-chat": {"replacement": "deepseek-flash", "note": "2026-07-24 起停用"},
    "deepseek-reasoner": {"replacement": "deepseek-flash", "note": "2026-07-24 起停用"},
    "deepseek-v4-flash": {"replacement": "deepseek-flash", "note": "已退役，请求会被转到 V4.1-Flash"},
    "kimi-latest": {"replacement": "kimi-k3", "note": "2026-01-28 起停用"},
    "kimi-k2.5": {"replacement": "kimi-k2.6", "note": "2026-08-31 起停用"},
    "kimi-k2-0905-preview": {"replacement": "kimi-k2.6", "note": "2026-05-25 起停用"},
    "kimi-k2-0711-preview": {"replacement": "kimi-k2.6", "note": "2026-05-25 起停用"},
    "kimi-k2-turbo-preview": {"replacement": "kimi-k2.6", "note": "2026-05-25 起停用"},
    "kimi-k2-thinking": {"replacement": "kimi-k3", "note": "2026-05-25 起停用"},
    "kimi-k2-thinking-turbo": {"replacement": "kimi-k3", "note": "2026-05-25 起停用"},
    "moonshot-v1-auto": {"replacement": "kimi-k3", "note": "2026-08-31 起停用"},
    "moonshot-v1-8k": {"replacement": "kimi-k3", "note": "2026-08-31 起停用"},
    "moonshot-v1-32k": {"replacement": "kimi-k3", "note": "2026-08-31 起停用"},
    "moonshot-v1-128k": {"replacement": "kimi-k3", "note": "2026-08-31 起停用"},
    "o1-preview": {"replacement": "gpt-6.1-sol", "note": "2025-07-28 起停用"},
    "o1-mini": {"replacement": "gpt-6-luna", "note": "2025-10-27 起停用"},
    "o3-mini": {"replacement": "gpt-6-luna", "note": "2026-10-23 关停"},
    "o3-mini-2025-01-31": {"replacement": "gpt-6-luna", "note": "2026-10-23 关停"},
    "gpt-4o-2024-05-13": {"replacement": "gpt-6.1-sol", "note": "2026-10-23 关停"},
    "claude-3-7-sonnet-20250219": {"replacement": "claude-sonnet-5-5", "note": "2026-02-19 起停用"},
    "claude-3-5-sonnet-latest": {"replacement": "claude-sonnet-5-5", "note": "2025-10-28 起停用"},
    "claude-3-5-sonnet-20241022": {"replacement": "claude-sonnet-5-5", "note": "2025-10-28 起停用"},
    "claude-3-5-sonnet-20240620": {"replacement": "claude-sonnet-5-5", "note": "2025-10-28 起停用"},
    "claude-3-5-haiku-latest": {"replacement": "claude-haiku-4-5-20251001", "note": "2026-02-19 起停用"},
    "claude-3-5-haiku-20241022": {"replacement": "claude-haiku-4-5-20251001", "note": "2026-02-19 起停用"},
    "claude-3-haiku-20240307": {"replacement": "claude-haiku-4-5-20251001", "note": "2026-04-20 起停用"},
    "claude-3-opus-20240229": {"replacement": "claude-opus-5-5", "note": "2026-01-05 起停用"},
    "claude-sonnet-4-20250514": {"replacement": "claude-sonnet-5-5", "note": "2026-06-15 起停用"},
    "claude-opus-4-20250514": {"replacement": "claude-opus-5-5", "note": "2026-06-15 起停用"},
    "claude-opus-4-1-20250805": {"replacement": "claude-opus-5-5", "note": "2026-08-05 起停用"},
    "claude-sonnet-4-5-20250929": {"replacement": "claude-sonnet-5-5", "note": "将于 2026-11-30 停用"},
}


def curate_online_models(online: list, recommended: list) -> tuple:
    """整理服务商 /models 返回的清单：去掉已停用型号，推荐型号排前，其余按名称排序。返回 (列表, 隐藏数)"""
    names = sorted({m for m in online if m and isinstance(m, str)})
    hidden = [m for m in names if m in RETIRED_MODELS]
    alive = [m for m in names if m not in RETIRED_MODELS]
    first = [m for m in recommended if m in alive]
    rest = [m for m in alive if m not in first]
    return first + rest, len(hidden)


class AISettingsManager:
    """
    运行时 AI 模型与 Embedding 配置管理器：
    1. 持久化存储至 data/ai_settings.json
    2. 支持在 Web 界面无需重启即时切换供应商与模型
    3. 支持 API Key 掩码安全展示
    4. 提供远端接口连通性测试与延时探测
    """

    def __init__(self):
        self._load()

    def _get_default_settings(self) -> Dict[str, Any]:
        return {
            "provider": "deepseek",
            "api_key": settings.LLM_API_KEY,
            "base_url": settings.LLM_BASE_URL,
            "model": settings.LLM_MODEL,
            "temperature": settings.LLM_TEMPERATURE,
            "max_tokens": 4096,
            "embedding_provider": "none",
            "embedding_api_key": settings.EMBEDDING_API_KEY,
            "embedding_base_url": settings.EMBEDDING_BASE_URL,
            "embedding_model": settings.EMBEDDING_MODEL
        }

    def _load(self):
        if AI_SETTINGS_FILE.exists():
            try:
                with open(AI_SETTINGS_FILE, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
                    return
            except Exception as e:
                print(f"[AISettings] 加载配置文件异常: {e}")
        self.data = self._get_default_settings()
        self._save()

    def _save(self):
        try:
            AI_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(AI_SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[AISettings] 保存配置文件异常: {e}")

    def get_settings(self, mask_key: bool = True) -> Dict[str, Any]:
        """获取设置信息，可选择是否对 API Key 进行脱敏（明文 Key 绝不回传前端）"""
        cfg = dict(self.data)
        if mask_key and cfg.get("api_key"):
            key = cfg["api_key"]
            cfg["api_key_masked"] = f"{key[:4]}****{key[-4:]}" if len(key) > 8 else "********"
            cfg["has_api_key"] = True
            # 安全：回传的配置中移除明文 Key（前端留空=保持现有 Key，输入新值=替换）
            cfg["api_key"] = ""
        else:
            cfg["api_key_masked"] = ""
            cfg["has_api_key"] = bool(cfg.get("api_key"))
            cfg["api_key"] = ""

        cfg["presets"] = PROVIDER_PRESETS
        cfg["retired_models"] = RETIRED_MODELS
        cfg["presets_reviewed"] = PRESETS_REVIEWED
        return cfg

    def update_settings(self, new_settings: Dict[str, Any]) -> Dict[str, Any]:
        """更新并保存配置"""
        for k, v in new_settings.items():
            if k in ["api_key_masked", "has_api_key", "presets", "retired_models", "presets_reviewed"]:
                continue
            # 若前端未更改 key（传入了掩码或空），则保持现有 key
            if k == "api_key" and (not v or "****" in v):
                continue
            self.data[k] = v
        self._save()
        return self.get_settings(mask_key=True)

    def test_connection(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        测试远端大模型接口的连通性与往返延迟
        """
        target_key = api_key if api_key is not None else self.data.get("api_key")
        target_url = base_url if base_url is not None else self.data.get("base_url")
        target_model = model if model is not None else self.data.get("model")

        if not target_key or target_key.startswith("sk-placeholder"):
            return {
                "ok": False,
                "latency_ms": 0,
                "error": "未提供有效的 API Key，请先输入密钥"
            }
        if not target_url:
            return {
                "ok": False,
                "latency_ms": 0,
                "error": "未提供 Base URL 端点地址"
            }

        from app.core import llm_usage

        start_time = time.time()
        started = time.perf_counter()
        model = target_model or PROVIDER_PRESETS["deepseek"]["default_model"]
        try:
            client = OpenAI(
                api_key=target_key,
                base_url=target_url,
                timeout=15.0
            )
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "user", "content": "Hello, ping test"}
                ],
                max_tokens=5
            )
            llm_usage.record(purpose="connection_test", kind="chat", model=model, started=started, max_tokens=5,
                             usage=getattr(resp, "usage", None))
            elapsed_ms = int((time.time() - start_time) * 1000)
            return {
                "ok": True,
                "latency_ms": elapsed_ms,
                "model": resp.model or target_model,
                "message": f"连接成功！往返延迟 {elapsed_ms}ms"
            }
        except Exception as e:
            llm_usage.record(purpose="connection_test", kind="chat", model=model, started=started, max_tokens=5, error=e)
            elapsed_ms = int((time.time() - start_time) * 1000)
            return {
                "ok": False,
                "latency_ms": elapsed_ms,
                "error": f"连接测试失败: {str(e)}"
            }

    def fetch_online_models(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        provider: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        联网从远端模型供应商获取实时支持的最新模型清单：
        1. 优先请求服务商的 /models 接口，过滤已停用型号，推荐型号排前；
        2. 网络不通或未提供合法 Key 时回退到内置推荐清单（核对日期见 PRESETS_REVIEWED）。
        """
        target_provider = provider or self.data.get("provider", "deepseek")
        preset = PROVIDER_PRESETS.get(target_provider, {})
        preset_models = preset.get("available_models", [])

        target_key = api_key if api_key is not None and api_key != "" else self.data.get("api_key", "")
        target_url = (base_url or self.data.get("base_url") or preset.get("base_url", "")).rstrip("/")

        if not target_url:
            return {
                "ok": True,
                "models": preset_models,
                "count": len(preset_models),
                "source": "preset_fallback",
                "message": "已载入最新推荐模型"
            }

        headers = {}
        if target_key and not target_key.startswith("sk-placeholder"):
            headers["Authorization"] = f"Bearer {target_key}"

        models_endpoint = f"{target_url}/models"

        try:
            with httpx.Client(timeout=6.0, follow_redirects=True) as client:
                resp = client.get(models_endpoint, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    model_list = []
                    if "data" in data and isinstance(data["data"], list):
                        for item in data["data"]:
                            if isinstance(item, dict) and "id" in item:
                                model_list.append(item["id"])
                    elif "models" in data and isinstance(data["models"], list):
                        for item in data["models"]:
                            if isinstance(item, dict) and "name" in item:
                                model_list.append(item["name"])
                            elif isinstance(item, dict) and "id" in item:
                                model_list.append(item["id"])

                    models, hidden = curate_online_models(model_list, preset_models)
                    if models:
                        suffix = f"（已隐藏 {hidden} 个停用型号）" if hidden else ""
                        return {
                            "ok": True,
                            "models": models,
                            "count": len(models),
                            "source": "online_api",
                            "message": f"已从服务商接口获取 {len(models)} 个可用模型{suffix}"
                        }
        except Exception as e:
            print(f"[AISettingsManager] 联网获取模型失败/转入离线预设: {e}")

        # 回退到内置推荐清单
        return {
            "ok": True,
            "models": preset_models,
            "count": len(preset_models),
            "source": "preset_fallback",
            "message": f"未能联网获取，已载入 {preset.get('name', target_provider)} 推荐模型（核对于 {PRESETS_REVIEWED}）"
        }

ai_settings_manager = AISettingsManager()
