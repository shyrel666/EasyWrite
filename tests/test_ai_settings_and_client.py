"""
AI 设置与 LLM/嵌入客户端测试（新预设矩阵 + 诚实模式信号）。
conftest 在导入应用前切换到临时数据目录，并逐用例恢复配置。
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BASE_DIR))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.core.ai_settings_manager import ai_settings_manager, PROVIDER_PRESETS  # noqa: E402
from app.core.llm_client import llm_client  # noqa: E402
from app.core.embedding_client import embedding_client  # noqa: E402

client = TestClient(app)


def test_provider_presets_are_real_models():
    """预设矩阵中的模型名必须是真实存在的（旧版含虚构的 deepseek-v4-pro）"""
    deepseek = PROVIDER_PRESETS["deepseek"]
    assert "deepseek-chat" in deepseek["available_models"]
    assert "deepseek-reasoner" in deepseek["available_models"]
    assert all("v4-pro" not in m for m in deepseek["available_models"])


def test_settings_api_masks_key():
    """设置读取必须掩码 Key，不向客户端泄露明文"""
    ai_settings_manager.data["api_key"] = "sk-abcdef1234567890"
    ai_settings_manager._save()
    llm_client.reload_config()

    res = client.get("/api/v1/ai/settings")
    assert res.status_code == 200
    data = res.json()
    assert "sk-abcdef1234567890" not in str(data)
    assert "****" in data.get("api_key_masked", "")

    ai_settings_manager.data["api_key"] = ""
    ai_settings_manager._save()
    llm_client.reload_config()


def test_connection_probe_rejects_missing_key():
    """无 Key 时连通性探针明确返回失败，不假装成功"""
    res = client.post("/api/v1/ai/test", json={
        "api_key": "", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat",
    })
    assert res.status_code == 200
    assert res.json()["ok"] is False


def test_fetch_online_models_fallback():
    """联网获取模型：无 Key/不可达时优雅回退到预设清单并标注来源"""
    res = client.post("/api/v1/ai/models/fetch", json={"provider": "deepseek"})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["source"] in ("preset_fallback", "online_api")
    assert data["count"] >= 2


def test_llm_mode_signal_honest():
    """诚实信号：未配置时生成走模拟器且 mode=mock"""
    ai_settings_manager.data["api_key"] = ""
    ai_settings_manager._save()
    llm_client.reload_config()
    assert llm_client.is_configured is False
    out = llm_client.chat_completion(system_prompt="s", user_prompt="u")
    assert out
    assert llm_client.get_mode() == "mock"


def test_structured_output_returns_none_without_llm():
    """未配置 LLM 时结构化输出返回 None（调用方必须显式降级，不得编造数据）"""
    ai_settings_manager.data["api_key"] = ""
    ai_settings_manager._save()
    llm_client.reload_config()
    assert llm_client.chat_completion_structured("s", "u") is None


def test_embedding_describe_endpoint():
    """嵌入配置描述接口：未配置时 is_available=False（BM25+重排兜底）"""
    ai_settings_manager.data["embedding_provider"] = "none"
    ai_settings_manager._save()
    embedding_client.reload_config()
    res = client.get("/api/v1/ai/embedding/describe")
    assert res.status_code == 200
    data = res.json()
    assert data["is_available"] is False
    assert "siliconflow" in data["presets"]


def test_ai_status_endpoint():
    res = client.get("/api/v1/ai/status")
    assert res.status_code == 200
    data = res.json()
    assert "llm_configured" in data and "last_mode" in data
