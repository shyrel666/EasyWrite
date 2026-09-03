import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BASE_DIR))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_api_workflow():
    # 1. 健康检测
    res = client.get("/")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "online"
    print("[1] GET / 健康检测通过:", data["system"])

    # 2. 新建标书项目
    proj_payload = {
        "name": "智慧医院信息化集成与综合管理平台",
        "client_name": "市第一人民医院",
        "description": "需要包含HIS互联互通、微服务架构、等保三级安全及容灾备份方案"
    }
    res = client.post("/api/v1/project/create", json=proj_payload)
    assert res.status_code == 200
    proj = res.json()
    proj_id = proj["id"]
    print(f"[2] POST /api/v1/project/create 创建成功，ID: {proj_id}, 包含根大纲数: {len(proj['outline'])}")
    assert len(proj["outline"]) >= 3

    # 3. 分章智能草拟
    gen_payload = {
        "project_id": proj_id,
        "section_id": "sec_2_1",
        "section_title": "2.1 系统总体逻辑架构设计",
        "section_path": "第二章 总体技术架构与方案设计 > 2.1 系统总体逻辑架构设计",
        "requirements": ["微服务分层设计", "高内聚低耦合原则"],
        "custom_instruction": "突出面向医院高并发就诊业务场景"
    }
    res = client.post(f"/api/v1/project/{proj_id}/section/generate", json=gen_payload)
    assert res.status_code == 200
    gen_res = res.json()
    print(f"[3] POST 分章草拟生成成功，生成字符数: {len(gen_res['generated_content'])}")
    assert len(gen_res["generated_content"]) > 50

    # 4. 人工修改章节正文
    edit_res = client.put(
        f"/api/v1/project/{proj_id}/section",
        json={"section_id": "sec_2_1", "content": "【人工审核修改后】本系统总体架构完全满足三甲医院数字化标准。"}
    )
    assert edit_res.status_code == 200
    print("[4] PUT 人工章节修改保存成功")

    # 5. 导出整本标书 Word
    export_res = client.get(f"/api/v1/project/{proj_id}/export")
    assert export_res.status_code == 200
    assert len(export_res.content) > 5000
    print(f"[5] GET /export 标书 Word 下载接口验证通过，文件二进制大小: {len(export_res.content)} 字节")

    print("\n[SUCCESS] API 全流程接口集成测试通过！")

if __name__ == "__main__":
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    test_api_workflow()
