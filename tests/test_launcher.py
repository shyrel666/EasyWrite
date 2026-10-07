"""启动入口集成测试：新下载目录、.env 解析、端口冲突与不完整的前端。"""

import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_PATHS = (
    "DATA_DIR", "DB_PATH", "UPLOAD_DIR", "KNOWLEDGE_DIR",
    "OUTPUT_DIR", "RENDERED_DIR", "TEMPLATE_DIR",
)


@pytest.fixture
def downloaded_project(tmp_path):
    """只复制发布源码和静态资源，不携带用户环境、数据库或配置。"""
    project = tmp_path / "EasyWrite fresh copy 中文"
    shutil.copytree(
        PROJECT_ROOT / "backend" / "app", project / "backend" / "app",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (project / "scripts").mkdir()
    shutil.copy2(PROJECT_ROOT / "scripts" / "start.py", project / "scripts" / "start.py")
    (project / "backend" / ".env").write_text(
        "VERSION=launcher-smoke\nLLM_API_KEY=\nEMBEDDING_API_KEY=\n", encoding="utf-8",
    )
    return project


def launcher_env():
    env = os.environ.copy()
    # 子进程必须用下载副本自己的数据路径，而不是 conftest 的临时运行目录。
    for name in (*RUNTIME_PATHS, "VERSION", "PYTHONPATH", "PYTHONHOME"):
        env.pop(name, None)
    env.update(PYTHONUTF8="1", LLM_API_KEY="", EMBEDDING_API_KEY="")
    return env


def test_launch_downloaded_project_from_another_directory(downloaded_project, tmp_path):
    """真实启动后验证首页、静态资源、深链接、API 和本地 .env。"""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    opener = build_opener(ProxyHandler({}))
    log_path = tmp_path / "launcher.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, str(downloaded_project / "scripts" / "start.py"),
             "--port", str(port), "--no-browser"],
            cwd=tmp_path, env=launcher_env(), stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 30
            while True:
                assert process.poll() is None, log_path.read_text(encoding="utf-8")
                try:
                    with opener.open(f"{url}/health", timeout=1) as response:
                        health = json.load(response)
                    break
                except OSError:
                    assert time.monotonic() < deadline, log_path.read_text(encoding="utf-8")
                    time.sleep(0.1)
            assert health == {"status": "ok", "version": "launcher-smoke"}
            with opener.open(Request(f"{url}/", headers={"Accept": "text/html"}), timeout=2) as response:
                html = response.read().decode("utf-8")
            assert 'id="app"' in html
            asset = re.search(r'src="(/assets/[^\"]+\.js)"', html).group(1)
            with opener.open(f"{url}{asset}", timeout=2) as response:
                assert len(response.read()) > 1000
            with opener.open(
                Request(f"{url}/project/example/workspace", headers={"Accept": "text/html"}), timeout=2,
            ) as response:
                assert 'id="app"' in response.read().decode("utf-8")
            with opener.open(f"{url}/api/v1/projects", timeout=2) as response:
                assert json.load(response) == []
            with opener.open(f"{url}/api/v1/ai/status", timeout=2) as response:
                assert json.load(response)["llm_configured"] is False
            assert (downloaded_project / "backend" / "data" / "easywrite.db").is_file()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_occupied_port_fails_before_initializing_application(downloaded_project, tmp_path):
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        result = subprocess.run(
            [sys.executable, str(downloaded_project / "scripts" / "start.py"),
             "--port", str(occupied.getsockname()[1]), "--no-browser"],
            cwd=tmp_path, env=launcher_env(), capture_output=True, encoding="utf-8", timeout=10,
        )
    assert result.returncode == 1
    assert "--port" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (downloaded_project / "backend" / "data").exists()


def test_incomplete_frontend_fails_with_build_instructions(downloaded_project, tmp_path):
    (downloaded_project / "backend" / "app" / "static" / "index.html").write_text(
        '<script src="/assets/missing.js"></script>', encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(downloaded_project / "scripts" / "start.py"), "--no-browser"],
        cwd=tmp_path, env=launcher_env(), capture_output=True, encoding="utf-8", timeout=10,
    )
    assert result.returncode == 1
    assert "npm ci" in result.stderr
    assert "npm run build" in result.stderr
    assert not (downloaded_project / "backend" / "data").exists()


@pytest.mark.parametrize("open_browser", [True, False])
def test_browser_waits_for_service_readiness(monkeypatch, open_browser):
    spec = importlib.util.spec_from_file_location("easywrite_launcher", PROJECT_ROOT / "scripts" / "start.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    observed = threading.Event()
    ready = threading.Event()
    stopped = threading.Event()
    opened_urls = []

    class PendingServer:
        @property
        def started(self):
            observed.set()
            return ready.is_set()

    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened_urls.append(url) or True)
    announcer = threading.Thread(
        target=launcher.announce_when_ready,
        args=(PendingServer(), "http://127.0.0.1:8790/", open_browser, stopped),
    )
    announcer.start()
    try:
        assert observed.wait(timeout=2)
        assert opened_urls == []
        ready.set()
        announcer.join(timeout=2)
        assert not announcer.is_alive()
        assert opened_urls == (["http://127.0.0.1:8790/"] if open_browser else [])
    finally:
        stopped.set()
        announcer.join(timeout=2)


@pytest.mark.skipif(os.name != "nt", reason="Windows 启动器校验流程")
def test_windows_bootstrap_rejects_incomplete_mirror_download(downloaded_project, tmp_path):
    """镜像返回损坏文件时，启动器必须在执行二进制之前失败。"""
    for filename in ("start.ps1", "uv-downloads.txt"):
        shutil.copy2(PROJECT_ROOT / "scripts" / filename, downloaded_project / "scripts" / filename)

    class IncompleteDownload(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = b"incomplete wheel"
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), IncompleteDownload) as mirror:
        worker = threading.Thread(target=mirror.serve_forever, daemon=True)
        worker.start()
        env = launcher_env()
        env["EASYWRITE_UV_MIRROR"] = f"http://127.0.0.1:{mirror.server_port}"
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 str(downloaded_project / "scripts" / "start.ps1"), "--no-browser"],
                cwd=tmp_path, env=env, capture_output=True, encoding="utf-8", timeout=15,
            )
        finally:
            mirror.shutdown()
            worker.join(timeout=2)
    assert result.returncode == 1
    assert "SHA256" in result.stdout
    assert not (downloaded_project / ".runtime" / "uv" / "uv.exe").exists()
    assert not (downloaded_project / "backend" / "data").exists()
