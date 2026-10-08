"""AI 模型与嵌入配置路由"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body

from app.core import llm_usage
from app.core.ai_settings_manager import ai_settings_manager
from app.core.llm_client import llm_client
from app.core.embedding_client import embedding_client

router = APIRouter(prefix="/ai", tags=["AI 配置中心"])


@router.get("/settings", summary="获取当前 AI 模型配置与可用预设")
def get_ai_settings():
    return ai_settings_manager.get_settings(mask_key=True)


@router.put("/settings", summary="更新 AI 模型配置并热重载（含嵌入端点）")
def update_ai_settings(settings_data: Dict[str, Any] = Body(...)):
    updated = ai_settings_manager.update_settings(settings_data)
    llm_client.reload_config()
    embedding_client.reload_config()
    return {"status": "success", "settings": updated}


@router.post("/test", summary="测试大模型端点连通性与延时")
def test_ai_connection(payload: Dict[str, Any] = Body(...)):
    return ai_settings_manager.test_connection(
        api_key=payload.get("api_key"),
        base_url=payload.get("base_url"),
        model=payload.get("model"),
    )


@router.post("/models/fetch", summary="联网动态获取模型供应商的实时可用模型列表")
def fetch_ai_models(payload: Dict[str, Any] = Body(...)):
    return ai_settings_manager.fetch_online_models(
        api_key=payload.get("api_key"),
        base_url=payload.get("base_url"),
        provider=payload.get("provider"),
    )


@router.get("/embedding/describe", summary="获取嵌入服务配置与预设")
def describe_embedding():
    return embedding_client.describe()


@router.post("/embedding/test", summary="嵌入服务连通性探针（真实嵌入一条短语）")
def test_embedding():
    ok, msg = embedding_client.test_connection()
    return {"ok": ok, "message": msg}


@router.get("/status", summary="当前 LLM/嵌入可用状态（前端离线横幅依据）")
def ai_status():
    return {
        "llm_configured": llm_client.is_configured,
        "llm_model": llm_client.model if llm_client.is_configured else "",
        "llm_base_url": llm_client.base_url if llm_client.is_configured else "",
        "last_mode": llm_client.last_mode(),  # 全局最近一次，不受某个请求上下文影响
        "embedding_available": embedding_client.is_available,
        "embedding_model": embedding_client.model if embedding_client.is_available else "",
    }


@router.get("/usage", summary="模型调用记录汇总：次数、用量、耗时（按用途/模型/日期/运行），可按项目或运行筛选")
def llm_usage_summary(days: int = 7, project_id: Optional[str] = None, recent: int = 30, run_id: Optional[str] = None):
    return llm_usage.summarize(days=max(1, min(days, 180)), project_id=project_id,
                               recent_limit=max(0, min(recent, 200)), run_id=run_id)
