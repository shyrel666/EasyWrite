"""历史示例分类审计只读，批量清理保留可疑资料及附件。"""
import copy
import json

import pytest

from app.models.schemas import CompanyQualification
from app.services.assets import asset_manager as module

Manager = module.EnterpriseAssetManager


def write_library(path, row):
    path.write_text(json.dumps({"qualifications": [row], "personnel": [], "cases": [], "components": []},
                               ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("extra,field", [({"name": "企业真实资质"}, "name"), ({"holder": "真实公司"}, "holder"),
                                       ({"attachments": [{"id": "proof", "filename": "证明.pdf"}]}, "attachments")])
def test_existing_example_audit_is_readonly_and_clear_protects_it(tmp_path, extra, field):
    path = tmp_path / "enterprise_assets.json"
    row = dict(copy.deepcopy(Manager.DEFAULT_QUALIFICATIONS[0]), status="example", **extra)
    write_library(path, row)
    before = path.read_bytes()
    manager = Manager(path)
    audit = manager.migration_audit()
    assert audit["total"] == 1 and field in audit["items"][0]["changed_fields"]
    assert path.read_bytes() == before and manager.qualifications[0]["status"] == "example"
    assert manager.clear_examples()["qualifications"] == 0
    assert path.read_bytes() == before
    assert Manager(path).migration_audit() == audit


def test_default_empty_fields_are_not_suspicious(tmp_path):
    path = tmp_path / "enterprise_assets.json"
    row = CompanyQualification(**Manager.DEFAULT_QUALIFICATIONS[0], status="example").model_dump()
    write_library(path, row)
    manager = Manager(path)
    assert manager.migration_audit()["total"] == 0
    assert manager.clear_examples()["qualifications"] == 1


@pytest.mark.parametrize("kind", module.ASSET_KINDS)
def test_audit_covers_all_preset_categories(tmp_path, kind):
    manager = Manager(tmp_path / "enterprise_assets.json")
    item = getattr(manager, kind)[0]
    field = "project_name" if kind == "cases" else "name"
    item[field] = "用户修改的真实资料"
    manager._save()
    manager = Manager(manager.storage_file)
    assert manager.migration_audit()["items"][0]["kind"] == kind
    manager.clear_examples()
    assert getattr(manager, kind)[0][field] == "用户修改的真实资料"


def test_legacy_migration_includes_new_fields_and_backups_once(tmp_path):
    path = tmp_path / "enterprise_assets.json"
    row = dict(copy.deepcopy(Manager.DEFAULT_QUALIFICATIONS[0]), holder="真实持有人")
    write_library(path, row)
    original = path.read_bytes()
    manager = Manager(path)
    assert manager.qualifications[0]["status"] == "unverified"
    history = manager.migration_audit()["migrations"]
    assert len(history) == 1 and history[0]["source"] == "legacy_status_v2"
    assert (tmp_path / history[0]["backup"]).read_bytes() == original
    assert history[0]["items"][0]["changed_fields"] == ["holder"]
    saved = path.read_bytes()
    assert Manager(path).migration_audit()["migrations"] == history
    assert path.read_bytes() == saved and len(list(tmp_path.glob("*.migration-*.bak"))) == 1


def test_backup_failure_does_not_overwrite_legacy_file(tmp_path, monkeypatch):
    path = tmp_path / "enterprise_assets.json"
    write_library(path, Manager.DEFAULT_QUALIFICATIONS[0])
    original = path.read_bytes()

    def fail(*args):
        raise OSError("备份失败")

    monkeypatch.setattr(module.shutil, "copy2", fail)
    with pytest.raises(OSError, match="备份失败"):
        Manager(path)
    assert path.read_bytes() == original


@pytest.mark.parametrize("status", ["unverified", "confirmed"])
def test_audit_api_protects_attachment_until_user_confirms_classification(client, tmp_path, monkeypatch, status):
    from app.api import assets as api_module
    manager = Manager(tmp_path / "enterprise_assets.json")
    monkeypatch.setattr(api_module, "asset_manager", manager)
    row = manager.qualifications[0]
    meta = manager.add_attachment("qualifications", row["id"], "真实证明.pdf", b"%PDF-1.4\n%%EOF\n")
    attachment, _ = manager.attachment_file("qualifications", row["id"], meta.id)
    before = manager.storage_file.read_bytes()
    audit = client.get("/api/v1/assets/migration-audit").json()
    assert audit["total"] == 1
    assert manager.storage_file.read_bytes() == before
    result = client.delete("/api/v1/assets/examples").json()
    assert result["protected"][0]["asset_id"] == row["id"]
    assert attachment.exists()
    response = client.post("/api/v1/assets/qualifications", json={**row, "status": status})
    assert response.status_code == 200 and response.json()["status"] == status
    assert client.get("/api/v1/assets/migration-audit").json()["total"] == 0
    assert attachment.exists() and len(manager.qualifications[0]["attachments"]) == 1
