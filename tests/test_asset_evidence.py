"""证明附件、所属主体、确认时间与证明材料检查（阶段 B1）"""
from datetime import date

from app.core.config import settings
from app.models.schemas import GlobalFacts, TenderAnalysis18
from app.services.assets.asset_manager import asset_manager
from app.services.assets.material_check import bid_deadline, check_material, parse_date

PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

QUAL = {
    "id": "qual_b1_iso",
    "name": "ISO9001 质量管理体系认证",
    "cert_no": "ISO-2025-001",
    "issue_org": "某认证中心",
    "expiry_date": "2028-05-31",
}


def _create_qual(client, **extra):
    res = client.post("/api/v1/assets/qualifications", json={**QUAL, **extra})
    assert res.status_code == 200
    return res.json()


def test_attachment_upload_download_delete(client):
    _create_qual(client)
    base = f"/api/v1/assets/qualifications/{QUAL['id']}/attachments"

    res = client.post(base, files={"file": ("ISO证书.pdf", PDF_BYTES, "application/pdf")})
    assert res.status_code == 200, res.text
    att = res.json()
    assert att["filename"] == "ISO证书.pdf" and att["size"] == len(PDF_BYTES) and len(att["sha256"]) == 64
    stored = settings.DATA_DIR / "asset_files" / QUAL["id"]
    assert (stored / f"{att['id']}.pdf").read_bytes() == PDF_BYTES

    listed = client.get(base).json()
    assert [a["id"] for a in listed] == [att["id"]]
    saved = next(q for q in client.get("/api/v1/assets/qualifications").json() if q["id"] == QUAL["id"])
    assert saved["attachments"][0]["id"] == att["id"]

    download = client.get(f"{base}/{att['id']}")
    assert download.status_code == 200 and download.content == PDF_BYTES
    assert download.headers["content-type"] == "application/pdf"

    assert client.delete(f"{base}/{att['id']}").status_code == 200
    assert client.get(base).json() == []
    assert not (stored / f"{att['id']}.pdf").exists()
    assert client.delete(f"{base}/{att['id']}").status_code == 404


def test_attachment_validation(client):
    _create_qual(client)
    base = f"/api/v1/assets/qualifications/{QUAL['id']}/attachments"
    # 扩展名不在白名单 / 内容与扩展名不符 / 空文件
    assert client.post(base, files={"file": ("a.exe", b"MZ....", "application/octet-stream")}).status_code == 400
    assert client.post(base, files={"file": ("fake.pdf", PNG_BYTES, "application/pdf")}).status_code == 400
    assert client.post(base, files={"file": ("empty.png", b"", "image/png")}).status_code == 400
    assert client.post(base, files={"file": ("scan.png", PNG_BYTES, "image/png")}).status_code == 200
    # 超过 20MB
    big = b"%PDF-" + b"0" * (20 * 1024 * 1024)
    res = client.post(base, files={"file": ("big.pdf", big, "application/pdf")})
    assert res.status_code == 400 and "20MB" in res.json()["detail"]
    # 方案组件不收附件；资料不存在 → 404
    comp = client.post("/api/v1/assets/components", json={"id": "comp_b1", "name": "组件", "content": "正文"}).json()
    assert client.post(f"/api/v1/assets/components/{comp['id']}/attachments",
                       files={"file": ("a.pdf", PDF_BYTES, "application/pdf")}).status_code == 400
    assert client.post("/api/v1/assets/qualifications/nope/attachments",
                       files={"file": ("a.pdf", PDF_BYTES, "application/pdf")}).status_code == 404


def test_edit_keeps_attachments_and_tracks_confirmation(client):
    created = _create_qual(client, status="unverified")
    assert created["confirmed_at"] == "" and created["attachments"] == []
    base = f"/api/v1/assets/qualifications/{QUAL['id']}/attachments"
    att = client.post(base, files={"file": ("证书.pdf", PDF_BYTES, "application/pdf")}).json()

    # 编辑时客户端提交的附件被忽略，服务端已有附件保留
    confirmed = _create_qual(client, status="confirmed", holder="某某科技有限公司",
                             attachments=[{"id": "att_forged", "filename": "x.pdf"}])
    assert [a["id"] for a in confirmed["attachments"]] == [att["id"]]
    assert confirmed["holder"] == "某某科技有限公司"
    assert confirmed["confirmed_at"]
    # 已确认的条目再次保存保留原确认时间；改回待核实时清空
    again = _create_qual(client, status="confirmed", summary="改了说明")
    assert again["confirmed_at"] == confirmed["confirmed_at"]
    assert _create_qual(client, status="unverified")["confirmed_at"] == ""

    # 删除资料连同附件目录
    assert client.delete(f"/api/v1/assets/qualifications/{QUAL['id']}").status_code == 200
    assert not (settings.DATA_DIR / "asset_files" / QUAL["id"]).exists()


def test_attachment_dir_never_escapes_storage():
    folder = asset_manager._asset_dir("../../evil")
    assert folder.parent == asset_manager.files_dir and ".." not in folder.name


def test_check_material_rules():
    today = date(2026, 10, 7)
    deadline = date(2026, 10, 20)
    facts = GlobalFacts(company_name="某某（重庆）科技有限公司")
    ok_att = [{"id": "att_1", "filename": "a.pdf"}]

    def check(kind="qualifications", **fields):
        item = {"id": "x", "name": "证书", "status": "confirmed", "attachments": ok_att, **fields}
        return check_material(kind, item, facts, deadline, today=today)

    good = check(expiry_date="2027-01-01", holder=" 某某(重庆)科技有限公司")  # 全半角括号、空白不算不一致
    assert good.status == "齐备" and good.problems == []
    assert check(expiry_date="长期有效").status == "齐备"
    assert check(attachments=[]).status == "缺附件"
    assert check(status="unverified").status == "待核实"
    assert check(status="example").status == "示例资料"
    assert check(expiry_date="2026-09-30").problems[0].code == "expired"
    expiring = check(expiry_date="2026年10月15日")
    assert expiring.status == "过期" and expiring.problems[0].code == "expiring"
    assert check(expiry_date="2026-09").problems[0].code == "expired"  # 只有年月按月末算
    assert check(expiry_date="2026-10").status == "齐备"
    assert check(expiry_date="三年").problems[0].code == "expiry_unknown"
    mismatch = check(holder="某某集团有限公司")
    assert mismatch.status == "主体不符" and "某某集团有限公司" in mismatch.problems[0].message
    # 多个问题取最严重的状态；人员/业绩不检查有效期
    assert check(attachments=[], expiry_date="2020-01-01").status == "过期"
    assert check("personnel", expiry_date="2020-01-01").status == "齐备"
    # 未填投标人全称时不核对所属主体
    no_name = check_material("cases", {"id": "c", "project_name": "某业绩", "holder": "别家公司", "attachments": ok_att},
                             GlobalFacts(), deadline, today=today)
    assert no_name.status == "齐备" and no_name.name == "某业绩"


def test_bid_deadline_parsing():
    today = date(2026, 10, 7)
    ta = TenderAnalysis18(submission_deadline="2026年10月20日09:30（北京时间）")
    assert bid_deadline(ta, today) == (date(2026, 10, 20), "投标截止时间")
    assert bid_deadline(TenderAnalysis18(submission_deadline="未提及"), today)[0] == today
    assert bid_deadline(None, today)[0] == today
    assert parse_date("2026/2/30") is None


def test_material_check_endpoint(client, project_id):
    _create_qual(client, holder="另一家公司")
    client.put(f"/api/v1/project/{project_id}/facts", json={"company_name": "重庆某某科技有限公司"})

    res = client.get(f"/api/v1/assets/material-check?project_id={project_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["company_name"] == "重庆某某科技有限公司"
    check = next(c for c in data["checks"] if c["asset_id"] == QUAL["id"])
    assert {p["code"] for p in check["problems"]} == {"no_attachment", "holder_mismatch"}
    assert check["status"] == "主体不符"
    # 示例资料一律标为"示例资料"；不传项目时不核对所属主体
    examples = [c for c in data["checks"] if c["asset_status"] == "example"]
    assert examples and all(c["status"] == "示例资料" for c in examples)
    plain = client.get("/api/v1/assets/material-check").json()
    assert next(c for c in plain["checks"] if c["asset_id"] == QUAL["id"])["status"] == "缺附件"
    assert client.get("/api/v1/assets/material-check?project_id=nope").status_code == 404
