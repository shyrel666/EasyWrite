"""R01：模型/维度隔离、启动降级、固定配置快照与原子重建。全部使用本地嵌入桩。"""
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import knowledge
from app.core.embedding_client import embedding_client
from app.db.models import KBChunk, KBDocument
from app.services.rag import indexer, retriever


def configure(monkeypatch, model="model-a", dim=2, create=None):
    if create is None:
        def create(input, model):
            return SimpleNamespace(data=[SimpleNamespace(embedding=[1.0] + [0.0] * (dim - 1)) for _ in input])
    monkeypatch.setattr(embedding_client, "provider", "test")
    monkeypatch.setattr(embedding_client, "base_url", "http://embedding.test/v1")
    monkeypatch.setattr(embedding_client, "model", model)
    monkeypatch.setattr(embedding_client, "is_available", True)
    monkeypatch.setattr(embedding_client, "client", SimpleNamespace(embeddings=SimpleNamespace(create=create)))
    return indexer.embedding_version()


@pytest.fixture()
def database(tmp_path, monkeypatch):
    path = tmp_path / "vectors.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    @contextmanager
    def get_session():
        with Session(engine) as session:
            yield session
            session.commit()

    monkeypatch.setattr(indexer, "get_session", get_session)
    yield engine, path
    engine.dispose()


def seed(database, rows):
    with Session(database[0]) as session:
        session.add(KBDocument(id="doc", doc_name="测试知识库", status="ready"))
        for cid, version, vector in rows:
            session.add(KBChunk(id=cid, doc_id="doc", content="容灾备份方案", context_text="容灾备份方案",
                                embedding_version=version,
                                embedding=vector if isinstance(vector, bytes) else np.array(vector, dtype=np.float32).tobytes()))
        session.commit()
    return indexer.KnowledgeIndex()


@pytest.mark.parametrize("dim", [2, 3])
def test_model_switch_never_uses_old_space_even_with_same_dimension(database, monkeypatch, client, dim):
    old = configure(monkeypatch)
    idx = seed(database, [("old", old, [1, 0])])
    assert idx.search_dense("备份")
    configure(monkeypatch, model="model-b", dim=dim)
    monkeypatch.setattr(retriever, "knowledge_index", idx)
    monkeypatch.setattr(knowledge, "knowledge_index", idx)

    res = client.post("/api/v1/knowledge/search", json={"query": "备份", "rerank": False})
    assert res.status_code == 200
    assert res.json()["refs"][0]["chunk_id"] == "old"  # BM25 仍可用
    assert res.json()["refs"][0]["dense_score"] == 0
    assert "重建向量" in res.json()["message"]
    assert idx.search_dense("备份") == []
    assert idx.stats()["embedding_stale_count"] == 1


def test_mixed_dimensions_versions_and_corrupt_vectors_survive_reload_and_startup(database, monkeypatch):
    version = configure(monkeypatch, dim=3)
    idx = seed(database, [("a", version, [1, 0]), ("b", version, [1, 0, 0]),
                          ("c", "old:model", [1, 0, 0]), ("bad", version, b"x"),
                          ("nan", version, [float("nan"), 0, 0])])
    idx.reload()
    assert [cid for cid, _ in idx.search_dense("备份")] == ["b"]
    assert idx.stats()["embedding_stale_count"] == 4
    env = {**os.environ, "DB_PATH": str(database[1]), "EMBEDDING_PROVIDER": "none"}
    result = subprocess.run([sys.executable, "-c", "import app.main"],
                            cwd=Path(__file__).resolve().parents[1] / "backend", env=env,
                            capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stderr


def test_dimension_change_falls_back_and_can_be_rebuilt(database, monkeypatch):
    version = configure(monkeypatch, dim=3)
    idx = seed(database, [("old", version, [1, 0])])
    assert idx.search_dense("备份") == []
    assert "BM25" in idx.dense_status()["embedding_warning"]
    assert idx.reindex_embeddings() == 1
    assert [cid for cid, _ in idx.search_dense("备份")] == ["old"]
    assert idx.stats()["embedding_stale_count"] == 0


def test_endpoint_is_part_of_embedding_version(monkeypatch):
    before = configure(monkeypatch)
    monkeypatch.setattr(embedding_client, "base_url", "http://other.test/v1")
    assert indexer.embedding_version() != before


def test_query_wait_does_not_block_bm25_or_reload_and_model_switch_discards_result(database, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def create(input, model):
        entered.set()
        assert release.wait(5)
        return SimpleNamespace(data=[SimpleNamespace(embedding=[1.0, 0.0])])

    version = configure(monkeypatch, create=create)
    idx = seed(database, [("a", version, [1, 0])])
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = pool.submit(idx.search_dense, "备份")
        try:
            assert entered.wait(3)
            assert pool.submit(idx.search_bm25, "备份").result(timeout=2)
            pool.submit(idx.reload).result(timeout=2)
            configure(monkeypatch, model="model-b")
        finally:
            release.set()
        assert pending.result(timeout=3) == []


def test_rebuild_failure_keeps_all_persisted_vectors(database, monkeypatch):
    version = configure(monkeypatch)
    idx = seed(database, [(str(i), version, [1, 0]) for i in range(33)])
    calls = []

    def create(input, model):
        calls.append(model)
        if len(calls) == 2:
            raise RuntimeError("模拟第二批网络失败")
        return SimpleNamespace(data=[SimpleNamespace(embedding=[1.0, 0.0, 0.0]) for _ in input])

    configure(monkeypatch, model="model-b", create=create)
    with pytest.raises(RuntimeError, match="原索引保留"):
        idx.reindex_embeddings()
    with Session(database[0]) as session:
        rows = session.exec(select(KBChunk)).all()
        assert all(r.embedding_version == version and len(r.embedding) == 8 for r in rows)


def test_ingest_batches_keep_one_model_snapshot_during_reload(database, monkeypatch):
    calls = []

    def create(input, model):
        calls.append(model)
        configure(monkeypatch, model="model-b", dim=3)
        return SimpleNamespace(data=[SimpleNamespace(embedding=[1.0, 0.0]) for _ in input])

    version = configure(monkeypatch, create=create)
    idx = indexer.KnowledgeIndex()
    idx.add_document("doc", "资料", "", [{"id": str(i), "context_text": "备份"} for i in range(33)])
    assert calls == ["model-a", "model-a"]
    with Session(database[0]) as session:
        rows = session.exec(select(KBChunk)).all()
        assert all(r.embedding_version == version and len(r.embedding) == 8 for r in rows)
    assert idx.search_dense("备份") == []


def counting(dim, calls):
    def create(input, model):
        calls.extend(input)
        return SimpleNamespace(data=[SimpleNamespace(embedding=[1.0] + [0.0] * (dim - 1)) for _ in input])
    return create


def test_rebuild_only_embeds_stale_chunks(database, monkeypatch):
    calls = []
    version = configure(monkeypatch, create=counting(2, calls))
    idx = seed(database, [("ok1", version, [1, 0]), ("ok2", version, [0, 1]),
                          ("old", "old:model", [1, 0, 0]), ("bad", version, b"x")])
    assert idx.stats()["embedding_stale_count"] == 2
    assert idx.reindex_embeddings() == 2
    assert len(calls) == 2  # 已兼容的两个块不重新计费
    assert idx.stats()["embedding_stale_count"] == 0
    assert idx.reindex_embeddings() == 0 and len(calls) == 2


def test_same_version_dimension_change_rebuilds_kept_vectors_too(database, monkeypatch):
    calls = []
    version = configure(monkeypatch, create=counting(3, calls))
    idx = seed(database, [("ok1", version, [1, 0]), ("ok2", version, [0, 1]), ("old", "old:model", [1, 0])])
    assert idx.reindex_embeddings() == 3
    assert len(calls) == 3
    with Session(database[0]) as session:
        assert {len(r.embedding) for r in session.exec(select(KBChunk)).all()} == {12}
    assert len(idx.search_dense("备份")) == 3


def test_reindex_endpoint_requires_embedding_and_reuses_running_task(client, monkeypatch):
    monkeypatch.setattr(knowledge, "embedding_version", lambda: "")
    assert client.post("/api/v1/knowledge/reindex").status_code == 400

    monkeypatch.setattr(knowledge, "embedding_version", lambda: "test:model:abc")
    submitted = []
    monkeypatch.setattr(knowledge.task_manager, "submit", lambda *a, **kw: submitted.append(a) or "t-new")
    monkeypatch.setattr(knowledge.task_manager, "list", lambda **kw: [{"id": "t-running"}])
    assert client.post("/api/v1/knowledge/reindex").json() == {"task_id": "t-running", "already_running": True}
    monkeypatch.setattr(knowledge.task_manager, "list", lambda **kw: [])
    assert client.post("/api/v1/knowledge/reindex").json() == {"task_id": "t-new", "already_running": False}
    assert submitted[0][0] == "kb_reindex"
