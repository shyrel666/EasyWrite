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
    tender_structure_json: str = Field(default="", description="招标文件章节树（偏离表按技术需求章节抽取）")
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


class TaskModel(SQLModel, table=True):
    """后台任务记录：运行中以内存为准、状态变化时落库；服务重启后未结束的任务标记为 interrupted"""
    __tablename__ = "tasks"

    id: str = Field(primary_key=True)
    type: str = Field(index=True)
    title: str = ""
    # 所属项目（知识库等全局任务为空）
    project_id: str = Field(default="", index=True)
    # pending / running / completed / failed / cancelled / interrupted
    status: str = Field(default="pending", index=True)
    progress: int = 0
    message: str = ""
    result_json: str = ""
    error: str = ""
    created_at: str = ""
    updated_at: str = ""
    # 提交时间戳（秒，排序用；created_at 只精确到秒）
    created_ts: float = Field(default=0.0, index=True)
    scope: str = "local"


class LLMCallLog(SQLModel, table=True):
    """每一次真实的模型请求（含截断放大重试、限流重试与失败）：耗时与用量；离线模拟不记录"""
    __tablename__ = "llm_calls"

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: str = Field(default="", index=True)
    project_id: str = Field(default="", index=True)
    # 调用用途：tender_extract / outline_draft / section_write / rerank / embedding …
    purpose: str = Field(default="", index=True)
    # chat / stream / embedding
    kind: str = "chat"
    model: str = ""
    # ok / truncated（max_tokens 截断）/ empty（无正文）/ error / aborted（流式中途被取消）
    status: str = "ok"
    latency_ms: int = 0
    # 流式首个正文 token 的到达耗时
    first_token_ms: Optional[int] = None
    # 用量：服务商未返回时为空（不估算）
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    max_tokens: int = 0
    finish_reason: str = ""
    error: str = ""


class SectionVersion(SQLModel, table=True):
    """章节正文历史版本：AI 生成/润色/批量撰写/恢复覆盖前后的快照（人工自动保存不逐次留版）"""
    __tablename__ = "section_versions"

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: str = Field(index=True)
    section_id: str = Field(index=True)
    content: str = ""
    # manual 覆盖前的人工稿 / ai_generate / polish / batch / restore
    source: str = "manual"
    char_count: int = 0
    created_at: str = ""
