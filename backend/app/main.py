import logging
import re
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from app.core.config import settings
from app.core import llm_usage
from app.core.request_context import current_project_id
from app.core.task_manager import task_manager
from app.db.database import migrate_legacy_json  # noqa: F401
from app.services.rag.ingestor import mark_interrupted_ingests
from app.services.project_store import ProjectNotFound
from app.api.ai_settings import router as ai_settings_router
from app.api.projects import router as projects_router
from app.api.tender import router as tender_router
from app.api.outline import router as outline_router
from app.api.sections import router as sections_router
from app.api.deviations import router as deviations_router
from app.api.compliance import router as compliance_router
from app.api.knowledge import router as knowledge_router
from app.api.assets import router as assets_router
from app.api.evidence import router as evidence_router
from app.api.export import router as export_router
from app.api.tasks import router as tasks_router
from app.api.proposals import router as proposals_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("easywrite")

# 重启恢复：上次进程未跑完的后台任务标记为"已中断"，未入库完成的知识库文档标记为失败；清理过期调用记录
task_manager.recover_interrupted()
mark_interrupted_ingests()
llm_usage.prune()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="面向政企软件与信息化招投标的高效技术标书编纂流水线系统",
)

# 开发期 Vite (5173) 跨域；生产同源由静态托管
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_PROJECT_PATH = re.compile(rf"^{re.escape(settings.API_PREFIX)}/project/([^/]+)")


class ProjectContextMiddleware:
    """按 URL 绑定当前项目（纯 ASGI，不包装 SSE 流）：后台任务归属与模型用量记录据此归档"""

    def __init__(self, asgi_app):
        self.app = asgi_app

    async def __call__(self, scope, receive, send):
        match = _PROJECT_PATH.match(scope.get("path", "")) if scope["type"] == "http" else None
        if not match or match.group(1) == "create":
            await self.app(scope, receive, send)
            return
        token = current_project_id.set(match.group(1))
        try:
            await self.app(scope, receive, send)
        finally:
            current_project_id.reset(token)


app.add_middleware(ProjectContextMiddleware)


@app.exception_handler(ProjectNotFound)
async def project_not_found_handler(request: Request, exc: ProjectNotFound):
    return JSONResponse(status_code=404, content={"detail": "项目不存在"})


for r in (
    ai_settings_router, projects_router, tender_router, outline_router,
    sections_router, deviations_router, compliance_router, knowledge_router,
    assets_router, evidence_router, export_router, tasks_router, proposals_router,
):
    app.include_router(r, prefix=settings.API_PREFIX)


@app.get("/", summary="系统服务首页或健康检测")
def index_or_health(request: Request):
    index_file = Path(__file__).resolve().parent / "static" / "index.html"
    accept_header = request.headers.get("accept", "")
    if "text/html" in accept_header and index_file.exists():
        return FileResponse(str(index_file))
    return {
        "system": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "status": "online",
        "ui_url": "/",
        "docs_url": "/docs",
    }


@app.get("/health", summary="系统健康检测探针")
def health():
    return {"status": "ok", "version": settings.VERSION}


# 生产模式：托管前端构建产物（frontend npm run build 输出至此）
STATIC_DIR = Path(__file__).resolve().parent / "static"
SPA_ASSETS_DIR = STATIC_DIR / "assets"
if SPA_ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(SPA_ASSETS_DIR)), name="spa_assets")


@app.get("/{full_path:path}", include_in_schema=False, summary="SPA 深链接回退")
def spa_fallback(full_path: str, request: Request):
    """vue-router history 模式：非 API 路径回退到 SPA 入口（/project/xx/workspace 等刷新直达）"""
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="接口不存在")
    index_file = STATIC_DIR / "index.html"
    accept_header = request.headers.get("accept", "")
    if "text/html" in accept_header and index_file.exists():
        return FileResponse(str(index_file))
    raise HTTPException(status_code=404, detail="资源不存在")
