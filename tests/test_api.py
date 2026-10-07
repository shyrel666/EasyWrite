"""
RESTful API 全生命周期集成测试（新架构）：
项目向导 → 拆标应用 → 大纲草案/展开 → 章节流式生成(SSE) → 偏离表 → 质检 → 合规任务 → 导出。
"""
import json
import sys
import time
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BASE_DIR))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from conftest import TENDER_SAMPLE  # noqa: E402

client = TestClient(app)


def test_full_lifecycle():
    # 1. 健康检测
    res = client.get("/health")
    assert res.status_code == 200 and res.json()["version"] == "0.2.0"

    # 2. 创建项目（向导入口）
    res = client.post("/api/v1/project/create", json={
        "name": "智慧水务一体化综合调度系统建设项目",
        "client_name": "某市水务环境集团",
        "description": "物联感知、实时调度、能耗管理与安防联动一体化平台",
    })
    assert res.status_code == 200
    project = res.json()
    pid = project["id"]
    assert project["stage"] == "created"
    assert project["outline"] == []

    # 3. 文本拆标 + 应用到项目
    res = client.post("/api/v1/tender/analyze/text", json={"text": TENDER_SAMPLE})
    assert res.status_code == 200
    analysis = res.json()
    assert len(analysis["star_disqualification_items"]) >= 3
    res = client.post(f"/api/v1/project/{pid}/tender/apply",
                      json={"analysis": analysis, "tender_text": TENDER_SAMPLE})
    assert res.status_code == 200
    assert res.json()["stage"] == "tender_analyzed"

    # 4. 一级大纲草案（无 Key 时规则兜底结构）
    res = client.post(f"/api/v1/project/{pid}/outline/draft-level1", json={})
    assert res.status_code == 200
    chapters = res.json()["chapters"]
    assert len(chapters) >= 3

    # 5. 展开完整大纲树（人工确认门之后）
    res = client.post(f"/api/v1/project/{pid}/outline/expand",
                      json={"chapters": chapters, "total_word_budget": 20000})
    assert res.status_code == 200
    outline = res.json()["outline"]
    assert outline and outline[0]["children"]

    # 6. 章节 SSE 流式生成
    leaf = outline[0]["children"][0]
    with client.stream("POST", f"/api/v1/project/{pid}/section/generate/stream", json={
        "project_id": pid, "section_id": leaf["id"], "section_title": leaf["title"],
        "section_path": leaf.get("path", ""), "requirements": [],
    }) as stream_resp:
        assert stream_resp.status_code == 200
        tokens, refs_event, done = [], None, False
        for line in stream_resp.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            if "token" in event:
                tokens.append(event["token"])
            if "refs" in event:
                refs_event = event
            if event.get("done"):
                done = True
        assert done, "SSE 未收到 done 事件"
        assert refs_event is not None, "SSE 未先推送检索引用事件"
        assert "".join(tokens), "流式生成内容为空"

    # 7. 章节人工保存
    res = client.put(f"/api/v1/project/{pid}/section", json={
        "section_id": leaf["id"], "content": "人工修改后的章节正文。", "status": "reviewed",
    })
    assert res.status_code == 200

    # 8. 引用锁定/排除（人工在环）
    res = client.put(f"/api/v1/project/{pid}/section/refs", json={
        "section_id": leaf["id"], "pinned_refs": ["chunk_x"], "excluded_refs": ["chunk_y"],
    })
    assert res.status_code == 200

    # 9. 偏离表：提取 → 手编落库 → 批量响应任务 → 回填
    res = client.post(f"/api/v1/project/{pid}/deviation/extract", json={})
    assert res.status_code == 200
    items = res.json()["items"]
    assert items, "偏离表提取为空"
    assert all(it["response_status"] == "待生成" for it in items), "未配置 LLM 时不得伪造响应"

    items[0]["response_status"] = "完全满足"
    items[0]["response_detail"] = "人工填写的点对点响应。"
    res = client.put(f"/api/v1/project/{pid}/deviation", json=items)
    assert res.status_code == 200

    res = client.post(f"/api/v1/project/{pid}/deviation/generate")
    assert res.status_code == 200
    task_id = res.json()["task_id"]
    for _ in range(40):
        task = client.get(f"/api/v1/tasks/{task_id}").json()
        if task["status"] in ("completed", "failed"):
            break
        time.sleep(0.3)
    assert task["status"] == "completed"

    res = client.post(f"/api/v1/project/{pid}/deviation/inject", json={"section_id": leaf["id"]})
    assert res.status_code == 200

    # 10. 八维质检（规则快扫，同步）
    res = client.post(f"/api/v1/project/{pid}/quality/inspect")
    assert res.status_code == 200
    assert res.json()["overall_score"] >= 0

    # 11. 合规审查（后台任务）
    res = client.post(f"/api/v1/project/{pid}/compliance/check")
    assert res.status_code == 200
    task_id = res.json()["task_id"]
    for _ in range(40):
        task = client.get(f"/api/v1/tasks/{task_id}").json()
        if task["status"] in ("completed", "failed"):
            break
        time.sleep(0.3)
    assert task["status"] == "completed"
    assert task["result"]["mode"] == "rules"

    # 12. 导出 Word（含目录域与页码）
    res = client.get(f"/api/v1/project/{pid}/export")
    assert res.status_code == 200
    assert len(res.content) > 5000
    assert res.headers["content-type"].startswith("application/vnd.openxmlformats")

    # 13. 清理
    res = client.delete(f"/api/v1/project/{pid}")
    assert res.status_code == 200
