"""
智能完善测试用的可控模型桩：替换 llm_client 底层的 OpenAI 客户端（预算检查、调用记录、截断放大重试都照常经过 llm_client），
按提示词区分重排 / 起草 / 修订，依次返回脚本中的结果（最后一项重复使用）。
"""
import time
from types import SimpleNamespace

from app.core.llm_client import llm_client
from app.services.project_store import find_node, project_store
from app.services.rag.indexer import knowledge_index

# 章节自身的评分要点（rubric_planner 写入要求的格式）：未写到时是阻塞问题
REQUIREMENTS = ["评分子项：故障分级响应（5分）", "评分子项：季度巡检安排（3分）"]
OUTLINE = [{"id": "sec_1", "title": "第一章 服务方案", "level": 1, "children": [
    {"id": "sec_1_1", "title": "1.1 运维保障", "level": 2, "requirements": REQUIREMENTS},
    {"id": "sec_1_2", "title": "1.2 项目团队", "level": 2},
]}]
LACKING = "本节说明运维服务的组织方式与人员分工。"            # 两个要点都没写到：2 个阻塞问题
HALF = LACKING + "故障分级响应按事件等级处理。"              # 写到一个：1 个阻塞问题
FULL = HALF + "季度巡检安排由运维组执行。"                  # 都写到：目标达成


class FakeModel:
    def __init__(self, monkeypatch, drafts=(LACKING,), revisions=(FULL,), reranks=('{"scores": []}',)):
        self.queues = {"draft": list(drafts), "revise": list(revisions), "rerank": list(reranks)}
        self.calls = []
        monkeypatch.setattr(llm_client, "is_configured", True)
        monkeypatch.setattr(llm_client, "model", "fake-model")
        monkeypatch.setattr(llm_client, "max_tokens", 1000)
        monkeypatch.setattr(llm_client, "client",
                            SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=self.create))))

    @staticmethod
    def kind(kw):
        system, user = kw["messages"][0]["content"], kw["messages"][1]["content"]
        if "重排专家" in system:
            return "rerank"
        return "revise" if "【修订任务】" in user else "draft"

    def create(self, **kw):
        kind = self.kind(kw)
        self.calls.append((kind, kw))
        queue = self.queues[kind]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        content, finish = item if isinstance(item, tuple) else (item, "stop")
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content))],
                               usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150,
                                                     completion_tokens_details=None))

    def kinds(self):
        return [k for k, _ in self.calls]

    def user_prompts(self, kind):
        return [kw["messages"][1]["content"] for k, kw in self.calls if k == kind]


def no_kb(monkeypatch, chunks=None, recall=False):
    """知识库检索桩：chunks 为知识库中的块（可被锁定引用）；recall=True 时每次召回都返回它们（触发重排），否则无候选"""
    chunks = chunks or {}
    hits = [(cid, 1.0) for cid in chunks] if recall else []
    monkeypatch.setattr(knowledge_index, "search_bm25", lambda q, top_k=20, doc_filter=None: list(hits))
    monkeypatch.setattr(knowledge_index, "search_dense", lambda q, top_k=20, doc_filter=None: [])
    monkeypatch.setattr(knowledge_index, "get_chunk", lambda cid: chunks.get(cid))
    monkeypatch.setattr(knowledge_index, "get_doc_name", lambda doc_id: "历史标书")


def chunk(cid, content="历史项目的运维组织与故障处理流程。"):
    return {"id": cid, "doc_id": "doc_1", "section_title": "运维方案", "breadcrumb": "历史标书 > 运维方案",
            "content": content, "tags": [], "parent_summary": ""}


def wait(client, task_id):
    for _ in range(500):
        task = client.get(f"/api/v1/tasks/{task_id}").json()
        if task["status"] not in ("pending", "running"):
            return task
        time.sleep(0.02)
    raise AssertionError("任务超时")


def refine(client, pid, sid="sec_1_1", expect=200, **body):
    res = client.post(f"/api/v1/project/{pid}/section/{sid}/refine", json=body)
    assert res.status_code == expect, res.json()
    return wait(client, res.json()["task_id"]) if expect == 200 else res.json()


def node(pid, sid="sec_1_1"):
    return find_node(project_store.get(pid).outline, sid)
