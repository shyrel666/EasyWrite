"""R07：知识库文档、块与策展条目的删除、旧数据清理和并发写回。"""
import json
import uuid

import pytest
from sqlmodel import Session, delete as sql_delete

from app.core.llm_client import llm_client
from app.db import database
from app.db.database import get_session
from app.db.models import KBChunk, KBDocument, KBItem
from app.services.rag import curator
from app.services.rag.indexer import knowledge_index


@pytest.fixture()
def documents():
    records = []
    with get_session() as session:
        for name in ("甲", "乙"):
            doc_id = f"p2_{uuid.uuid4().hex}"
            record = {"doc": doc_id, "chunk": f"{doc_id}_chunk", "item": f"{doc_id}_item"}
            records.append(record)
            session.add(KBDocument(id=doc_id, doc_name=f"{name}文档", chunk_count=1, item_count=1, status="ready"))
            session.add(KBChunk(id=record["chunk"], doc_id=doc_id, content=f"{name}系统容灾备份方案",
                                context_text=f"{name}系统容灾备份方案", breadcrumb=f"{name}文档 > 方案"))
            session.add(KBItem(id=record["item"], doc_id=doc_id, title=f"{name}系统容灾条目", summary="部署和运维要求",
                               chunk_ids_json=json.dumps([record["chunk"]])))
    knowledge_index.reload()
    yield records
    with get_session() as session:
        ids = [r["doc"] for r in records]
        for model in (KBItem, KBChunk):
            session.exec(sql_delete(model).where(model.doc_id.in_(ids)))
        session.exec(sql_delete(KBDocument).where(KBDocument.id.in_(ids)))
    knowledge_index.reload()


def test_delete_removes_items_from_api_prompts_and_storage(client, documents):
    removed, kept = documents
    assert removed["item"] in curator.build_item_metadata_prompt()
    assert client.delete(f"/api/v1/knowledge/documents/{removed['doc']}").status_code == 200
    items = client.get("/api/v1/knowledge/items").json()["items"]
    assert removed["item"] not in {i["id"] for i in items}
    assert kept["item"] in {i["id"] for i in items}
    assert client.get("/api/v1/knowledge/items", params={"doc_id": removed["doc"]}).json() == {"items": []}
    assert removed["item"] not in curator.build_item_metadata_prompt()
    assert kept["item"] in curator.build_item_metadata_prompt()
    assert curator.get_item_contents([removed["item"]]) == []
    assert knowledge_index.get_chunk(removed["chunk"]) is None
    with get_session() as session:
        assert session.get(KBDocument, removed["doc"]) is None
        assert session.get(KBChunk, removed["chunk"]) is None
        assert session.get(KBItem, removed["item"]) is None
        assert session.get(KBItem, kept["item"]) is not None


def test_delete_failure_rolls_back_document_chunks_and_items(client, documents, monkeypatch):
    target = documents[0]
    delete = Session.delete

    def fail_document_delete(session, item):
        if isinstance(item, KBDocument) and item.id == target["doc"]:
            raise RuntimeError("模拟文档删除失败")
        return delete(session, item)

    monkeypatch.setattr(Session, "delete", fail_document_delete)
    with pytest.raises(RuntimeError, match="模拟文档删除失败"):
        client.delete(f"/api/v1/knowledge/documents/{target['doc']}")
    with get_session() as session:
        assert session.get(KBDocument, target["doc"]) is not None
        assert session.get(KBChunk, target["chunk"]) is not None
        assert session.get(KBItem, target["item"]) is not None
    assert knowledge_index.get_chunk(target["chunk"]) is not None
    assert target["item"] in curator.build_item_metadata_prompt()


def test_orphan_items_are_hidden_and_cleaned_on_startup(documents):
    orphan_id = f"orphan_{uuid.uuid4().hex}"
    with get_session() as session:
        session.add(KBItem(id=orphan_id, doc_id="missing_doc", title="旧版遗留条目", summary="不能进入提示词"))
    assert orphan_id not in {it["id"] for it in curator.list_items()}
    assert orphan_id not in curator.build_item_metadata_prompt()
    assert curator.get_item_contents([orphan_id]) == []
    database.init_db()
    with get_session() as session:
        assert session.get(KBItem, orphan_id) is None
        assert all(session.get(KBItem, record["item"]) is not None for record in documents)
    assert database.cleanup_orphan_kb_items() == 0


def test_document_deleted_during_curation_cannot_recreate_items(client, documents, monkeypatch):
    target, kept = documents
    calls = []

    def generate(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            assert client.delete(f"/api/v1/knowledge/documents/{target['doc']}").status_code == 200
            return {"items": [{"title": "过时的策展结果", "summary": "删除前资料", "chunk_ids": [target["chunk"]]}]}
        return {"items": []}

    monkeypatch.setattr(llm_client, "is_configured", True)
    monkeypatch.setattr(llm_client, "chat_completion_structured", generate)
    assert curator.curate_document(target["doc"]) == 0
    assert curator.list_items(target["doc"]) == []
    with get_session() as session:
        assert session.get(KBItem, f"{target['doc']}_it0") is None
        assert session.get(KBDocument, target["doc"]) is None
    assert kept["item"] in curator.build_item_metadata_prompt()


def test_curation_replaces_items_and_updates_count_in_same_transaction(documents, monkeypatch):
    target = documents[0]
    responses = iter([{"items": [{"title": "重新策展", "summary": "有效来源", "chunk_ids": [target["chunk"]]}]}, {"items": []}])
    monkeypatch.setattr(llm_client, "is_configured", True)
    monkeypatch.setattr(llm_client, "chat_completion_structured", lambda *args, **kwargs: next(responses))
    assert curator.curate_document(target["doc"]) == 1
    [item] = curator.list_items(target["doc"])
    assert item["title"] == "重新策展"
    assert curator.get_item_contents([item["id"]])[0]["content"] == "甲系统容灾备份方案"
    with get_session() as session:
        assert session.get(KBItem, target["item"]) is None
        assert session.get(KBDocument, target["doc"]).item_count == 1
