# EasyWrite v0.2.0 重构实施记录

> 本文件为 2026-09 重构的工程实施记录。详细架构说明见 [walkthrough.md](walkthrough.md)，使用文档见 [README.md](README.md)。

## 实施阶段与交付物

### Phase 0 · 地基修复
- 修复 requirements.txt（移除未使用的 docxtpl/chromadb/sentence-transformers/tiktoken，补齐 sqlmodel/rank-bm25/jieba/numpy/matplotlib）
- 清理假测试 Key（旧配置残留导致真实 401 请求后静默降级）
- 重写 LLM 客户端：同步+异步双客户端、模式诚实信号 `get_mode()`、真实模型预设（移除虚构的 deepseek-v4-pro）
- 新增 embedding_client（嵌入与生成模型解耦）、task_manager（后台任务+进度+取消）
- 新增 SQLite 持久层（SQLModel/WAL）+ 旧 JSON 数据幂等迁移
- 全量删除伪造默认值（拆标编造★条款/资质/付款比例、偏离表假条款、合规假条款、封面硬编码日期）

### Phase 1 · RAG 分层混合检索
- 新增模块：chunker（章节感知分块+块过滤+同节尾部合并）、enricher（LLM 定位摘要，可选）、indexer（BM25+稠密双索引+RRF）、retriever（多查询→双路召回→RRF→LLM重排→阈值→父级扩展）、curator（LLM 条目策展）、ingestor（入库管线编排）
- 知识库 API：上传（后台任务）/文档管理/重索引/策展/混合检索/条目查询

### Phase 2 · 核心流水线
- tender_analyzer：LLM 优先分段抽取 + 哨兵值纪律 + 规则只做补充
- outline_generator：评分项对齐 + 一级人工确认门 + 字数预算分配
- section_generator：检索注入 + 跨章连贯上下文 + 事实完备模式（omit/placeholder）+ 异步流式
- deviation_engine：确定性抽取（指纹+覆盖率）+ 手编落库 + 不伪造响应
- compliance_checker：LLM 多轮证据核查制（范围界定→逐条款证据→定稿）+ 规则快扫前置
- docx_generator：目录域 + 页码域 + 真实 Heading 样式 + eastAsia 字体 + 真实日期
- API 拆分：53 条路由按 10 个域组织，长任务统一走 task 轮询

### Phase 3 · 前端重写
- Vue 3 + Vite + Pinia + vue-router + Element Plus + Tailwind 本地构建（零 CDN 依赖）
- 9 个视图：仪表盘/向导/编纂台/偏离表/质检/知识库/资产/模板/设置
- 编纂台三栏（大纲树/编辑器/引用溯源抽屉）+ 自动保存 + SSE 流式 + 引用锁定/排除
- 响应式三档：移动端底部标签栏 / 平板可折叠 / 桌面三栏
- 构建产物输出至 backend/app/static，FastAPI 单进程全托管

### Phase 4 · 测试与文档
- 测试套件全部重写：**48 项测试全部通过**
- RAG 金标准评估（4 领域合成知识库 + 跨领域干扰查询，含"消防改造 vs 消防工程"复现实验）
- README / walkthrough / implementation_plan 文档更新

## 关键 Bug 修复记录

| 问题 | 根因 | 修复 |
| :--- | :--- | :--- |
| 金标准查询 top-1 被无关章节污染 | 小章节（<60字）被跨节尾部合并，面包屑归属丢失 | 合并限制在同一小节内，小章节独立成块 |
| 项目列表接口 500 | ORM 实例在 Session 关闭后访问过期属性 | 领域转换移入 Session 作用域 |
| 偏离表离线伪造"完全满足" | 模拟器文本被当作真实 LLM 输出接受 | 仅接受 `get_mode()=="llm"` 的输出 |
| API 设置接口泄露明文 Key | 掩码逻辑只写 api_key_masked 未移除明文 | 回传配置强制置空 api_key |
| 工期字段规则漏提取 | 正则未覆盖"工期要求："变体 | 补全"X要求"词形 |
| 旧测试污染配置致整批失败 | 旧测试残留假 Key | conftest 会话级配置快照/恢复隔离 |

## 验收结果

- `pytest tests/ -q`：**48 passed**
- 全流程冒烟：创建项目 → 拆标 → 大纲 → SSE 流式撰写 → 偏离表 → 八维质检 → 合规任务 → Word 导出（含目录/页码域）全链路通过
- 生产模式：`uvicorn app.main:app` 单进程托管 SPA + API 验证通过
