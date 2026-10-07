"""后台任务查询路由（前端进度轮询、刷新后重新接管、任务中心）"""
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.core.task_manager import task_manager

router = APIRouter(prefix="/tasks", tags=["后台任务"])


@router.get("", summary="最近任务列表（可按项目/类型/进行中筛选，含服务重启前的历史任务）")
def list_tasks(
    limit: int = 30,
    project_id: Optional[str] = None,
    type: Optional[str] = None,
    active: bool = False,
    with_result: bool = False,
):
    limit = max(1, min(limit, 200))
    return {"tasks": task_manager.list(limit, project_id=project_id, task_type=type, active_only=active, with_result=with_result)}


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
