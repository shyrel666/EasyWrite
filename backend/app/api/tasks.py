"""后台任务查询路由（前端进度轮询）"""
from fastapi import APIRouter, HTTPException

from app.core.task_manager import task_manager

router = APIRouter(prefix="/tasks", tags=["后台任务"])


@router.get("", summary="最近任务列表")
def list_tasks(limit: int = 30):
    return {"tasks": task_manager.list(limit)}


@router.get("/{task_id}", summary="查询任务状态与进度")
def get_task(task_id: str):
    task = task_manager.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.post("/{task_id}/cancel", summary="请求取消运行中的任务")
def cancel_task(task_id: str):
    ok = task_manager.cancel(task_id)
    return {"status": "success" if ok else "ignored", "cancelled": ok}
