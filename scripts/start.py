"""一键启动入口：使用已构建的前端，服务就绪后打开浏览器。"""

import argparse
import os
import re
import socket
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
STATIC_DIR = BACKEND_DIR / "app" / "static"


class StartupError(Exception):
    """可直接展示给用户的启动错误。"""


def port_number(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("端口必须是 1 到 65535 之间的整数") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("端口必须是 1 到 65535 之间的整数")
    return port


def validate_frontend(static_dir: Path) -> None:
    """确认入口及其直接引用的静态资源齐全，避免打开空白工作台。"""
    index_file = static_dir / "index.html"
    if index_file.is_file():
        html = index_file.read_text(encoding="utf-8")
        assets = re.findall(r'''(?:src|href)=["'](/assets/[^"']+)["']''', html)
        if assets and all((static_dir / urlsplit(asset).path.lstrip("/")).is_file() for asset in assets):
            return
    raise StartupError(
        "前端构建产物缺失或不完整。请重新下载完整项目；"
        "如需从源码构建，先安装 Node.js，再在 frontend 目录执行 npm ci 和 npm run build。"
    )


def announce_when_ready(server, url: str, open_browser: bool, stopped: threading.Event) -> None:
    """等待 Uvicorn 完成应用初始化和监听，再展示地址并打开浏览器。"""
    while not server.started:
        if stopped.wait(0.1):
            return
    print(f"\nEasyWrite 已启动：{url}", flush=True)
    print(f"API 文档：{url}docs", flush=True)
    print("按 Ctrl+C 停止服务。首次使用可在「系统设置」中配置大模型。\n", flush=True)
    if open_browser:
        try:
            if not webbrowser.open(url):
                print(f"未能自动打开浏览器，请手动访问 {url}", flush=True)
        except Exception:
            print(f"未能自动打开浏览器，请手动访问 {url}", flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="start", description="启动 EasyWrite 工作台（API + Web 界面）")
    parser.add_argument("--port", type=port_number, default=8790, help="服务端口，默认 8790")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args(argv)

    try:
        validate_frontend(STATIC_DIR)
        # 始终从 backend 启动，保证 .env 的解析不依赖用户当前所在目录。
        os.chdir(BACKEND_DIR)
        sys.path.insert(0, str(BACKEND_DIR))
        try:
            import uvicorn
        except ImportError as exc:
            raise StartupError("后端依赖未准备好。请使用项目根目录的 start.bat 或 sh start.sh 启动。") from exc

        # 提前占住端口；冲突时不导入应用、不写入数据库，也不打开浏览器。
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind(("127.0.0.1", args.port))
            except OSError as exc:
                raise StartupError(
                    f"无法监听端口 {args.port}：{exc}。请关闭占用端口的程序，或使用 --port 指定其他端口。"
                ) from exc

            config = uvicorn.Config("app.main:app", host="127.0.0.1", port=args.port)
            server = uvicorn.Server(config)
            stopped = threading.Event()
            announcer = threading.Thread(
                target=announce_when_ready,
                args=(server, f"http://127.0.0.1:{args.port}/", not args.no_browser, stopped),
                daemon=True,
            )
            announcer.start()
            try:
                server.run(sockets=[listener])
            finally:
                stopped.set()
            return 0 if server.started else 1
    except StartupError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr, flush=True)
        return 1
    except OSError as exc:
        print(f"[ERROR] 启动失败：{exc}", file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    # Windows 重定向输出时可能使用西文编码，统一为 UTF-8 以正常显示中文提示。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
