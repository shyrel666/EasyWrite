import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    PROJECT_NAME: str = "EasyWrite 智能标书编纂系统"
    VERSION: str = "0.2.0"
    API_PREFIX: str = "/api/v1"

    # LLM Settings (OpenAI-compatible)
    # Supports DeepSeek, 通义千问 Qwen, OpenAI, Kimi, etc.
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-flash")
    LLM_TEMPERATURE: float = 0.3

    # Embedding Settings (OpenAI-compatible; separate from LLM provider)
    # e.g. SiliconFlow BAAI/bge-m3, DashScope qwen3.7-text-embedding, Ollama bge-m3
    EMBEDDING_API_KEY: str = os.getenv("EMBEDDING_API_KEY", "")
    EMBEDDING_BASE_URL: str = os.getenv("EMBEDDING_BASE_URL", "")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")

    # RAG retrieval tuning
    RAG_CHUNK_TARGET_CHARS: int = 500       # 语义分块目标长度
    RAG_CHUNK_MAX_CHARS: int = 1000         # 分块硬上限
    RAG_CHUNK_MIN_CHARS: int = 60           # 低于此长度并入相邻块
    RAG_RECALL_TOP_K: int = 20              # 每路召回数量
    RAG_RRF_K: int = 60                     # RRF 融合常数
    RAG_RERANK_THRESHOLD: float = 6.0       # LLM 重排 0-10 分阈值，低于则不注入
    RAG_CONTEXTUAL_SUMMARIES: bool = False  # 入库时是否用 LLM 为每块生成一句话定位摘要

    # 智能完善（单章节写—查—改闭环）的运行预算：每次运行的模型请求次数与时长上限
    REFINE_MAX_CALLS: int = 15
    REFINE_MAX_SECONDS: int = 600

    # Storage Paths
    DATA_DIR: Path = BASE_DIR / "data"
    UPLOAD_DIR: Path = DATA_DIR / "uploads"
    KNOWLEDGE_DIR: Path = DATA_DIR / "knowledge_store"
    OUTPUT_DIR: Path = DATA_DIR / "exports"
    RENDERED_DIR: Path = DATA_DIR / "rendered_diagrams"
    TEMPLATE_DIR: Path = BASE_DIR / "templates"
    DB_PATH: Path = DATA_DIR / "easywrite.db"

    model_config = SettingsConfigDict(env_file=".env", extra="allow")


settings = Settings()

# Ensure directories exist
for p in [
    settings.DATA_DIR,
    settings.UPLOAD_DIR,
    settings.KNOWLEDGE_DIR,
    settings.OUTPUT_DIR,
    settings.RENDERED_DIR,
    settings.TEMPLATE_DIR,
]:
    p.mkdir(parents=True, exist_ok=True)
