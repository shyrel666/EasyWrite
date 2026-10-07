# 易标参考项目对比与借鉴建议

整理日期：2026-10-07。

本文汇总易标与 EasyWrite 的功能差异、近期修复和值得借鉴的设计，用于后续需求与开发规划。文中的优先级和落地方式是结合 EasyWrite 当前代码提出的建议，尚未开发的内容不计入现有功能。

## 1. 对比基准

- 参考项目：[易标（OpenBidKit_Yibiao）](https://github.com/FB208/OpenBidKit_Yibiao)。
- 已核对版本：[v2.30.14](https://github.com/FB208/OpenBidKit_Yibiao/releases/tag/v2.30.14)，提交 `91c9fe447266e769ccfb435741e06de182079549`，发布日期为 2026-10-06。下文源码链接固定到该提交。
- 当前项目：EasyWrite，已提交基线为 `1d59c5f`，同时纳入整理时工作区中的未提交代码。
- 实现关系：EasyWrite 参考易标的业务设计思路，采用 FastAPI、Vue 3 与 Python 文档处理实现；易标采用 Electron、React、TypeScript 与 Open XML 助手。功能比较按实际行为核对。
- Agent 状态：EasyWrite 的 [Agent 设计方案](AGENT_DESIGN.md)明确标注“尚未实现”。本文与该方案衔接，不将方案内容描述为已交付功能。

## 2. 功能差异

双方共有招标文件18项解析、全局事实、知识库、企业资信、废标检查、模型接入和 Word 导出等能力。

| 方向 | 易标已具备的能力 | EasyWrite 当前情况 |
| --- | --- | --- |
| Agent 编写与修订 | 工具调用、并发生成、检查修订、暂停与继续 | 单章、流式和批量生成已有业务接口；自主工具调用与持续修订尚未实现 |
| 项目流程 | 多招标文件、多标段、多阶段投标、已有方案导入扩写 | 已支持评分细则按目标分包选择；复杂投标流程尚需补充 |
| 标书检查 | 专门的多份标书查重，以及废标项检查 | 侧重当前项目的废标核查、评分覆盖和八维质量检查 |
| 配图 | AI 生图、HTML 绘图、Mermaid 本地渲染 | 主要支持 Mermaid；完整渲染图片目前由前端提交 |
| 文档输入 | 更多本地文件格式，以及可选的 MinerU 解析 | 支持 `.docx`、文字型 PDF；扫描件、加密或无法提取文字的 PDF 会明确报错 |
| 扩展与交付 | 插件、资源下载、桌面安装包，以及可研报告 Beta | 浏览器工作台，提供自动准备运行环境的启动脚本 |

依据：[易标功能说明][ref-readme]、[文档解析服务][ref-file]、[业务菜单][ref-menu]。

EasyWrite 已有的专门实现包括：

| 方向 | 当前实现与用途 | 代码入口 |
| --- | --- | --- |
| 评分驱动编写 | 结构化提取评分项、分值与子项，关联大纲、分配篇幅，并按分值计算要点覆盖率 | [评分提取](backend/app/services/parser/scoring_extractor.py)、[大纲规划](backend/app/services/generator/rubric_planner.py)、[覆盖检查](backend/app/services/checker/scoring_coverage.py) |
| 混合检索 | BM25 与向量双路召回、RRF 融合、模型重排，支持引用溯源、锁定与排除 | [检索服务](backend/app/services/rag/retriever.py) |
| 技术偏离表 | 按技术要求提取条款、逐项应答，可独立导出 Word | [偏离引擎](backend/app/services/parser/deviation_engine.py)、[导出接口](backend/app/api/export.py) |
| 章节历史版本 | AI 覆盖前保留人工稿，记录生成、润色、批量和恢复版本 | [版本仓储](backend/app/services/version_store.py)、[章节接口](backend/app/api/sections.py) |
| 调用记录 | 记录模型请求用量、耗时、状态、用途和项目归属；缺少服务商用量时保留未知 | [调用记录](backend/app/core/llm_usage.py) |

这些实现描述的是当前能力，并不表示易标没有类似业务概念。评分覆盖率只反映是否写到要点，内容质量、证明材料有效性与实际得分仍需独立评审。

## 3. 已核对的修复与优化

### 3.1 易标近期更新

以下更新属于易标自身实现，不能据此认定 EasyWrite 存在同一缺陷或已经继承对应修复。

| 更新 | 参考提交 | 对 EasyWrite 的启发 |
| --- | --- | --- |
| 困难要求下允许降级处理，减少弱模型主动放弃 | [91c9fe4](https://github.com/FB208/OpenBidKit_Yibiao/commit/91c9fe4) | 明确失败原因、可用结果和待解决问题，避免把最低可用结果标记为全部完成 |
| 优化提示词前缀缓存 | [93e326b](https://github.com/FB208/OpenBidKit_Yibiao/commit/93e326b) | 固定公共上下文的组织方式，按服务商能力验证缓存收益 |
| 优化扩缩写、去表格、格式校验和子代理并发流程 | [4a61f2a](https://github.com/FB208/OpenBidKit_Yibiao/commit/4a61f2a)、[307d585](https://github.com/FB208/OpenBidKit_Yibiao/commit/307d585) | 将生成、修订和格式校验分成可检查的业务步骤 |
| 修复配图比例回显 | [ca4d218](https://github.com/FB208/OpenBidKit_Yibiao/commit/ca4d218) | 配图配置、渲染结果与编辑界面使用一致的参数 |
| 优化中断后的重试 | [f756978](https://github.com/FB208/OpenBidKit_Yibiao/commit/f756978) | 保存已完成产物，重试剩余步骤 |
| 修复页边距调整后边框错位、部分图片无法识别导致导出失败 | [004fe51](https://github.com/FB208/OpenBidKit_Yibiao/commit/004fe51)、[f9665f7](https://github.com/FB208/OpenBidKit_Yibiao/commit/f9665f7) | 导出验收同时检查文档结构、图片与版面配置 |

发布记录：[易标 Releases](https://github.com/FB208/OpenBidKit_Yibiao/releases)。

### 3.2 EasyWrite 当前已有的修复

- 项目写回重新读取最新状态，避免旧快照覆盖其他章节的并发编辑；批量生成保护用户中途修改的章节。[项目仓储](backend/app/services/project_store.py)、[章节接口](backend/app/api/sections.py)
- 流式生成失败时保留原稿，避免将不完整正文落库；真实流式输出开始后不会追加演示文本。[章节接口](backend/app/api/sections.py)、[模型客户端](backend/app/core/llm_client.py)
- 改进推理模型输出额度重试、空字段补充和服务商流式用量参数兼容。[模型客户端](backend/app/core/llm_client.py)
- 改进合并单元格、跨页表格、PDF 页眉页脚与书签处理，并按招标原文解释条款标记。[Word 解析](backend/app/services/parser/word_parser.py)、[PDF 解析](backend/app/services/parser/pdf_parser.py)、[招标分析](backend/app/services/parser/tender_analyzer.py)
- 以实际评分覆盖替代固定分数，空白标书明确标记为未撰写。[质量检查](backend/app/services/checker/quality_inspector.py)
- 后台任务持久化，重启后明确标记中断；测试隔离运行数据，并修复 Windows 中文日期格式问题。[任务管理](backend/app/core/task_manager.py)、[测试配置](tests/conftest.py)

本次对比中运行过的相关回归测试为：后端118项、前端34项，均通过。这是对应测试范围的验证记录，不代表真实模型效果或全部投标场景均已验证。

## 4. 借鉴优先级

P0 为单章节 Agent 正式采纳前需要补齐的基础；P1 为随后改善交付质量与使用效率的能力；P2 根据实际业务需求安排。工作量是相对判断，不是工期承诺。

| 优先级 | 借鉴方向 | 当前差距 | 建议交付 | 相对工作量 |
| --- | --- | --- | --- | --- |
| P0 | 企业证明材料与事实缺失处理 | 附件多以名称或索引记录，预设资料缺少统一的示例和核实状态；部分招标要求自动转成承诺 | 实际附件管理，区分招标要求、拟承诺和已确认能力，记录来源、适用主体与核实状态 | 中 |
| P0 | 生成、检查、定向修订 | 生成、评分检查和质检分散在现有业务入口 | 单章节 Agent，提供带证据、问题清单与修改差异的候选稿 | 高 |
| P0 | 任务恢复 | 已保存任务记录，但重启后未完成任务标记为中断，需要重新发起 | 保存步骤与产物，继续未完成工作，并保护人工编辑与重复写入 | 高 |
| P1 | 文档产出与导出验收 | 完整 Mermaid 渲染依赖前端，缺少统一的产物检查报告 | 后端渲染入口、导出检查与可定位的问题清单 | 中 |
| P1 | 多文件、多标段组织 | 已有目标分包选择，尚缺多文件来源、澄清补遗和复杂流程管理 | 项目文档清单、来源与版本记录、适用标段范围 | 中至高 |
| P1 | 请求队列与公共前缀复用 | 需要协调并发、取消、上下文依赖和缓存效果 | 可配置的请求上限、公共上下文组织与效果对比 | 中 |
| P2 | 查重、扩展解析与配图 | 尚缺专门的多标书查重、扫描件解析和 AI/HTML 配图 | 按用户材料与交付需求逐项增加 | 按具体范围评估 |

## 5. 重点设计与落地方式

### 5.1 企业资料成为可核查的证据

借鉴易标的资信附件管理，以及全局事实缺失时使用待填写或概括表达的设计。[资信服务][ref-assets]、[全局事实处理][ref-facts]

EasyWrite 建议补充：

- 资质、人员和业绩绑定实际上传附件，保存文件标识、原始名称、适用主体与来源位置；沿用已有有效期字段，增加材料检查结果。
- 资料明确标识已核实、待核实或示例。示例和未核实资料不自动作为企业已具备能力写入正式候选稿。
- 招标要求、拟承诺和已确认能力分别记录。解析结果提出承诺建议，由用户确认后成为正式事实。
- 对需要证明材料的评分项分别展示文字覆盖与材料齐备情况，能定位缺少的附件和待确认事实。

验收示例：招标文件要求某项资质，而企业未提供证书时，系统显示缺少证明材料，不将预设资质直接写成企业已持有。

### 5.2 单章节生成与修订闭环

借鉴易标的提交检查策略：区分阻塞问题与质量问题，判断相邻修复是否取得进展，无进展时换方法，并保留已完成成果。[提交策略][ref-submission]

建议复用现有评分、检索、质检与版本服务，形成以下流程：

1. 用户确定目标章节、评分项、证据和字数范围。
2. 读取本次需要的招标原文与已确认企业事实，保存输入依据。
3. 生成候选稿，核对评分要点、事实、证明材料与篇幅。
4. 根据具体问题定向修订，保留原稿与已通过检查的内容。
5. 展示引用、待核实事项、检查报告和正文差异，供用户采纳。

运行应有明确的修订轮数、调用次数、时间和输出额度上限。达到最低可用目标或预算上限时，报告未解决事项，不显示为全部目标完成。

采纳前需校验正文和输入依据版本，并在同一事务内完成正文、必要历史版本与执行回执的提交。这些能力当前仍需补充，实施约束以 [Agent 设计方案](AGENT_DESIGN.md)为准。

### 5.3 保存任务过程并继续执行

借鉴易标将工作文件、会话、状态和结果分别保存的组织方式。[持久任务服务][ref-persistence]

EasyWrite 建议保存运行范围、阶段、已完成章节、选用证据、失败步骤与产物标识。恢复前重新核对业务版本，重试未完成步骤；用户已经修改的正文需显式处理冲突。

正文写入使用稳定操作标识和持久执行回执。响应或检查点丢失后，先查询回执，避免重复覆盖正文或重复创建版本。暂停、取消和浏览器断开连接分别处理。

验收需覆盖两种情况：写入前故障不留下部分正文或版本；写入成功但响应丢失时，继续执行不会产生第二次相同写入。

### 5.4 导出成为可检查的交付步骤

借鉴易标将 Mermaid、HTML 本地渲染与文档导出封装成服务的分工，沿用 EasyWrite 当前文档技术栈。[本地渲染服务][ref-render]

建议新增后端可调用的完整图形渲染入口，并生成导出检查报告：检查空章节、缺图、图片格式、标题层级、列表编号、表格结构和版面配置。目录页码与实际分页需要通过文档渲染或 Word 更新域进一步确认，结构检查不能替代版面验证。

后台任务应能独立导出含图 Word；某张图失败时保留其他有效产物，明确列出需要修复的图及所属章节。

### 5.5 多文件与多标段保留来源关系

借鉴易标的多招标文件和多标段工作流，在已有目标分包选择之上增加项目文档清单。[功能说明][ref-readme]、[工作区数据结构][ref-schema]

主招标文件、澄清补遗、企业资料和已有方案分别记录来源、版本与适用标段。条款与评分项关联到对应原文；补遗与旧要求冲突时展示差异，供用户确定生效要求。已有方案导入后保留原始内容，展示扩写与修订范围。

### 5.6 请求并发与缓存收益可验证

借鉴易标的统一请求队列和公共前缀组织。[请求队列][ref-queue]、[前缀复用][ref-prefix]

检索和独立核查可以并发；正文生成按跨章依赖安排，写入继续遵守项目更新与版本校验规则。队列统一处理并发上限、取消和重试。

公共上下文保持稳定，但缓存方式、命中条件和预热收益取决于服务商。先使用现有模型调用记录对比耗时、实际用量和缓存命中信息，确认收益后再扩大应用；缺失的用量字段保持未知。

## 6. 建议实施顺序与验收

| 阶段 | 交付内容 | 验收依据 |
| --- | --- | --- |
| 1. 事实、证据与写入基础 | 真实附件、核实状态、原文按需读取、共用业务命令、版本校验、原子提交与执行回执 | 示例不混入已确认事实；证据可定位；写入失败可回滚；重复操作返回原回执 |
| 2. 单章节编写与修订 | 候选稿、问题清单、定向修订、正文差异、采纳入口和运行预算 | 用同一材料对比现有生成方式，核对评分遗漏、无依据声明和人工修改量 |
| 3. 恢复与交付 | 步骤保存、暂停取消、失败继续、后端图形渲染与导出报告 | 重启不重复已完成写入；人工修改受保护；后台独立导出；缺图可定位 |
| 4. 按业务需求扩展 | 多文件、多标段、请求优化，以及查重、扩展解析和配图 | 在对应真实材料和业务流程中验证效果，并记录耗时与实际用量 |

首个完整交付目标：用户选择一个技术评分项及其企业证据，获得有引用、经过检查、可定向修订、可查看差异并采纳的章节候选稿；失败后可以继续未完成步骤。

实现前继续细化 [Agent 设计方案](AGENT_DESIGN.md)中的工具契约、事务与恢复规则。本文件负责参考项目比较和优先级，不替代该方案。

## 7. 参考源码索引

| 源码 | 借鉴主题 |
| --- | --- |
| [contentGenerationAgent.cjs][ref-agent] | 编写任务的规划、工具调用与业务阶段组织 |
| [piSubmissionPolicy.cjs][ref-submission] | 阻塞与质量问题分离、修复进展与最低目标 |
| [globalFactsTaskV2.cjs][ref-facts] | 事实缺失时的待填写与概括表达模式 |
| [credentialLibraryService.cjs][ref-assets] | 企业资料、证明图片及所属关系 |
| [piPersistentTaskStore.cjs][ref-persistence] | 工作目录、会话、任务状态与结果保存 |
| [localImageRenderService.cjs][ref-render] | 本地 Mermaid/HTML 渲染与并发池 |
| [aiRequestQueue.cjs][ref-queue] | 请求并发、排队与取消 |
| [promptPrefixCache.cjs][ref-prefix] | 公共消息前缀与可选预热 |
| [fileService.cjs][ref-file] | 本地与 MinerU 解析入口 |
| [workspace_schema.sql][ref-schema] | 项目文档、任务、知识库与资信数据结构 |

[ref-readme]: https://github.com/FB208/OpenBidKit_Yibiao/blob/91c9fe447266e769ccfb435741e06de182079549/README.md
[ref-agent]: https://github.com/FB208/OpenBidKit_Yibiao/blob/91c9fe447266e769ccfb435741e06de182079549/client/electron/services/contentGenerationAgent.cjs
[ref-submission]: https://github.com/FB208/OpenBidKit_Yibiao/blob/91c9fe447266e769ccfb435741e06de182079549/client/electron/services/pi/piSubmissionPolicy.cjs
[ref-facts]: https://github.com/FB208/OpenBidKit_Yibiao/blob/91c9fe447266e769ccfb435741e06de182079549/client/electron/services/globalFactsTaskV2.cjs
[ref-assets]: https://github.com/FB208/OpenBidKit_Yibiao/blob/91c9fe447266e769ccfb435741e06de182079549/client/electron/services/credentialLibraryService.cjs
[ref-persistence]: https://github.com/FB208/OpenBidKit_Yibiao/blob/91c9fe447266e769ccfb435741e06de182079549/client/electron/services/pi/piPersistentTaskStore.cjs
[ref-render]: https://github.com/FB208/OpenBidKit_Yibiao/blob/91c9fe447266e769ccfb435741e06de182079549/client/electron/services/localImageRenderService.cjs
[ref-queue]: https://github.com/FB208/OpenBidKit_Yibiao/blob/91c9fe447266e769ccfb435741e06de182079549/client/electron/utils/aiRequestQueue.cjs
[ref-prefix]: https://github.com/FB208/OpenBidKit_Yibiao/blob/91c9fe447266e769ccfb435741e06de182079549/client/electron/utils/promptPrefixCache.cjs
[ref-file]: https://github.com/FB208/OpenBidKit_Yibiao/blob/91c9fe447266e769ccfb435741e06de182079549/client/electron/services/fileService.cjs
[ref-schema]: https://github.com/FB208/OpenBidKit_Yibiao/blob/91c9fe447266e769ccfb435741e06de182079549/sql/workspace_schema.sql
[ref-menu]: https://github.com/FB208/OpenBidKit_Yibiao/blob/91c9fe447266e769ccfb435741e06de182079549/client/src/app/menuConfig.ts
