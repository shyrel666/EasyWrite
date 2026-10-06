"""验证应用的数据库与运行时文件实际位于测试临时目录。"""
import sqlite3
from contextlib import closing
from pathlib import Path

from app.core.ai_settings_manager import AI_SETTINGS_FILE, ai_settings_manager
from app.core.config import BASE_DIR, settings
from app.db.database import engine
from app.services.assets.asset_manager import asset_manager
from app.services.exporter.diagram_renderer import diagram_renderer
from app.services.exporter.template_manager import template_manager


def test_runtime_storage_and_credentials_are_isolated(runtime_data_dir, project_id):
    assert not runtime_data_dir.is_relative_to(BASE_DIR)
    for path in (
        settings.DATA_DIR, settings.DB_PATH, settings.UPLOAD_DIR,
        settings.KNOWLEDGE_DIR, settings.OUTPUT_DIR, settings.RENDERED_DIR,
        settings.TEMPLATE_DIR, AI_SETTINGS_FILE, asset_manager.storage_file,
        template_manager.storage_file, diagram_renderer.output_dir,
    ):
        assert path.resolve().is_relative_to(runtime_data_dir)

    assert Path(engine.url.database).resolve() == settings.DB_PATH.resolve()
    # 从临时数据库独立读取 API 创建的项目，验证实际写入位置。
    with closing(sqlite3.connect(settings.DB_PATH)) as connection:
        row = connection.execute(
            "SELECT id FROM projects WHERE id = ?", (project_id,),
        ).fetchone()
    assert row == (project_id,)
    assert settings.LLM_API_KEY == settings.EMBEDDING_API_KEY == ""
    assert not ai_settings_manager.data.get("api_key")
    assert not ai_settings_manager.data.get("embedding_api_key")
