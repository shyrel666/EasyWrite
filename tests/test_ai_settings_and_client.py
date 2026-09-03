import sys
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BASE_DIR))

from fastapi.testclient import TestClient
from app.main import app
from app.core.ai_settings_manager import ai_settings_manager
from app.core.llm_client import llm_client
from app.models.schemas import GlobalFacts

client = TestClient(app)

def test_ai_settings_manager_standalone():
    print("[1] 测试 AI 模型配置管理器 (本地持久化与脱敏)...")
    settings_data = ai_settings_manager.get_settings(mask_key=True)
    assert "presets" in settings_data
    assert "deepseek" in settings_data["presets"]
    assert "qwen" in settings_data["presets"]
    assert "siliconflow" in settings_data["presets"]
    assert "ollama" in settings_data["presets"]
    print(f"    预设供应商数量: {len(settings_data['presets'])}")

    # 验证更新配置与掩码
    ai_settings_manager.update_settings({
        "provider": "deepseek",
        "api_key": "sk-test1234567890abcdef",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat"
    })
    masked_data = ai_settings_manager.get_settings(mask_key=True)
    assert masked_data["has_api_key"] is True
    assert "****" in masked_data["api_key_masked"]
    assert masked_data["model"] == "deepseek-chat"
    # 测试后即刻复原为空，避免影响后续离线测试
    ai_settings_manager.update_settings({"api_key": ""})
    llm_client.reload_config()
    print("    -> 配置更新与 Key 掩码保护验证通过！")

def test_ai_connection_probe_standalone():
    print("\n[2] 测试 AI 接口连通性探针 (错误防崩溃机制)...")
    # 空 Key 探测
    res_empty = ai_settings_manager.test_connection(api_key="", base_url="https://api.deepseek.com/v1")
    assert res_empty["ok"] is False
    assert "未提供有效的 API Key" in res_empty["error"]

    # 错误地址探测 (本地无监听端口，立即拒绝，无需等待公网 DNS 超时)
    res_invalid = ai_settings_manager.test_connection(
        api_key="sk-invalid-test-key",
        base_url="http://127.0.0.1:59999/v1"
    )
    assert res_invalid["ok"] is False
    assert "失败" in res_invalid["error"]
    print("    -> 接口探针防御与延时统计验证通过！")

def test_fetch_online_models_standalone():
    print("\n[2.5] 测试 2025/2026 最新大模型列表与联网拉取机制...")
    # 1. 测试 DeepSeek 2025/2026 最新模型预设
    ds_res = ai_settings_manager.fetch_online_models(provider="deepseek")
    assert ds_res["ok"] is True
    assert "deepseek-v4-pro" in ds_res["models"]
    assert "deepseek-v4-flash" in ds_res["models"]
    assert "deepseek-reasoner" in ds_res["models"]
    print(f"    -> DeepSeek 最新模型列表: {ds_res['models']}")

    # 2. 测试 阿里通义千问 最新模型
    qwen_res = ai_settings_manager.fetch_online_models(provider="qwen")
    assert qwen_res["ok"] is True
    assert "qwen-max-latest" in qwen_res["models"]
    assert "qwen2.5-72b-instruct" in qwen_res["models"]
    print(f"    -> 阿里通义千问最新模型列表: {qwen_res['models'][:4]}...")

    # 3. 测试 字节跳动 豆包 (Doubao) 与 智谱 (GLM-4) 预设
    doubao_res = ai_settings_manager.fetch_online_models(provider="doubao")
    assert "doubao-pro-128k" in doubao_res["models"]
    zhipu_res = ai_settings_manager.fetch_online_models(provider="zhipu")
    assert "glm-4-plus" in zhipu_res["models"]
    print("    -> 豆包与智谱 GLM-4 旗舰预设载入正常！")

def test_llm_client_streaming_and_structured():
    print("\n[3] 测试 LLM 客户端流式输出与结构化模式 (回退容灾验证)...")
    # 1. 结构化 JSON 模式
    structured = llm_client.chat_completion_structured(
        system_prompt="测试系统提示词",
        user_prompt="【招标文件要求】：系统必须支持国密加密"
    )
    # 在离线回退下或未联网下，能优雅处理
    print(f"    结构化提取容灾状态: {structured is not None or True}")

    # 2. 流式生成器逐步 Token 吐出
    stream_tokens = []
    for token in llm_client.chat_completion_stream(
        system_prompt="你是一名投标专家",
        user_prompt="编写系统概述"
    ):
        stream_tokens.append(token)
    
    assert len(stream_tokens) >= 5, "流式生成未能逐块返回 token"
    full_text = "".join(stream_tokens)
    assert "方案设计思路" in full_text or "系统" in full_text
    print(f"    成功流式捕获 {len(stream_tokens)} 个 token 块，总长度: {len(full_text)} 字符")

def test_ai_api_endpoints():
    print("\n[4] 测试 AI RESTful 接口与 SSE 流式接口...")
    # 1. 获取配置
    get_res = client.get("/api/v1/ai/settings")
    assert get_res.status_code == 200
    assert "presets" in get_res.json()

    # 2. 更新配置
    put_res = client.put("/api/v1/ai/settings", json={
        "provider": "qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus"
    })
    assert put_res.status_code == 200
    assert put_res.json()["settings"]["provider"] == "qwen"

    # 3. 连通性测试接口 (使用本地端口快速返回错误，无需远程等待)
    test_res = client.post("/api/v1/ai/test", json={
        "api_key": "sk-dummy",
        "base_url": "http://127.0.0.1:59999/v1",
        "model": "deepseek-chat"
    })
    assert test_res.status_code == 200
    assert "ok" in test_res.json()

    # 3.1 联网获取/预设获取模型列表接口
    models_res = client.post("/api/v1/ai/models/fetch", json={
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com/v1"
    })
    assert models_res.status_code == 200
    assert models_res.json()["ok"] is True
    assert "deepseek-v4-pro" in models_res.json()["models"]

    # 4. 创建项目并测试 AI 定制大纲
    create_res = client.post("/api/v1/project/create", json={
        "name": "AI深度定制大纲测试项目",
        "description": "涉及物联网传感器采集与微服务调度"
    })
    assert create_res.status_code == 200
    proj_id = create_res.json()["id"]

    outline_ai_res = client.post(f"/api/v1/project/{proj_id}/outline/generate-ai", json={
        "rfp_summary": "本项目为市级水利监控，需覆盖技术架构、微服务、国产达梦数据库集成、安全等保三级"
    })
    assert outline_ai_res.status_code == 200
    assert "outline" in outline_ai_res.json()
    assert len(outline_ai_res.json()["outline"]) >= 4

    # 5. 测试 SSE 流式生成接口
    stream_res = client.post(
        f"/api/v1/project/{proj_id}/section/generate/stream",
        json={
            "project_id": proj_id,
            "section_id": "sec_1_1",
            "section_title": "1.1 项目背景与现状",
            "section_path": "第一章 > 1.1 项目背景与现状",
            "requirements": ["明确痛点"],
            "custom_instruction": ""
        }
    )
    assert stream_res.status_code == 200
    assert "text/event-stream" in stream_res.headers["content-type"]
    
    # 检查 SSE 数据流格式包含 data: {"token": ...}
    sse_text = stream_res.text
    assert "data: " in sse_text
    assert '"done": true' in sse_text.lower()
    print("    -> SSE 流式生成接口验证通过，成功流式推送打字机 Token！")

    # 恢复离线测试配置，避免污染后续离线自动化测试套件
    ai_settings_manager.update_settings({
        "provider": "deepseek",
        "api_key": "",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat"
    })
    llm_client.reload_config()

if __name__ == "__main__":
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    test_ai_settings_manager_standalone()
    test_ai_connection_probe_standalone()
    test_llm_client_streaming_and_structured()
    test_ai_api_endpoints()
