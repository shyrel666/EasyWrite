"""阶段 B5：导出前检查清单（只提示，不阻止导出）"""
import time

from app.core.task_manager import task_manager


def _preflight(client, pid):
    res = client.get(f"/api/v1/project/{pid}/export/preflight")
    assert res.status_code == 200
    data = res.json()
    return data, {c["key"]: c for c in data["checks"]}


def _wait(task_id):
    for _ in range(200):
        task = task_manager.get(task_id)
        if task["status"] not in ("pending", "running"):
            return task
        time.sleep(0.02)
    raise AssertionError("任务超时")


def test_empty_project_needs_attention(client, project_id):
    data, checks = _preflight(client, project_id)
    assert list(checks) == ["unwritten", "placeholders", "unverified_assets", "book_consistency", "material",
                            "unreviewed", "redlines", "deviation_pending"]
    assert checks["unwritten"]["status"] == "warn" and checks["unwritten"]["message"] == "尚未规划大纲"
    assert checks["redlines"]["status"] == "unknown"
    assert checks["material"]["status"] == "ok" and checks["deviation_pending"]["status"] == "ok"
    assert checks["book_consistency"]["status"] == "ok" and "0 个已撰写章节" in checks["book_consistency"]["message"]
    assert not data["ready"] and data["attention"] == 2


def test_sections_placeholders_assets_and_review(client, project_id):
    client.post("/api/v1/assets/personnel", json={"id": "p_unv", "name": "李工", "role": "开发", "status": "unverified"})
    asset_ref = {"ref_type": "asset", "kind": "personnel", "asset_id": "p_unv", "name": "李工", "status": "unverified"}
    outline = [{"id": "sec_1", "title": "第一章 方案", "level": 1, "children": [
        {"id": "sec_1_1", "title": "1.1 空白", "level": 2},
        {"id": "sec_1_2", "title": "1.2 有占位", "level": 2, "status": "completed", "last_refs": [asset_ref],
         "content": "响应时限【待填写】，人员【待核实：李工的证书】，工期【待填写】，我方（【投标人全称：待填写】）承担保修。"},
        {"id": "sec_1_3", "title": "1.3 已校审", "level": 2, "status": "reviewed", "content": "完整正文"},
    ]}]
    client.put(f"/api/v1/project/{project_id}/outline", json={"outline": outline})
    _, checks = _preflight(client, project_id)

    assert [d["section_id"] for d in checks["unwritten"]["details"]] == ["sec_1_1"]
    ph = checks["placeholders"]
    assert ph["status"] == "warn" and ph["count"] == 4
    assert ph["details"][0]["section_id"] == "sec_1_2" and "【待核实：李工的证书】" in ph["details"][0]["detail"]
    # 带标签前缀的占位（【投标人全称：待填写】）也要计入：只匹配以"待填写/待核实"开头的括号会漏掉这种写法
    assert "【投标人全称：待填写】" in ph["details"][0]["detail"]
    assert checks["unverified_assets"]["details"] == [{"title": "1.2 有占位", "detail": "李工", "section_id": "sec_1_2"}]
    assert [d["section_id"] for d in checks["unreviewed"]["details"]] == ["sec_1_2"]

    # 资料确认后不再提示
    client.post("/api/v1/assets/personnel", json={"id": "p_unv", "name": "李工", "role": "开发", "status": "confirmed"})
    _, checks = _preflight(client, project_id)
    assert checks["unverified_assets"]["status"] == "ok"


def test_book_consistency_reports_cross_section_conflicts(client, project_id):
    """F4：同一承诺类别跨章取值不一致；采纳为全局事实后偏离值按"与全局事实冲突"报，不重复报取值不一致"""
    outline = [{"id": "sec_1", "title": "第一章 服务", "level": 1, "children": [
        {"id": "sec_1_1", "title": "3.1 运维保障", "level": 2, "status": "completed",
         "content": "接到报修后30分钟内响应。"},
        {"id": "sec_1_2", "title": "5.2 应急说明", "level": 2, "status": "completed",
         "content": "故障发生后承诺2小时内响应，驻场工程师3名。"},
    ]}]
    client.put(f"/api/v1/project/{project_id}/outline", json={"outline": outline})
    _, checks = _preflight(client, project_id)
    book = checks["book_consistency"]
    assert book["status"] == "warn"
    assert "跨章取值不一致 1 组" in book["message"] and "承诺数值" in book["message"]
    detail = book["details"][0]["title"]
    assert "响应时限" in detail and "30分钟" in detail and "2小时" in detail and "3.1" in detail

    client.put(f"/api/v1/project/{project_id}/facts", json={"sla_commitment": "接到报修后30分钟内响应"})
    _, checks = _preflight(client, project_id)
    book = checks["book_consistency"]
    assert "与全局事实冲突 1 处" in book["message"] and "跨章取值" not in book["message"]
    assert "2小时" in book["details"][0]["title"] and book["details"][0]["section_id"] == "sec_1_2"


def test_book_consistency_example_asset_and_clean_book(client, project_id):
    example = next(p for p in client.get("/api/v1/assets/personnel").json()
                   if p.get("status") == "example")["name"]
    outline = [{"id": "sec_1", "title": "第一章 服务", "level": 1, "children": [
        {"id": "sec_1_1", "title": "3.1 运维保障", "level": 2, "status": "completed",
         "content": f"项目经理由{example}担任。"},
    ]}]
    client.put(f"/api/v1/project/{project_id}/outline", json={"outline": outline})
    _, checks = _preflight(client, project_id)
    book = checks["book_consistency"]
    assert book["status"] == "warn" and "资料问题 1 处" in book["message"]
    assert book["details"][0]["section_id"] == "sec_1_1" and example in book["details"][0]["title"]

    # 无冲突时为 ok：正文的取值全部以【待填写】占位（占位不参与数值抽取）
    client.put(f"/api/v1/project/{project_id}/section",
               json={"section_id": "sec_1_1", "content": "响应时限为【待填写】，人员配置按制度执行。"})
    _, checks = _preflight(client, project_id)
    book = checks["book_consistency"]
    assert book["status"] == "ok" and "1 个已撰写章节未发现跨章冲突" in book["message"]


def test_material_and_deviation_gaps(client, project_id):
    analysis = {"scoring_items": [{"id": "s2", "name": "企业资质", "points": 2, "response_type": "evidence",
                                   "criteria": "具有ISO27001认证证书得2分"}]}
    client.post(f"/api/v1/project/{project_id}/tender/apply", json={"analysis": analysis})
    client.put(f"/api/v1/project/{project_id}/deviation", json=[
        {"index": 1, "clause_title": "支持国产数据库", "response_status": "待生成"},
        {"index": 2, "clause_title": "支持国密算法", "response_status": "完全满足", "response_detail": "支持"},
    ])
    _, checks = _preflight(client, project_id)
    assert checks["material"]["status"] == "warn"
    assert checks["material"]["details"] == [{"title": "企业资质", "detail": "未关联", "section_id": ""}]
    assert checks["deviation_pending"]["count"] == 1 and "国产数据库" in checks["deviation_pending"]["details"][0]["detail"]


def test_latest_compliance_check_is_reported(client, project_id):
    report = {"passed": False, "total_star_items": 3, "satisfied_star_items": 1, "mode": "llm",
              "risk_items": [{"item": "★ 须支持国密算法", "reason": "正文未响应", "severity": "HIGH"}]}
    task = _wait(task_manager.submit("compliance_check", lambda ctx: report, project_id=project_id))
    assert task["status"] == "completed"
    _, checks = _preflight(client, project_id)
    red = checks["redlines"]
    assert red["status"] == "warn" and red["count"] == 2 and "模型证据核查" in red["message"]
    assert red["details"][0]["title"] == "★ 须支持国密算法"

    ok = {**report, "satisfied_star_items": 3, "risk_items": []}
    _wait(task_manager.submit("compliance_check", lambda ctx: ok, project_id=project_id))
    _, checks = _preflight(client, project_id)
    assert checks["redlines"]["status"] == "ok"

    # 核查之后项目有更新：提示重新核查
    time.sleep(1.1)
    client.put(f"/api/v1/project/{project_id}/facts", json={"company_name": "某公司"})
    _, checks = _preflight(client, project_id)
    assert checks["redlines"]["status"] == "unknown" and "重新核查" in checks["redlines"]["message"]


def test_acceptance_missing_certificate_shows_in_quality_and_preflight(client, project_id):
    """阶段 B 验收：评分项要求某资质、企业已录入但未上传证书 → 质检与导出前清单都显示"缺附件" """
    analysis = {"scoring_items": [{"id": "s_iso", "name": "企业资质", "points": 2, "response_type": "evidence",
                                   "criteria": "投标人具有ISO27001信息安全管理体系认证证书得2分"}]}
    client.post(f"/api/v1/project/{project_id}/tender/apply", json={"analysis": analysis})
    client.post("/api/v1/assets/qualifications", json={"id": "q_27001", "name": "ISO27001 信息安全管理体系认证",
                                                       "cert_no": "ISMS-9", "issue_org": "某认证中心"})
    sug = client.get(f"/api/v1/project/{project_id}/evidence/suggestions").json()["suggestions"]["s_iso"]
    assert [c["key"] for c in sug] == ["qualifications:q_27001"]  # 示例资质不在建议中
    client.put(f"/api/v1/project/{project_id}/evidence/s_iso", json={"asset_keys": ["qualifications:q_27001"]})

    quality = client.post(f"/api/v1/project/{project_id}/quality/inspect").json()
    assert next(c for c in quality["scoring_coverage"] if c["item_id"] == "s_iso")["material_status"] == "缺附件"
    _, checks = _preflight(client, project_id)
    assert checks["material"]["details"] == [
        {"title": "企业资质", "detail": "缺附件：未上传证明附件", "section_id": ""}]
