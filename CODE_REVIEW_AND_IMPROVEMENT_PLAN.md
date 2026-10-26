# EasyWrite 代码审查与优化实施台账

更新日期：2026-10-26。审查基线：`7c3d3bb`；P0-1 至 P0-10 已全部实施并通过本地验证，单项提交见下表。以下定位依据实际代码，函数名用于后续代码移动后的检索。本文随 P0 实施同步更新，不把计划能力记作已完成。

## 1. 审查范围与结论

已阅读根目录 [CLAUDE.md](<D:/Pycharm Project/EasyWrite/CLAUDE.md>)、[IMPROVEMENT_PLAN.md](<D:/Pycharm Project/EasyWrite/IMPROVEMENT_PLAN.md>)、[README.md](<D:/Pycharm Project/EasyWrite/README.md>)，以及 [docs/eval/2026-10-25.md](<D:/Pycharm Project/EasyWrite/docs/eval/2026-10-25.md>)、[docs/eval/2026-10-25-2.md](<D:/Pycharm Project/EasyWrite/docs/eval/2026-10-25-2.md>)；核对了 backend、frontend 的实现与现有测试。

当前系统具备拆标、评分项大纲、资料选取、候选稿、规则检查、智能修订和 Word 导出闭环。本轮已修复偏离承诺、依据失效检测、检查遗漏、保存顺序、校审状态、迁移保护和导出文件隔离等 P0 问题。分级事实、字数控制、评分索引和附件编排仍需按 P1/P2 增强，不能据此宣称已具备完整交付能力。模型写出流畅文字不能替代资格证明或企业承诺确认。

本轮顺序：每个 P0 单独实施，先通过本地测试再单独提交。pytest 使用临时数据目录和离线模型桩，不修改真实企业资料；前端变更执行 Vitest 和生产构建并提交静态资源。未运行收费的真实模型评估。

## 2. P0 实施状态

| 编号 | 问题 | 状态 | 单项提交 | 提交前验证 |
|---|---|---|---|---|
| P0-1 | 偏离响应被强制满足，事实约束不完整 | 已修复 | `aab8bc5` | pytest 416；Vitest 93；ruff、build 通过 |
| P0-2 | 知识库指纹口径不一致，旧候选稿缺指纹 | 已修复 | `c41d74a` | pytest 420；Vitest 93；ruff、build 通过 |
| P0-3 | 跨分包条款去重和响应写回串项 | 已修复 | `3b7e0d8` | pytest 425；Vitest 93；ruff、build 通过 |
| P0-4 | 删除的证明关联仍可能齐备或被替代 | 已修复 | `d927f14` | pytest 428；Vitest 93；ruff、build 通过 |
| P0-5 | 承诺单位、比较方向及全书口径错误 | 已修复 | `54afadd` | pytest 441；Vitest 93；ruff、build 通过 |
| P0-6 | 资料仅检查首次出现，全书漏父章正文 | 已修复 | `4ce44bf` | pytest 446；Vitest 93；ruff、build 通过 |
| P0-7 | 导出、回填等操作未可靠等待保存 | 已修复 | `7e0b130` | pytest 446；Vitest 101；ruff、build 通过 |
| P0-8 | 修改正文后沿用已校审状态 | 已修复 | `3d1e954` | pytest 454；Vitest 102；ruff、build 通过 |
| P0-9 | 历史示例分类误伤及清理边界 | 已修复 | `bc9d583` | pytest 466；Vitest 104；ruff、build 通过 |
| P0-10 | 同名 Word 导出覆盖和文件生命周期 | 已修复 | 本项提交，见 git log | pytest 476；Vitest 104；ruff、build 通过 |

### P0-1：偏离表应忠实表达实际能力

- **风险 / ROI：高 / 高。** 原提示词要求红线不能负偏离、无能力依据也声明满足；`_apply` 将不在正向白名单内的状态强制改为完全满足，普通文本又靠“优于”等词猜测正偏离。
- **代码定位：** [backend/app/services/parser/deviation_engine.py · _apply](<D:/Pycharm Project/EasyWrite/backend/app/services/parser/deviation_engine.py:370>)、[backend/app/services/parser/deviation_engine.py · generate_response_for_item](<D:/Pycharm Project/EasyWrite/backend/app/services/parser/deviation_engine.py:375>)、[backend/app/services/parser/deviation_engine.py · _generate_batch](<D:/Pycharm Project/EasyWrite/backend/app/services/parser/deviation_engine.py:408>)；完整事实结构见 [backend/app/models/schemas.py · GlobalFacts](<D:/Pycharm Project/EasyWrite/backend/app/models/schemas.py:45>)。
- **已实施：** 保留负偏离；未知、缺失状态和纯文本降级为待核实；默认条目为待生成；使用完整 `GlobalFacts.to_constraint_text()`；UI 筛选、Markdown、Word、导出前检查覆盖待核实。
- **验证：** [tests/test_deviation_truthfulness.py](<D:/Pycharm Project/EasyWrite/tests/test_deviation_truthfulness.py>) 覆盖单条/批量、SLA/工期/自定义事实、红线、未知状态及普通文本；[frontend/src/components/__tests__/DeviationView.test.js](<D:/Pycharm Project/EasyWrite/frontend/src/components/__tests__/DeviationView.test.js>) 验证导出提醒。模型正确性仍需人工核实，当前修改不等于自动证明企业满足招标要求。

### P0-2：候选稿知识库依据指纹统一

- **风险 / ROI：高 / 高。** 基线已经加入知识库指纹，不能仍称“完全没有实现”；实际残留是生成时指纹含父级背景，采纳时只读原始 chunk 正文，导致未修改的依据也被判变化。旧候选稿有引用但无对应指纹又可能绕过校验。
- **代码定位：** [backend/app/services/proposals.py · compute_manifest](<D:/Pycharm Project/EasyWrite/backend/app/services/proposals.py:77>)、[backend/app/services/proposals.py · proposal_state](<D:/Pycharm Project/EasyWrite/backend/app/services/proposals.py:311>)、[backend/app/services/proposals.py · apply_proposal](<D:/Pycharm Project/EasyWrite/backend/app/services/proposals.py:344>)、[backend/app/services/rag/retriever.py · compose_content](<D:/Pycharm Project/EasyWrite/backend/app/services/rag/retriever.py:215>)。
- **已实施：** 两端使用相同的背景扩展内容；详情与采纳共用校验，旧候选稿缺少已用引用的指纹时要求重新生成，不用当前内容替旧稿补签。
- **验证：** [tests/test_proposals.py](<D:/Pycharm Project/EasyWrite/tests/test_proposals.py>) 覆盖稳定父级背景、背景变更、正文更新、片段删除、旧清单拒绝及事务不写正文。

### P0-3：跨包同文条款不能去重或串写

- **风险 / ROI：高 / 高。** 原提取在分包过滤之前按正文去重，包 2 的同文要求可能先被包 1 去掉；批量生成又按 clause_title 合并响应。
- **代码定位：** [backend/app/services/parser/deviation_engine.py · extract_from_structure](<D:/Pycharm Project/EasyWrite/backend/app/services/parser/deviation_engine.py:90>)、[backend/app/api/deviations.py · generate_project_deviations](<D:/Pycharm Project/EasyWrite/backend/app/api/deviations.py:86>)。
- **已实施：** 去重包括分包、来源章节、等级和文本指纹；响应写回包含分包、章节、等级、当前条文及行号，并比较生成前的完整条目快照，保护并发人工修改；统计实际写回条数。
- **验证：** [tests/test_deviation_packages.py](<D:/Pycharm Project/EasyWrite/tests/test_deviation_packages.py>) 验证跨包/通用同文并存、同范围重复去除；[tests/test_deviation_structure.py](<D:/Pycharm Project/EasyWrite/tests/test_deviation_structure.py>) 验证包间响应隔离及生成期间修改条文、响应说明、分包后不覆盖。

### P0-4：失效证明关联必须成为缺口

- **风险 / ROI：高 / 高。** 原状态聚合只检查找到的资产，一条有效加一条删除可能仍为齐备；全部关联删除时选材回退自动匹配，偏离用户指定依据。
- **代码定位：** [backend/app/services/assets/evidence.py · evidence_report](<D:/Pycharm Project/EasyWrite/backend/app/services/assets/evidence.py:124>)、[backend/app/services/generator/evidence_set.py · select_evidence](<D:/Pycharm Project/EasyWrite/backend/app/services/generator/evidence_set.py:90>)、[backend/app/services/checker/export_preflight.py · _material](<D:/Pycharm Project/EasyWrite/backend/app/services/checker/export_preflight.py:94>)。
- **已实施：** 缺失关联参与状态聚合；更严重问题优先，否则显示关联失效；有用户关联键时禁止自动改用其他资产，删除缺口进入 excluded_assets 和导出检查；前端使用警示样式。
- **验证：** [tests/test_evidence_links.py](<D:/Pycharm Project/EasyWrite/tests/test_evidence_links.py>)、[tests/test_evidence_set.py](<D:/Pycharm Project/EasyWrite/tests/test_evidence_set.py>) 覆盖部分和全部删除、禁止替代、导出警告；[frontend/src/utils/__tests__/material.test.js](<D:/Pycharm Project/EasyWrite/frontend/src/utils/__tests__/material.test.js>) 验证状态映射。

### P0-5：修复承诺的量纲和比较方向

已按下述方案实施；新增 [tests/test_commitment_consistency.py](<D:/Pycharm Project/EasyWrite/tests/test_commitment_consistency.py>)，单章/全书共享 `commitment_relation`。

- **风险 / ROI：高 / 高。** 时限中“天”和“小时”使用不同数值基准；驻场人数误用越少越优；工作日与自然日不可直接比较；全书检查没有复用单章的“不劣于事实、列待核实”规则。单章内多个值也可能被报为跨章冲突。
- **代码定位：** [backend/app/services/checker/section_check.py · extract_quantities](<D:/Pycharm Project/EasyWrite/backend/app/services/checker/section_check.py:245>)、[backend/app/services/checker/section_check.py · _better_or_equal](<D:/Pycharm Project/EasyWrite/backend/app/services/checker/section_check.py:329>)、[backend/app/services/checker/section_check.py · _check_commitments](<D:/Pycharm Project/EasyWrite/backend/app/services/checker/section_check.py:378>)、[backend/app/services/checker/book_consistency.py · book_consistency](<D:/Pycharm Project/EasyWrite/backend/app/services/checker/book_consistency.py:28>)。
- **修改方案：** 数量保留归一后的量纲；时限按分钟，工期/质保按天，工作日独立；人数及质保越多/长越优。抽取共享判定函数，返回一致、更优、不可比、冲突和无依据；不可比进入待核实。跨章冲突至少涉及两个不同章节，保留近似月份容差。
- **pytest：** 1 天与 24 小时相等、1 天劣于 4 小时、3 人事实下 2 人阻塞/4 人待核实、工作日不可比、同章多值不报跨章、单章与全书结论一致。前端沿用已有报告契约，运行现有 Vitest。
- **边界：** 分级 SLA 的“级别/场景/对象”语义归属属于 P1-1，不能仅靠数值方向根治。

### P0-6：检查每次资料出现及所有有正文节点

已按下述方案实施；新增 [tests/test_checker_occurrences.py](<D:/Pycharm Project/EasyWrite/tests/test_checker_occurrences.py>)，另修复标注跨段借用。

- **风险 / ROI：高 / 高。** `_check_assets` 仅查每个标识第一次出现；首处有待核实标签可能掩盖后续无标注断言。全书扫描只收叶子，父章节自己的正文漏检，而 Word 会导出父章正文。
- **代码定位：** [backend/app/services/checker/section_check.py · _check_assets](<D:/Pycharm Project/EasyWrite/backend/app/services/checker/section_check.py:454>)、[backend/app/services/checker/book_consistency.py · _written_nodes](<D:/Pycharm Project/EasyWrite/backend/app/services/checker/book_consistency.py:19>)、[backend/app/services/exporter/docx_generator.py · export_project_to_docx](<D:/Pycharm Project/EasyWrite/backend/app/services/exporter/docx_generator.py:750>)。
- **修改方案：** 遍历全部标识命中，合并重叠标识，逐处判断标注和定位；全书递归收集全部有自有正文的节点，每份正文只扫描一次。
- **pytest：** 首处标注后续裸写、反向顺序、多处示例引用、重叠证书标识、父章含冲突但子章正常，以及父子均有正文不重复计数；运行报告展示 Vitest。

### P0-7：保存成功是导出等操作的前置条件

已按下述方案实施；新增 AppShell 导出等待测试，扩展偏离表与编辑器 Vitest。

- **风险 / ROI：高 / 高。** 顶栏导出读取服务器正文，不等待编辑器防抖；偏离表保存吞掉异常后继续导出/生成，回填不先保存；流式生成期间 flushSave 返回成功但内容尚未落库。
- **代码定位：** [frontend/src/components/layout/AppShell.vue · openExport](<D:/Pycharm Project/EasyWrite/frontend/src/components/layout/AppShell.vue:143>)、[frontend/src/components/layout/AppShell.vue · exportWord](<D:/Pycharm Project/EasyWrite/frontend/src/components/layout/AppShell.vue:168>)、[frontend/src/composables/useSectionSave.js · flushSave](<D:/Pycharm Project/EasyWrite/frontend/src/composables/useSectionSave.js:103>)、[frontend/src/views/DeviationView.vue · saveEdits](<D:/Pycharm Project/EasyWrite/frontend/src/views/DeviationView.vue:116>)、[frontend/src/views/DeviationView.vue · injectToOutline](<D:/Pycharm Project/EasyWrite/frontend/src/views/DeviationView.vue:190>)。
- **修改方案：** AppShell 增加异步保存前置回调，编纂页与偏离页接入；打开/刷新导出检查及真正导出前均等待保存。保存函数返回成功/失败，失败或生成中停止后续动作；串行保存并保留保存期间的新修改。
- **Vitest：** 延迟保存时不调用检查/导出/生成/回填；失败不继续；导出弹窗打开后又编辑必须再次保存；流式进行中拒绝视为已保存；保留最新草稿。pytest 回归导出与偏离 API。

### P0-8：校审应绑定正文修订号

已按下述方案实施。旧记录仅沿用既有校审声明并绑定当时存储的 revision，无法追认历史误标；后续正文改动必须重新校审。

- **风险 / ROI：高 / 高。** 自动保存沿用 reviewed，正文变化后仍显示已校审，导出检查无法识别校审过期。
- **代码定位：** [frontend/src/composables/useSectionSave.js · autosave](<D:/Pycharm Project/EasyWrite/frontend/src/composables/useSectionSave.js:77>)、[frontend/src/components/workspace/SectionEditor.vue · markReviewed](<D:/Pycharm Project/EasyWrite/frontend/src/components/workspace/SectionEditor.vue:189>)、[backend/app/services/section_content.py · write_node](<D:/Pycharm Project/EasyWrite/backend/app/services/section_content.py:32>)、[backend/app/api/sections.py · update_section_content](<D:/Pycharm Project/EasyWrite/backend/app/api/sections.py:240>)。
- **修改方案：** 正文变化统一使校审失效；记录 reviewed_revision，由明确校审操作设置为当前 revision；重复保存同文不降级。UI 使用服务端返回的 node_status，校审请求带修订基准，防止对旧正文确认。
- **pytest / Vitest：** reviewed 后编辑降级，同文保存保留，重新确认恢复，旧修订确认冲突；AI/回填/恢复等共享写入路径均不能继承旧校审，导出清单同步提醒。

### P0-9：保护历史误分类的示例资料

已按下述方案实施；新增只读 `GET /assets/migration-audit`、界面核对入口、迁移前备份与来源历史，批量清理保留可疑条目及附件。本轮未读取或自动确认真实企业资料。

- **风险 / ROI：高 / 高。** 已有迁移只处理无合法 status 的条目；历史上已经标成 example 的编辑数据会直接跳过。预设字段相等也不能说明新增 holder/attachments 没有真实资料；清空示例会删除其附件。
- **代码定位：** [backend/app/services/assets/asset_manager.py · _load_or_init](<D:/Pycharm Project/EasyWrite/backend/app/services/assets/asset_manager.py:361>)、[backend/app/services/assets/asset_manager.py · clear_examples](<D:/Pycharm Project/EasyWrite/backend/app/services/assets/asset_manager.py:586>)、[frontend/src/views/AssetsView.vue · load](<D:/Pycharm Project/EasyWrite/frontend/src/views/AssetsView.vue:87>)。
- **修改方案：** 用统一比较函数检查 DEFAULT_* 基础字段及额外业务字段；新增只读迁移审计，列出可疑 example，界面提供人工核对入口；批量清理保护这些记录。自动状态迁移前保存原文件备份与迁移来源，不将可疑条目自动确认为真实。
- **pytest / Vitest：** 已有 example 的名称编辑、仅 holder 改动、仅附件新增、默认空字段、重复启动幂等、审计不写盘、清除时保护附件、人工改为待核实/已确认解除提示、备份写失败不覆盖原数据；界面展示审计和编辑入口。

### P0-10：Word 导出文件隔离

已按下述方案实施；新增 `tests/test_export_isolation.py` 的 10 项回归，覆盖实际并发下载及文件清理。

- **风险 / ROI：高 / 高。** 物理文件名只由项目名决定，不同项目同名或同项目并发导出会覆盖；FileResponse 发送时可能读到另一请求内容。
- **代码定位：** [backend/app/services/exporter/docx_generator.py · export_project_to_docx](<D:/Pycharm Project/EasyWrite/backend/app/services/exporter/docx_generator.py:750>)、[backend/app/services/exporter/docx_generator.py · export_deviation_table](<D:/Pycharm Project/EasyWrite/backend/app/services/exporter/docx_generator.py:782>)、[backend/app/api/export.py · _export_bid](<D:/Pycharm Project/EasyWrite/backend/app/api/export.py:46>)。
- **修改方案：** 每次生成分配唯一物理路径，下载名仍为易读的“项目名_技术标书/技术偏离表.docx”；响应发送完成后清理，只清理本请求创建的文件；写入失败删除不完整临时输出。
- **pytest：** 并发同名、不同项目名净化后相同、同项目不同正文输出分别可解析；响应完成后文件清理；异常清理；下载文件名稳定。Vitest 回归下载接口。

## 3. P1：提升标书可用性与交付质量

以下均未实施。P0 修复不代表这些能力已经具备。

### P1-1：分级事实模型与招标要求分离

- **风险 / ROI：高 / 高。** `sla_commitment` 和 `custom_facts` 是文本；检查器只按响应/解决等 topic 找同类数值，无法区分“重大故障 2 小时”和“一般故障 24 小时”。候选稿压缩或修订可能因此被误导。
- **定位：** [backend/app/models/schemas.py · GlobalFacts](<D:/Pycharm Project/EasyWrite/backend/app/models/schemas.py:45>)、[backend/app/services/checker/section_check.py · fact_quantities](<D:/Pycharm Project/EasyWrite/backend/app/services/checker/section_check.py:310>)、[backend/app/services/checker/section_check.py · _check_commitments](<D:/Pycharm Project/EasyWrite/backend/app/services/checker/section_check.py:378>)。
- **方案：** 增加结构化 Commitment（topic、value、unit、comparator、severity、scope、subject、source、confirmed），保留旧文本向后兼容；企业确认事实与采购最低要求分别存储；提示词输出分级承诺表，检查只比较同作用域，不能映射则待核实。建议提取结果经人工采纳，不自动升级企业能力。
- **验证：** pytest 覆盖同级冲突、跨级不同值合法、未限定事实遇到分级正文、单位等价及旧文本兼容；Vitest 覆盖承诺卡片分级编辑、确认、拒绝及保存恢复。

### P1-2：篇幅压缩有独立策略及连续进展度量

- **风险 / ROI：中 / 高。** `length` 仅是一个质量问题，篇幅从超限 80% 压到超限 35% 仍计为一个问题；progressed 只比较问题个数，会误判没有进展，最终 minimal 只处理阻塞，压缩任务被放弃。
- **定位：** [backend/app/services/checker/section_check.py · _check_quality](<D:/Pycharm Project/EasyWrite/backend/app/services/checker/section_check.py:531>)、[backend/app/services/refine/policy.py · progressed](<D:/Pycharm Project/EasyWrite/backend/app/services/refine/policy.py:49>)、[backend/app/services/refine/policy.py · decide](<D:/Pycharm Project/EasyWrite/backend/app/services/refine/policy.py:67>)、[backend/app/services/generator/section_generator.py · build_prompts](<D:/Pycharm Project/EasyWrite/backend/app/services/generator/section_generator.py:140>)。
- **方案：** 报告增加 length_delta/overrun_ratio，独立 compression 模式；提示词给出目标区间、必须保留的评分点和事实，以及优先删套话/重复段的操作。正文和图表分别计预算；仅字数改善也计为进展，但评分覆盖或阻塞退化时回退前版；不强行截断正文凑字数。
- **验证：** pytest 用模型桩模拟持续压缩、压缩不动、删掉评分点、预算很小、只有图表等情况；Vitest 显示字数进展和仍超限原因，不能把部分完成显示为目标达成。

### P1-3：待核实事项统一身份及统计口径

- **风险 / ROI：中 / 高。** 同一条资料可同时成为 unverified_asset 与 placeholder；占位按原文去重，承诺按 topic/value，评估、章节和全书统计口径不一致。
- **定位：** [backend/app/models/schemas.py · VerifyItem](<D:/Pycharm Project/EasyWrite/backend/app/models/schemas.py:447>)、[backend/app/services/checker/section_check.py · _placeholders](<D:/Pycharm Project/EasyWrite/backend/app/services/checker/section_check.py:562>)、[backend/app/services/checker/section_check.py · check_section](<D:/Pycharm Project/EasyWrite/backend/app/services/checker/section_check.py:621>)、[backend/app/services/checker/book_consistency.py · book_consistency](<D:/Pycharm Project/EasyWrite/backend/app/services/checker/book_consistency.py:28>)。
- **方案：** VerifyItem 增加 stable_key、asset_key/fact_key、source、occurrences；同一业务事项合并，所有出现位置保留。明确同时展示“事项数”和“出现次数”，检查报告、导出清单、评估脚本使用同一聚合函数；核实结论随依据变化失效。
- **验证：** pytest 覆盖同一资产多处出现、资产标签与占位重合、同文不同主体、多个相同数值但不同级别、跨章合并；Vitest 验证事项计数和定位列表，保留所有整改位置。

### P1-4：自动生成评分标准响应索引表

- **风险 / ROI：中 / 很高。** 当前导出只有封面、目录和正文，未将评分项及章节映射形成专家快速定位表。
- **定位：** [backend/app/services/exporter/docx_generator.py · export_project_to_docx](<D:/Pycharm Project/EasyWrite/backend/app/services/exporter/docx_generator.py:750>)、[backend/app/models/schemas.py · OutlineNode](<D:/Pycharm Project/EasyWrite/backend/app/models/schemas.py:8>)、[backend/app/api/export.py · _export_bid](<D:/Pycharm Project/EasyWrite/backend/app/api/export.py:46>)。
- **方案：** 导出输入增加目标分包的 scoring_items/evidence_links；在目录后生成原生 Word 表格：评分项、分值、响应章节、证明附件、页码。用 bookmark/PAGEREF 关联章节及附件，不能编造静态页码；多章承接列全，未承接显示待补。页面提供启用开关与预览，按最终版式更新 Word 域后复核。
- **验证：** pytest 解包 docx 核对表格、书签唯一性、PAGEREF、仅目标分包、多章映射、未覆盖项、重复名称不同 ID；Vitest 验证开关及请求参数；人工用 Word 更新域后检查页码和表头跨页。

### P1-5：B1/B2 证明附件进入 Word 正文或附件册

- **风险 / ROI：高 / 很高。** 已有附件存储和证据链接，但导出函数没有证据输入；Markdown 图片分支不能替代企业证明附件编排。
- **定位：** [backend/app/services/assets/asset_manager.py · attachment_file](<D:/Pycharm Project/EasyWrite/backend/app/services/assets/asset_manager.py:558>)、[backend/app/services/assets/evidence.py · resolve_links](<D:/Pycharm Project/EasyWrite/backend/app/services/assets/evidence.py:108>)、[backend/app/services/exporter/docx_generator.py · _add_runs](<D:/Pycharm Project/EasyWrite/backend/app/services/exporter/docx_generator.py:366>)、[backend/app/services/exporter/docx_generator.py · export_project_to_docx](<D:/Pycharm Project/EasyWrite/backend/app/services/exporter/docx_generator.py:750>)。
- **方案：** 生成显式 EvidenceAppendixPlan（section_id、asset_key、attachment_id、页序、图题、放置位置）；仅从服务端解析附件，图片保持比例、PDF 按页渲染为图片；按章节插入或统一附件册，并与索引表互链。附件内容去重保留多处引用，保留证书边框/印章，不让模型合成证明；缺失、过期、主体不符继续提示。限制像素、页数和总大小。
- **验证：** pytest 覆盖多页 PDF 顺序、横向扫描件、损坏附件、缺页、跨评分项共用附件、主体不符、附件指纹变更和受控路径；Vitest 验证附件选择/排序/缺口提示；渲染 Word 后人工检查可读性、分页和无裁切。

### P1-6：图表有语义规范和可读性检查

- **风险 / ROI：中 / 高。** 已支持 Mermaid 真渲染与本地降级，不能称“没有架构图”；薄弱点是复杂图的层级、密度、图例与 Word 内文字尺寸，不能只按图片缩放解决。
- **定位：** [backend/app/services/generator/section_generator.py · build_prompts](<D:/Pycharm Project/EasyWrite/backend/app/services/generator/section_generator.py:140>)、[backend/app/services/exporter/docx_generator.py · _picture_size](<D:/Pycharm Project/EasyWrite/backend/app/services/exporter/docx_generator.py:615>)、[backend/app/services/exporter/docx_generator.py · _render_mermaid_block](<D:/Pycharm Project/EasyWrite/backend/app/services/exporter/docx_generator.py:630>)。
- **方案：** 提示词增加架构/网络拓扑/部署/流程各自的最少语义要素与节点上限；引入结构化 DiagramSpec 和统一主题、图例、标题。过密图按逻辑层拆图，宽图可横向页，导出检查列出渲染失败和文字过小项；保留失败插槽，不用无关图片顶替。
- **验证：** pytest 覆盖图片宽高约束、图题编号、横页与纵页恢复、失败占位；Vitest 覆盖 Mermaid 解析失败、长标签、主题切换与 PNG 输出；人工渲染比较典型 10/30/60 节点图可读性。

### P1-7：F2 补遗/澄清先实现可追溯覆盖

- **风险 / ROI：高 / 高。** 当前分析应用整体替换 tender_analysis，原文按项目保存；没有文件版本、条款替代关系或补遗优先级。直接重新上传无法证明最终采用了哪一版要求。
- **定位：** [backend/app/api/tender.py · apply_tender_analysis](<D:/Pycharm Project/EasyWrite/backend/app/api/tender.py:68>)、[backend/app/services/parser/document_parser.py · parse_document](<D:/Pycharm Project/EasyWrite/backend/app/services/parser/document_parser.py:27>)、[backend/app/services/project_store.py · set_tender_document_in](<D:/Pycharm Project/EasyWrite/backend/app/services/project_store.py:164>)。
- **方案：** 增加 TenderDocument（原件/补遗/澄清、发布时间、哈希、版本）、ClauseOverride（原条款引用、替代文本、来源页、人工确认）；规则建议覆盖映射，用户确认生效；重新计算受影响评分项、偏离表和候选稿依据，不覆盖企业全局承诺。初期限定同项目、同分包的明确替代/追加，复杂冲突交人工选择。
- **验证：** pytest 覆盖替换、追加、撤销、同日冲突、错分包、重复上传幂等与候选稿失效；Vitest 展示原文对照、影响章节、确认/撤回，未经确认的补遗不能自动生效。

### P1-8：受限暗标模式与导出前脱敏检查

- **风险 / ROI：高 / 中。** 封面直接输出企业名称、法定代表人和信用代码；正文、图片、附件、文件属性仍可能泄露身份，不能只删除封面文字。
- **定位：** [backend/app/services/exporter/docx_generator.py · _add_cover_page](<D:/Pycharm Project/EasyWrite/backend/app/services/exporter/docx_generator.py:307>)、[backend/app/services/exporter/docx_generator.py · _new_document](<D:/Pycharm Project/EasyWrite/backend/app/services/exporter/docx_generator.py:745>)、[backend/app/models/schemas.py · GlobalFacts](<D:/Pycharm Project/EasyWrite/backend/app/models/schemas.py:45>)。
- **方案：** 项目/模板增加 blind_bid 配置及招标方格式规则；导出副本屏蔽身份封面、页眉页脚和 docx 属性，正文扫描企业/人员/地址/账号等已知标识，统一字体字号与编号。明标资格证明与暗标技术册分开配置。初期禁止未核验图片/证明直接进入暗标册，并提供人工逐页核对清单；不宣称可自动保证满足所有暗标规则。
- **验证：** pytest 检查 docx XML、关系、属性、页眉页脚无已知标识，原始项目不被脱敏覆盖；Vitest 验证模式切换、禁用附件/封面选项及命中定位；真实模板经 Word/PDF 渲染人工验收。

## 4. P2：需要架构演进的能力

### P2-1：联合体多主体与资格适用策略

- **风险 / ROI：高 / 中，依赖真实业务规则。** 当前 holder 仅与 facts.company_name 比较，合法成员的证明会被排除；放宽成名单匹配又会错把成员 A 的资质当成牵头人资格。
- **定位：** [backend/app/services/assets/material_check.py · check_material](<D:/Pycharm Project/EasyWrite/backend/app/services/assets/material_check.py:74>)、[backend/app/services/assets/material_check.py · blocking_filter](<D:/Pycharm Project/EasyWrite/backend/app/services/assets/material_check.py:134>)、[backend/app/models/schemas.py · GlobalFacts](<D:/Pycharm Project/EasyWrite/backend/app/models/schemas.py:45>)。
- **方向：** ProjectParties 描述牵头人、成员、职责、份额和协议；EligibilityRule 明确各条款适用于牵头人/任一成员/各成员/指定工作范围。证据、生成、检查、索引、盖章附件按主体链路处理。先人工确认条款归属，再逐步自动抽取；不以简单 holder 白名单替代规则。pytest 必须覆盖牵头人限定、成员分别满足、合并业绩禁用、资质不可借用等场景。

### P2-2：稳定交付快照与多客户端并发控制

- **风险 / ROI：高 / 中。** 保存前置和唯一文件名只能消除局部窗口；前端读取正文渲染图后，后端导出再次读取项目，仍可能混用两版内容。项目写锁也不等于防止多客户端提交旧正文。
- **定位：** [frontend/src/components/layout/AppShell.vue · exportWord](<D:/Pycharm Project/EasyWrite/frontend/src/components/layout/AppShell.vue:168>)、[backend/app/api/export.py · _export_bid](<D:/Pycharm Project/EasyWrite/backend/app/api/export.py:46>)、[backend/app/services/project_store.py · update](<D:/Pycharm Project/EasyWrite/backend/app/services/project_store.py:236>)、[backend/app/services/section_content.py · save_section_content](<D:/Pycharm Project/EasyWrite/backend/app/services/section_content.py:51>)。
- **方向：** 建立 ExportSnapshot（项目/正文/事实/资料/附件哈希、模板版本、检查结果、时间），图像与 Word 使用同一快照；导出编号与审签记录可追溯。所有写接口逐步采用 revision/ETag 比较并返回冲突，后续再扩展数据库级多 worker 任务协调。

### P2-3：完整文档版本图与可验证交付质量门禁

- **风险 / ROI：中 / 中。** F2 的局部覆盖不能解决多轮补遗、评分标准拆分、资格条件调整以及已校审章节的影响传播；纯 DOCX 结构测试也不能确定最终视觉分页。
- **定位：** [backend/app/api/tender.py · apply_tender_analysis](<D:/Pycharm Project/EasyWrite/backend/app/api/tender.py:68>)、[backend/app/services/proposals.py · compute_manifest](<D:/Pycharm Project/EasyWrite/backend/app/services/proposals.py:77>)、[backend/app/services/exporter/docx_generator.py · export_project_to_docx](<D:/Pycharm Project/EasyWrite/backend/app/services/exporter/docx_generator.py:750>)。
- **方向：** 文档→条款→评分项→章节→证据→交付版本建立稳定 ID 和影响关系；追加补遗保留历史与撤回路径。引入可重复的 Word/PDF 渲染环境，对页码索引、附件清晰度、表格断页、暗标残留进行交付级验收。先用小型真实招标回归集验证，再考虑多项目并行编写。

## 5. 验收与后续顺序

1. P0-1 至 P0-10 已全部完成，每项通过测试后独立提交；最终全量 pytest 476 项、Vitest 104 项、ruff 和前端生产构建通过。
2. P1 优先做结构化承诺、压缩策略和核实事项口径，避免检查器把正确文本修坏。
3. 交付能力按评分索引 → 证明附件 → 图表可读性推进；F2 和暗标按真实项目需求并行评估。
4. P2 需要真实联合体/补遗/暗标样本和交付规范。验收不能只看“测试通过”或“模型无报错”，应检查评分项可定位、证明可阅读、承诺有依据、未决缺口可追踪。

当前回归均为本机离线自动化验证；真实 Word 页码、扫描件清晰度及业务承诺确认仍按上述模块验收要求实施，未完成的能力不计入已修复范围。

## 6. 最终验证命令

- `python -m pytest tests/ -q`：476 passed，包含本机已有真实招标 DOCX/PDF 解析回归。
- `python -m ruff check .`：All checks passed。
- `npm --prefix frontend test`：24 个测试文件，104 passed。
- `npm --prefix frontend run build`：成功，静态资源已同步提交；保留现有大包体积提示。
- `git diff --check`：通过。本文代码定位已重新核实到本轮修改后的函数起始行。
