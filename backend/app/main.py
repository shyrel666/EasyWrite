from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from app.core.config import settings
from app.api.endpoints import router as api_router

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="面向政企软件与信息化招投标的高效技术标书编纂流水线系统"
)

# 允许跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态前端资源
STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(api_router, prefix=settings.API_PREFIX)

@app.get("/", summary="系统服务首页或健康检测")
def index_or_health(request: Request):
    # 如果客户端是浏览器或期望 HTML，直接提供 Web 工作台
    accept_header = request.headers.get("accept", "")
    index_file = STATIC_DIR / "index.html"
    if "text/html" in accept_header and index_file.exists():
        return FileResponse(str(index_file))
    
    # 否则默认返回健康检测 JSON
    return {
        "system": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "status": "online",
        "ui_url": "/",
        "docs_url": "/docs",
        "llm_model": settings.LLM_MODEL
    }

@app.get("/health", summary="系统健康检测探针")
def health():
    return {"status": "ok", "version": settings.VERSION}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
