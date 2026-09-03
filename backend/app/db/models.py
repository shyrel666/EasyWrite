"""
SQLModel 表定义。

设计原则：
- 可查询字段（项目名、状态、时间、doc 归属）用真实列
- 嵌套树/复杂结构（大纲树、全局事实、偏离矩阵）用 JSON 列——访问模式总是整体加载
- kb_chunks.embedding 以 float32 BLOB 内联存储，embedding_version 支持模型变更后重建
- 所有表预留 scope 字段（当前恒为 "local"），为未来多用户/多租户平滑升级留路
"""
from typing import Optional

import numpy as np
from sqlmodel import Field, SQLModel


def vec_to_blob(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32).tobytes()


def blob_to_vec(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


class ProjectModel(SQLModel, table=True):
    __tablename__ = "projects"

    id: str = Field(primary_key=True)
    name: str = Field(index=True)
    client_name: str = ""
    description: str = ""
    facts_json: str = "{}"
    tender_analysis_json: Optional[str] = None
    tender_text: str = Field(default="", description="招标文件解析正文（偏离表抽取与上下文引用）")
    outline_json: str = "[]"
    deviation_json: str = "[]"
    # 向导流程状态：created / tender_analyzed / outline_confirmed / writing
    stage: str = "created"
    status: str = "active"  # active / archived
    scope: str = "local"
    created_at: str = ""
    updated_at: str = ""


class KBDocument(SQLModel, table=True):
    __tablename__ = "kb_documents"

    id: str = Field(primary_key=True)
    doc_name: str = Field(index=True)
    file_path: str = ""
    # ingesting / ready / error
    status: str = "ingesting"
    chunk_count: int = 0
    item_count: int = 0
    # 嵌入模型版本指纹（provider:model），变更后需要重建向量
    embedding_version: str = ""
    error_msg: str = ""
    scope: str = "local"
    created_at: str = ""
    updated_at: str = ""


class KBChunk(SQLModel, table=True):
    __tablename__ = "kb_chunks"

    id: str = Field(primary_key=True)
    doc_id: str = Field(index=True, foreign_key="kb_documents.id")
    seq: int = 0
    section_title: str = ""
    breadcrumb: str = ""
    # 纯正文（检索命中后注入提示词的内容）
    content: str = ""
    # 上下文增强文本（嵌入时的输入：文档标题|面包屑[+LLM摘要]+正文）
    context_text: str = ""
    tags_json: str = "[]"
    # 父章节内容摘要（上下文扩展用，可为空）
    parent_summary: str = ""
    # float32 向量 BLOB；NULL 表示尚未嵌入（BM25 仍可召回）
    embedding: Optional[bytes] = Field(default=None)
    embedding_version: str = ""
    scope: str = "local"


class KBItem(SQLModel, table=True):
    """LLM 策展知识条目（FB208 式）：标题 + 用途摘要 + 关联块区间，供大纲阶段路由"""

    __tablename__ = "kb_items"

    id: str = Field(primary_key=True)
    doc_id: str = Field(index=True, foreign_key="kb_documents.id")
    title: str = Field(index=True)
    summary: str = ""
    chunk_ids_json: str = "[]"
    scope: str = "local"
    created_at: str = ""
