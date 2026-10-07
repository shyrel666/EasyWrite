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
from app.core.ai_settings_manager import (  # noqa: E402
    PROVIDER_PRESETS, RETIRED_MODELS, ai_settings_manager, curate_online_models,
)
from app.core.llm_client import LLMClient, llm_client  # noqa: E402
from app.core.embedding_client import embedding_client  # noqa: E402

client = TestClient(app)


def test_provider_presets_are_current_models():
    """预设只列在售型号：默认模型在清单里，且不含任何已停用型号（如 2026-07 停用的 deepseek-chat）"""
    deepseek = PROVIDER_PRESETS["deepseek"]
    assert deepseek["default_model"] == "deepseek-flash"
    assert "deepseek-v4-pro" in deepseek["available_models"]
    for key, preset in PROVIDER_PRESETS.items():
        models = preset["available_models"]
        if preset["default_model"]:
            assert preset["default_model"] in models, key
        assert not set(models) & set(RETIRED_MODELS), key
    for info in RETIRED_MODELS.values():
        assert info["replacement"] not in RETIRED_MODELS


def test_curate_online_models_hides_retired_and_ranks_recommended():
    online = ["zeta-model", "deepseek-chat", "deepseek-v4-pro", "deepseek-flash", "", None, "deepseek-flash"]
    models, hidden = curate_online_models(online, ["deepseek-flash", "deepseek-v4-pro", "not-online"])
    assert models == ["deepseek-flash", "deepseek-v4-pro", "zeta-model"]
    assert hidden == 1


def test_settings_api_reports_retired_models():
    data = client.get("/api/v1/ai/settings").json()
    assert data["retired_models"]["deepseek-chat"]["replacement"] == "deepseek-flash"
    assert data["presets_reviewed"]


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
        "api_key": "", "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
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


def test_configured_startup_does_not_report_a_mock_generation(monkeypatch):
    """有配置的服务刚启动时，没有生成记录不能被当成演示输出。"""
    monkeypatch.setitem(ai_settings_manager.data, "api_key", "sk-test-mode-status")
    fresh_client = LLMClient()
    monkeypatch.setattr("app.api.ai_settings.llm_client", fresh_client)

    data = client.get("/api/v1/ai/status").json()
    assert data["llm_configured"] is True
    assert data["last_mode"] is None


def test_saving_model_config_clears_previous_mock_mode():
    """从未配置/演示模式保存模型后，旧模式不得污染已生效的新配置。"""
    ai_settings_manager.data["api_key"] = ""
    llm_client.reload_config()
    llm_client.chat_completion("s", "u")
    assert client.get("/api/v1/ai/status").json()["last_mode"] == "mock"

    res = client.put("/api/v1/ai/settings", json={
        "api_key": "sk-test-mode-status", "base_url": "https://api.example.com/v1", "model": "test-model",
    })
    assert res.status_code == 200
    data = client.get("/api/v1/ai/status").json()
    assert data["llm_configured"] is True
    assert data["llm_model"] == "test-model"
    assert data["last_mode"] is None


def test_configured_model_fallback_still_reports_mock(monkeypatch):
    """配置存在但模型返回空正文时，保留真实回退信号，避免隐藏演示结果。"""
    monkeypatch.setitem(ai_settings_manager.data, "api_key", "sk-test-mode-status")
    llm_client.reload_config()
    monkeypatch.setattr(llm_client, "_create_escalating", lambda *args, **kwargs: "")

    assert llm_client.chat_completion("s", "u")
    data = client.get("/api/v1/ai/status").json()
    assert data["llm_configured"] is True
    assert data["last_mode"] == "mock"
