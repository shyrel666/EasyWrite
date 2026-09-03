import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent

class Settings(BaseSettings):
    PROJECT_NAME: str = "EasyWrite 智能标书编纂系统"
    VERSION: str = "0.1.0"
    API_PREFIX: str = "/api/v1"
    
    # LLM Settings (OpenAI-compatible)
    # Supports DeepSeek, 通义千问 Qwen, OpenAI, Kimi, etc.
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "sk-placeholder")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-chat")
    LLM_TEMPERATURE: float = 0.3
    
    # Embedding Settings
    # Can use cloud API or local sentence-transformers
    EMBEDDING_PROVIDER: str = os.getenv("EMBEDDING_PROVIDER", "local")  # "local" or "openai"
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
    EMBEDDING_API_KEY: str = os.getenv("EMBEDDING_API_KEY", "")
    EMBEDDING_BASE_URL: str = os.getenv("EMBEDDING_BASE_URL", "")

    # Storage Paths
    DATA_DIR: Path = BASE_DIR / "data"
    UPLOAD_DIR: Path = DATA_DIR / "uploads"
    KNOWLEDGE_DIR: Path = DATA_DIR / "knowledge_store"
    OUTPUT_DIR: Path = DATA_DIR / "exports"
    TEMPLATE_DIR: Path = BASE_DIR / "templates"
    
    model_config = SettingsConfigDict(env_file=".env", extra="allow")

settings = Settings()

# Ensure directories exist
for p in [settings.DATA_DIR, settings.UPLOAD_DIR, settings.KNOWLEDGE_DIR, settings.OUTPUT_DIR, settings.TEMPLATE_DIR]:
    p.mkdir(parents=True, exist_ok=True)
