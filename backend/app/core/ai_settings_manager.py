import json
import time
from pathlib import Path
from typing import Dict, Any, Optional
import httpx
from openai import OpenAI
from app.core.config import settings

AI_SETTINGS_FILE = settings.DATA_DIR / "ai_settings.json"

PROVIDER_PRESETS = {
    "deepseek": {
        "name": "DeepSeek 深度求索",
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-v4-pro",
        "available_models": [
            "deepseek-v4-pro",
            "deepseek-v4-flash",
            "deepseek-v4-flash-vision-exp",
            "deepseek-reasoner",
            "deepseek-chat"
        ],
        "supports_embedding": False
    },
    "qwen": {
        "name": "阿里通义千问 DashScope",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-max-latest",
        "available_models": [
            "qwen-max-latest",
            "qwen-plus-latest",
            "qwen-turbo-latest",
            "qwen2.5-72b-instruct",
            "qwen2.5-32b-instruct",
            "qwen2.5-14b-instruct",
            "qwen-long",
            "text-embedding-v3"
        ],
        "supports_embedding": True,
        "default_embedding_model": "text-embedding-v3"
    },
    "siliconflow": {
        "name": "硅基流动 SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "default_model": "deepseek-ai/DeepSeek-V3",
        "available_models": [
            "deepseek-ai/DeepSeek-V3",
            "deepseek-ai/DeepSeek-R1",
            "Pro/deepseek-ai/DeepSeek-V3",
            "Pro/deepseek-ai/DeepSeek-R1",
            "Qwen/Qwen2.5-72B-Instruct",
            "Qwen/Qwen2.5-32B-Instruct",
            "THUDM/glm-4-9b-chat",
            "BAAI/bge-large-zh-v1.5",
            "BAAI/bge-m3"
        ],
        "supports_embedding": True,
        "default_embedding_model": "BAAI/bge-large-zh-v1.5"
    },
    "doubao": {
        "name": "字节跳动 豆包",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "doubao-pro-128k",
        "available_models": [
            "doubao-pro-128k",
            "doubao-pro-32k",
            "doubao-lite-128k",
            "doubao-lite-32k"
        ],
        "supports_embedding": False
    },
    "zhipu": {
        "name": "智谱清言 BigModel",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4-plus",
        "available_models": [
            "glm-4-plus",
            "glm-4-air",
            "glm-4-flash",
            "glm-4-long",
            "embedding-3"
        ],
        "supports_embedding": True,
        "default_embedding_model": "embedding-3"
    },
    "moonshot": {
        "name": "月之暗面 Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "kimi-latest",
        "available_models": [
            "kimi-latest",
            "moonshot-v1-128k",
            "moonshot-v1-32k",
            "moonshot-v1-8k"
        ],
        "supports_embedding": False
    },
    "ollama": {
        "name": "本地私有化 Ollama",
        "base_url": "http://localhost:11434/v1",
        "default_model": "deepseek-r1:14b",
        "available_models": [
            "deepseek-r1:32b",
            "deepseek-r1:14b",
            "deepseek-r1:8b",
            "deepseek-r1:7b",
            "qwen2.5:32b",
            "qwen2.5:14b",
            "qwen2.5:7b",
            "llama3.3:70b",
            "bge-m3"
        ],
        "supports_embedding": True,
        "default_embedding_model": "bge-m3"
    },
    "openai": {
        "name": "OpenAI 官方协议",
        "base_url": "https://api.openai.com/v1",
        "default_model": "o3-mini",
        "available_models": [
            "o3-mini",
            "o1",
            "o1-mini",
            "gpt-4o",
            "gpt-4o-mini",
            "text-embedding-3-large",
            "text-embedding-3-small"
        ],
        "supports_embedding": True,
        "default_embedding_model": "text-embedding-3-small"
    },
    "claude_proxy": {
        "name": "Anthropic Claude 代理",
        "base_url": "https://api.anthropic.com/v1",
        "default_model": "claude-3-7-sonnet-20250219",
        "available_models": [
            "claude-3-7-sonnet-20250219",
            "claude-3-5-sonnet-latest",
            "claude-3-5-haiku-latest"
        ],
        "supports_embedding": False
    },
    "custom": {
        "name": "自定义 OpenAI 兼容接口",
        "base_url": "",
        "default_model": "",
        "available_models": [],
        "supports_embedding": False
    }
}

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
            "api_key": settings.LLM_API_KEY if settings.LLM_API_KEY != "sk-placeholder" else "",
            "base_url": settings.LLM_BASE_URL,
            "model": settings.LLM_MODEL,
            "temperature": settings.LLM_TEMPERATURE,
            "max_tokens": 4096,
            "embedding_provider": "local",  # local / cloud
            "embedding_api_key": settings.EMBEDDING_API_KEY or "",
            "embedding_base_url": settings.EMBEDDING_BASE_URL or "",
            "embedding_model": settings.EMBEDDING_MODEL or "BAAI/bge-small-zh-v1.5"
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
        """获取设置信息，可选择是否对 API Key 进行脱敏"""
        cfg = dict(self.data)
        if mask_key and cfg.get("api_key"):
            key = cfg["api_key"]
            if len(key) > 8:
                cfg["api_key_masked"] = f"{key[:4]}****{key[-4:]}"
            else:
                cfg["api_key_masked"] = "********"
            # 保持原始 key 字段在前端不直接明文泄露
            cfg["has_api_key"] = True
        else:
            cfg["api_key_masked"] = ""
            cfg["has_api_key"] = bool(cfg.get("api_key"))

        cfg["presets"] = PROVIDER_PRESETS
        return cfg

    def update_settings(self, new_settings: Dict[str, Any]) -> Dict[str, Any]:
        """更新并保存配置"""
        for k, v in new_settings.items():
            if k in ["api_key_masked", "has_api_key", "presets"]:
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

        start_time = time.time()
        try:
            client = OpenAI(
                api_key=target_key,
                base_url=target_url,
                timeout=15.0
            )
            resp = client.chat.completions.create(
                model=target_model or "deepseek-chat",
                messages=[
                    {"role": "user", "content": "Hello, ping test"}
                ],
                max_tokens=5
            )
            elapsed_ms = int((time.time() - start_time) * 1000)
            return {
                "ok": True,
                "latency_ms": elapsed_ms,
                "model": resp.model or target_model,
                "message": f"连接成功！往返延迟 {elapsed_ms}ms"
            }
        except Exception as e:
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
        1. 优先发送网络请求探测各大开放平台 /models 接口；
        2. 若网络超时或未提供合法 Key，则优雅回退至 2025/2026 最新内置模型矩阵。
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
                    
                    if model_list:
                        # 过滤无效项与排序
                        valid_models = [m for m in model_list if m and isinstance(m, str)]
                        # 优先保留旗舰、chat、instruct 模型
                        unique_models = sorted(list(set(valid_models)))
                        return {
                            "ok": True,
                            "models": unique_models,
                            "count": len(unique_models),
                            "source": "online_api",
                            "message": f"成功联网从 API 获取到 {len(unique_models)} 个实时在线模型"
                        }
        except Exception as e:
            print(f"[AISettingsManager] 联网获取模型失败/转入离线预设: {e}")

        # 优雅回退至 2025/2026 最新官方模型矩阵
        return {
            "ok": True,
            "models": preset_models,
            "count": len(preset_models),
            "source": "preset_fallback",
            "message": f"已载入 {preset.get('name', target_provider)} 2025/2026 最新官方模型库"
        }

ai_settings_manager = AISettingsManager()
