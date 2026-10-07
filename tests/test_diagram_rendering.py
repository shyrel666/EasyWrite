import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BASE_DIR))

from app.services.exporter.diagram_renderer import diagram_renderer
from app.services.exporter.docx_generator import docx_exporter
from app.models.schemas import OutlineNode

def test_diagram_rendering_standalone():
    print("[1] 单元测试：离线高清晰度拓扑图渲染引擎...")
    sample_mermaid = """
    graph TD
        User[终端用户 / 业务前台] --> Gateway[API智能网关 / 安全鉴权中心]
        Gateway --> Svc1[核心业务微服务群]
        Gateway --> Svc2[数据治理与共享服务]
        Svc1 --> DB[(主备高可用数据库集群)]
        Svc2 --> DB
    """
    img_path = diagram_renderer.render_to_image(sample_mermaid, diagram_title="测试微服务逻辑架构图")
    assert img_path is not None, "未生成图片路径"
    assert img_path.exists(), "渲染的拓扑图片文件不存在"
    assert img_path.stat().st_size > 10000, "生成的图片体积过小"
    print(f"    成功渲染架构拓扑图片: {img_path.name}, 体积: {img_path.stat().st_size} 字节")

def test_docx_export_with_real_image():
    print("\n[2] 单元测试：高保真 Word 导出真实图片嵌入...")
    sample_outline = [
        OutlineNode(
            id="sec_1",
            title="第一章 总体技术方案",
            level=1,
            path="第一章 总体技术方案",
            content=(
                "以下为系统总体逻辑架构设计拓扑图：\n\n"
                "```mermaid\n"
                "graph TD\n"
                "    Frontend[前端UI门户] --> API[智能网关]\n"
                "    API --> Microservice[核心微服务]\n"
                "    Microservice --> Storage[(信创达梦数据库)]\n"
                "```\n\n"
                "如上图所示，系统采用微服务分层拓扑架构。"
            )
        )
    ]

    out_file = docx_exporter.export_project_to_docx(
        project_name="测试架构图嵌入项目",
        client_name="采购方",
        outline=sample_outline,
        output_filename="测试输出_含真实架构图标书.docx"
    )
    assert out_file.exists()
    assert out_file.stat().st_size > 15000
    print(f"    成功导出包含真实架构图的标书 Word: {out_file.name}, 大小: {out_file.stat().st_size} 字节")

if __name__ == "__main__":
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    test_diagram_rendering_standalone()
    test_docx_export_with_real_image()
