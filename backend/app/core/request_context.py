"""
请求上下文：当前操作所属的项目。

由 main.py 的中间件按 URL（/api/v1/project/{id}/...）设置；后台任务提交时连同上下文一起
带进工作线程，因此任务归属与模型用量记录无需层层传参。
"""
import contextvars

current_project_id: contextvars.ContextVar[str] = contextvars.ContextVar("current_project_id", default="")
