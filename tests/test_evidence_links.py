"""评分项与企业资料关联、证明材料状态与质检第 7 维（阶段 B2）"""
from app.services.assets.evidence import _codes, _code_ok, phrase_ratio

PDF_BYTES = b"%PDF-1.4\n%%EOF\n"

SCORING_ITEMS = [
    {"id": "s1", "name": "技术服务方案", "points": 30, "response_type": "proposal", "kind": "technical",
     "criteria": "内容包括：①运维流程；②应急预案。"},
    {"id": "s2", "name": "企业资质", "points": 2, "response_type": "evidence", "kind": "business",
     "criteria": "1.投标人具有ISO20000信息技术服务管理体系认证证书得1分； 2.投标人具有ISO27001信息安全管理体系认证证书得1分。",
     "note": "提供相关证书复印件，并加盖投标人单位公章。"},
    {"id": "s3", "name": "企业业绩", "points": 4, "response_type": "evidence", "kind": "business",
     "criteria": "自2022年1月1日至投标截止时间，投标人具有与本项目类似的信息化类运维项目经验，每提供1个业绩得1分。"},
    {"id": "s4", "name": "人员资质", "points": 4, "response_type": "evidence", "kind": "business",
     "criteria": "项目经理具备信息系统项目管理师（高级）证书，得4分。"},
]

ASSETS = {
    "qualifications": [
        {"id": "q_iso20000", "name": "ISO20000 信息技术服务管理体系认证", "cert_no": "ITSM-1", "issue_org": "某认证中心",
         "expiry_date": "2029-01-01"},
        {"id": "q_iso9001", "name": "ISO9001 质量管理体系认证", "cert_no": "QMS-1", "issue_org": "某认证中心"},
    ],
    "personnel": [
        {"id": "p_wang", "name": "王工", "role": "项目经理", "certificates": ["信息系统项目管理师"]},
        {"id": "p_li", "name": "李工", "role": "开发工程师", "certificates": ["PMP"]},
    ],
    "cases": [
        {"id": "c_water", "project_name": "某市智慧水务运维项目", "client_name": "某水务集团", "contract_amount": "100万元"},
    ],
}


def _setup(client, pid):
    analysis = {"project_name": "某市信息化运维项目", "submission_deadline": "2026年10月20日09:30",
                "scoring_items": SCORING_ITEMS}
    assert client.post(f"/api/v1/project/{pid}/tender/apply", json={"analysis": analysis}).status_code == 200
    for kind, items in ASSETS.items():
        for item in items:
            assert client.post(f"/api/v1/assets/{kind}", json=item).status_code == 200


def test_code_tokens_must_match():
    assert _codes("ISO/IEC 27001 信息安全") == {"27001"}
    assert "2023" not in _codes("2023年1月1日以来 ISO9001")
    assert _code_ok("ISO9001 质量管理体系认证", "具有ISO9001质量管理体系认证证书")
    assert not _code_ok("ISO9001 质量管理体系认证", "具有ISO27001信息安全管理体系认证证书")
    assert not _code_ok("PMP", "具备信息系统项目管理师证书")
    assert phrase_ratio("信息系统安全集成服务资质（一级）", "信息系统安全集成服务认证证书（一级）") > 0.8


def test_suggestions_match_by_name_and_skip_examples(client, project_id):
    _setup(client, project_id)
    res = client.get(f"/api/v1/project/{project_id}/evidence/suggestions")
    assert res.status_code == 200
    sug = res.json()["suggestions"]
    assert set(sug) == {"s2", "s3", "s4"}  # 只有证明材料类评分项
    qual_keys = [c["key"] for c in sug["s2"]]
    assert "qualifications:q_iso20000" in qual_keys
    assert "qualifications:q_iso9001" not in qual_keys  # 证书编号不同：不建议
    assert all(c["asset_status"] != "example" for items in sug.values() for c in items)
    assert [c["key"] for c in sug["s3"]] == ["cases:c_water"]
    assert [c["key"] for c in sug["s4"]] == ["personnel:p_wang"]  # PMP ≠ 信息系统项目管理师
    # 建议不会自动写入项目
    assert client.get(f"/api/v1/project/{project_id}").json()["evidence_links"] == {}


def test_link_validation_and_material_status(client, project_id):
    _setup(client, project_id)
    url = f"/api/v1/project/{project_id}/evidence"
    assert client.put(f"{url}/s2", json={"asset_keys": ["components:comp_01"]}).status_code == 400
    assert client.put(f"{url}/s2", json={"asset_keys": ["qualifications:nope"]}).status_code == 400
    assert client.put(f"{url}/s2", json={"asset_keys": ["qualifications:qual_cmmi5"]}).status_code == 400  # 示例
    assert client.put(f"{url}/nope", json={"asset_keys": []}).status_code == 404

    report = client.get(url).json()
    assert report["reference_date"] == "2026-10-20" and report["total"] == 3 and report["complete"] == 0
    assert {it["item_id"]: it["status"] for it in report["items"]} == {"s2": "未关联", "s3": "未关联", "s4": "未关联"}

    assert client.put(f"{url}/s2", json={"asset_keys": ["qualifications:q_iso20000"]}).status_code == 200
    s2 = next(it for it in client.get(url).json()["items"] if it["item_id"] == "s2")
    assert s2["status"] == "缺附件" and s2["links"][0]["name"].startswith("ISO20000")

    client.post("/api/v1/assets/qualifications/q_iso20000/attachments",
                files={"file": ("证书.pdf", PDF_BYTES, "application/pdf")})
    report = client.get(url).json()
    assert next(it for it in report["items"] if it["item_id"] == "s2")["status"] == "齐备"
    assert report["complete"] == 1

    # 所属主体与投标人全称不一致 → 主体不符；证书在投标截止日前到期 → 过期
    client.put(f"/api/v1/project/{project_id}/facts", json={"company_name": "重庆某某科技有限公司"})
    client.post("/api/v1/assets/qualifications", json={**ASSETS["qualifications"][0], "holder": "某某集团"})
    assert next(it for it in client.get(url).json()["items"] if it["item_id"] == "s2")["status"] == "主体不符"
    client.post("/api/v1/assets/qualifications", json={**ASSETS["qualifications"][0], "expiry_date": "2026-10-15"})
    assert next(it for it in client.get(url).json()["items"] if it["item_id"] == "s2")["status"] == "过期"

    # 资料被删除后关联失效；空列表取消关联
    client.delete("/api/v1/assets/qualifications/q_iso20000")
    s2 = next(it for it in client.get(url).json()["items"] if it["item_id"] == "s2")
    assert s2["status"] == "未关联" and s2["missing_links"] == ["qualifications:q_iso20000"]
    assert client.put(f"{url}/s2", json={"asset_keys": []}).status_code == 200
    assert "s2" not in client.get(f"/api/v1/project/{project_id}").json()["evidence_links"]


def test_quality_reports_material_separately(client, project_id):
    _setup(client, project_id)
    inspect = f"/api/v1/project/{project_id}/quality/inspect"
    before = client.post(inspect).json()
    rows = {c["item_id"]: c for c in before["scoring_coverage"]}
    assert rows["s1"]["material_status"] is None
    assert rows["s2"]["material_status"] == "未关联"
    assert before["material_total"] == 3 and before["material_complete"] == 0

    client.put(f"/api/v1/project/{project_id}/evidence/s4", json={"asset_keys": ["personnel:p_wang"]})
    client.post("/api/v1/assets/personnel/p_wang/attachments", files={"file": ("证书.pdf", PDF_BYTES, "application/pdf")})
    after = client.post(inspect).json()
    s4 = next(c for c in after["scoring_coverage"] if c["item_id"] == "s4")
    assert s4["material_status"] == "齐备" and s4["material_assets"] == ["王工"]
    assert after["material_complete"] == 1
    # 材料齐备与否不改变文字覆盖率与综合分
    assert after["scoring_coverage_rate"] == before["scoring_coverage_rate"]
    assert after["overall_score"] == before["overall_score"]
    d7 = next(d for d in after["dimensions"] if d["dimension_name"].startswith("7."))
    assert any("证明材料" in f and "1/3" in f for f in d7["findings"])
