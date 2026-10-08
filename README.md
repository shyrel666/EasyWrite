<p align="center">
  <img src="docs/assets/easywrite-logo.svg" width="96" height="96" alt="EasyWrite Logo">
</p>

<h1 align="center">EasyWrite</h1>

<p align="center">
  <strong>本地运行的智能招投标技术标编纂系统</strong>
</p>

<p align="center">
  招标解析 · 大纲规划 · 章节撰写 · 核查整改 · Word 导出
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-v0.2.0-4F7FC9?style=flat-square" alt="Version 0.2.0">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-64748B?style=flat-square" alt="Windows, macOS and Linux">
  <img src="https://img.shields.io/badge/AI-OpenAI%20compatible-0D9488?style=flat-square" alt="OpenAI 兼容接口">
  <a href="https://github.com/shyrel666/EasyWrite/stargazers"><img src="https://img.shields.io/github/stars/shyrel666/EasyWrite?style=flat-square&amp;label=stars&amp;color=E3B341" alt="GitHub stars"></a>
</p>

<p align="center">
  <a href="#快速开始"><strong>快速开始</strong></a> ·
  <a href="#核心功能">核心功能</a> ·
  <a href="#开发与测试">开发与测试</a> ·
  <a href="IMPROVEMENT_PLAN.md">改进计划</a> ·
  <a href="https://github.com/shyrel666/EasyWrite/issues">问题反馈</a>
</p>

## 快速开始

下载或克隆完整项目，在项目根目录启动。无需预装 Python 或 Node.js。

| 平台 | 启动方式 |
| --- | --- |
| Windows | 双击 `start.bat`，或在 PowerShell 执行 `.\start.bat` |
| macOS / Linux | 在终端执行 `sh start.sh` |

首次运行需联网，使用国内镜像自动准备 Python 3.12 与依赖，保存在项目的 `.runtime/` 目录中，后续启动复用缓存。仓库已附带前端构建产物。

服务就绪后自动打开 [http://127.0.0.1:8790/](http://127.0.0.1:8790/)，保持终端窗口打开，按 `Ctrl+C` 停止。

> [!NOTE]
> 未配置生成模型时进入离线演示模式。在「系统设置」中填写 OpenAI 兼容接口的地址、模型与 API Key 即可启用真实生成，无需重启。嵌入模型可选，未配置时使用 BM25 + LLM 重排。

<details>
<summary>启动选项与镜像配置</summary>

支持 64 位 Windows、macOS 和 Linux（x64 / ARM64）。macOS / Linux 需有 `curl` 或 `wget`、`unzip` 及 `sha256sum` 或 `shasum`。

指定端口并关闭自动打开浏览器：

- Windows：`.\start.bat --port 8001 --no-browser`
- macOS / Linux：`sh start.sh --port 8001 --no-browser`

下载源可通过环境变量覆盖，脚本保留已有设置：

| 环境变量 | 用途 | 默认值 |
| --- | --- | --- |
| `UV_DEFAULT_INDEX`（兼容 `UV_INDEX_URL`） | 后端依赖 | `https://pypi.tuna.tsinghua.edu.cn/simple` |
| `UV_PYTHON_INSTALL_MIRROR` | Python 运行时 | `https://registry.npmmirror.com/-/binary/python-build-standalone` |
| `EASYWRITE_UV_MIRROR` | uv 启动工具 | `https://pypi.tuna.tsinghua.edu.cn` |

AI 设置保存于 `backend/data/ai_settings.json`；也可使用 `backend/.env`，配置项见 [环境变量示例](backend/.env.example)。这两个配置文件均由 Git 忽略。企业资料保存于 `backend/data/enterprise_assets.json`，证明附件保存于 `backend/data/asset_files/`，同样由 Git 忽略；首次启动生成的示例资料不会写入标书，可在「企业资产」页一键清除。

</details>

## 核心功能

面向政企信息化与软件工程项目，将招标要求、历史资料与企业资产组织成可核查、可导出的技术标书。

**招标解析 → 大纲确认 → 章节撰写 → 核查整改 → Word 导出**

| 功能 | 说明 |
| --- | --- |
| 招标解析 | 解析 `.docx` / 文字型 `.pdf`，提取 18 项核心要素、★条款与评分办法 |
| 大纲规划 | 对齐评分项、分配字数预算，一级大纲经人工确认后展开 |
| 章节撰写 | 流式生成、跨章上下文与全局事实约束，支持人工编辑；可按章节阅读招标原文并高亮本节要点，查看每次撰写用到的知识库片段和企业资料；批量重写与定向修订的结果先成为候选稿，查看差异与检查报告后再采纳，正文或依据已变化时不会覆盖；"智能完善"对单个章节自动起草（或以现有正文为原稿）→ 规则检查 → 按问题定向修订（默认最多 2 轮），没有进展时换方法、只处理阻塞问题或停止，资料不足时如实列出缺口，结果为带检查报告的候选稿；每次运行有模型请求次数与时长上限，中断或预算用尽后可继续，不会重新获得修订轮数 |
| 知识与资产 | 历史标书混合检索、引用溯源与锁定/排除，复用资质、人员、业绩和方案组件；资料可附证明附件，按核实状态、有效期与所属主体检查材料是否齐备 |
| 核查整改 | 技术偏离表逐项应答、废标条款核查与八维质检；证明材料类评分项关联企业资料，单独给出“材料齐备”结论；单章检查离线可用，列出漏写的评分要点、与全局事实冲突的承诺数值、示例或未标注的待核实资料，以及待核实事项 |
| Word 导出 | 多套模板、原生目录/页码/表格与 Mermaid 图，偏离表可单独导出；导出前列出未撰写章节、剩余占位、材料缺口、未校审与红线核查结果 |

暂不支持扫描件、加密或无法提取文本的 PDF，`.doc` 请先转为 `.docx`。导出后在 Word 中更新目录域。

## 技术栈

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Vue 3](https://img.shields.io/badge/Vue-3-4FC08D?logo=vuedotjs&logoColor=white)

| 层级 | 技术 |
| --- | --- |
| 后端 | FastAPI · Pydantic v2 · SQLModel · SQLite（WAL） |
| 前端 | Vue 3 · Vite · Pinia · Element Plus · Tailwind CSS |
| 检索 | BM25 + 向量召回 → RRF 融合 → LLM 重排 |
| 文档 | PyMuPDF · python-docx · markdown-it-py · Mermaid |

FastAPI 托管前端与 API；后台任务持久化，可查看进度与历史。模型调用记录提供 Token 用量、耗时、项目归属，并可按运行（后台任务）汇总。

## 开发与测试

<details>
<summary>手动启动、前端开发与测试命令</summary>

需 Python 3.10+；前端构建和开发还需 Node.js 与 npm。从项目根目录执行：

```bash
python -m pip install -r backend/requirements-dev.txt
npm --prefix frontend ci
npm --prefix frontend run build
cd backend
python -m uvicorn app.main:app --port 8790
```

前端构建输出至 `backend/app/static/`；已有构建产物时可跳过两条 `npm` 命令。API 文档：[http://127.0.0.1:8790/docs](http://127.0.0.1:8790/docs)。

前端开发时，在另一个终端从项目根目录执行 `npm --prefix frontend run dev`，访问 [http://localhost:5173/](http://localhost:5173/)，API 自动代理至后端的 8790 端口。

测试与代码检查从项目根目录执行：

```bash
python -m pytest tests/ -q
python -m ruff check .
npm --prefix frontend test
```

测试使用独立临时数据目录与空密钥，不读取真实模型密钥或写入运行数据。

</details>

## 开源参考

参考 [易标（OpenBidKit_Yibiao）](https://github.com/FB208/OpenBidKit_Yibiao) 的招标解析、全局事实约束、废标检查与知识库设计思路，感谢原作者的开源贡献。

后续优化的实施路线与验收标准见 [改进计划](IMPROVEMENT_PLAN.md)。
