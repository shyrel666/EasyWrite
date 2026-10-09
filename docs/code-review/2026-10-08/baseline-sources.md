# 原始问题的基线源码片段

固定提交：`3aedaebebdb4a43f9ca2a2b63849e8ac7251284d`。以下内容直接由 `git show 3aedaebebdb4a43f9ca2a2b63849e8ac7251284d:<路径>` 提取，行号属于该提交，不随当前工作区修改而变化。完整文件可用相同命令查看。

<a id="indexer-98"></a>
## indexer.py:98

文件：`backend/app/services/rag/indexer.py`；基线查看命令：`git show 3aedaebebdb4a43f9ca2a2b63849e8ac7251284d:backend/app/services/rag/indexer.py`。

```text
  98:     def _rebuild_dense(self):
  99:         ids, vecs = [], []
 100:         for cid, c in self._chunks.items():
 101:             if c["_embedding"] is not None and len(c["_embedding"]) > 0:
 102:                 ids.append(cid)
 103:                 vecs.append(c["_embedding"])
 104:         self._dense_ids = ids
 105:         self._dense_matrix = np.vstack(vecs) if vecs else None
```

<a id="indexer-208"></a>
## indexer.py:208

文件：`backend/app/services/rag/indexer.py`；基线查看命令：`git show 3aedaebebdb4a43f9ca2a2b63849e8ac7251284d:backend/app/services/rag/indexer.py`。

```text
 208:     def search_dense(self, query: str, top_k: int = 20, doc_filter: Optional[List[str]] = None) -> List[Tuple[str, float]]:
 209:         with self._lock:
 210:             if self._dense_matrix is None or not embedding_client.is_available:
 211:                 return []
 212:             q = embedding_client.embed_query_sync(query)
 213:             if q is None:
 214:                 return []
 215:             q_norm = q / (np.linalg.norm(q) + 1e-10)
 216:             mat = self._dense_matrix / (np.linalg.norm(self._dense_matrix, axis=1, keepdims=True) + 1e-10)
 217:             scores = mat @ q_norm
 218:             order = np.argsort(-scores)
 219:             results = []
 220:             for idx in order[: top_k * 3]:
 221:                 cid = self._dense_ids[int(idx)]
 222:                 if doc_filter and self._chunks[cid]["doc_id"] not in doc_filter:
 223:                     continue
 224:                 results.append((cid, float(scores[idx])))
 225:                 if len(results) >= top_k:
 226:                     break
 227:             return results
```

<a id="indexer-142"></a>
## indexer.py:142

文件：`backend/app/services/rag/indexer.py`；基线查看命令：`git show 3aedaebebdb4a43f9ca2a2b63849e8ac7251284d:backend/app/services/rag/indexer.py`。

```text
 142:     def delete_document(self, doc_id: str) -> bool:
 143:         with get_session() as session:
 144:             doc = session.get(KBDocument, doc_id)
 145:             if not doc:
 146:                 return False
 147:             session.exec(sql_delete(KBChunk).where(KBChunk.doc_id == doc_id))  # type: ignore[arg-type]
 148:             session.delete(doc)
 149:         self.reload()
 150:         return True
```

<a id="workspaceview-124"></a>
## WorkspaceView.vue:124

文件：`frontend/src/views/WorkspaceView.vue`；基线查看命令：`git show 3aedaebebdb4a43f9ca2a2b63849e8ac7251284d:frontend/src/views/WorkspaceView.vue`。

```text
 124: async function onBatchDone(task) {
 125:   batchTaskId.value = ''
 126:   await store.load(projectId, true)
 127:   await store.loadProposalCounts()
 128:   editorKey.value += 1 // 重新挂载编辑器，载入新正文
 129:   const r = task.result || {}
 130:   if (task.status === 'completed') {
 131:     const failed = r.failed?.length ? `，失败 ${r.failed.length} 节（${r.failed.map((f) => f.title).slice(0, 3).join('、')}）` : ''
 132:     const skipped = r.skipped?.length ? `，跳过 ${r.skipped.length} 节（期间已被编辑）` : ''
 133:     const proposed = r.proposed?.length ? `，${r.proposed.length} 节生成候选稿（在大纲中标出，查看差异后采纳）` : ''
 134:     ElMessage[r.failed?.length ? 'warning' : 'success'](`批量撰写完成：写入 ${r.generated?.length || 0} 节${proposed}${skipped}${failed}`)
 135:   } else {
 136:     ElMessage.warning(`批量撰写已${task.status === 'cancelled' ? '取消' : '中止'}，已写入的章节已保存`)
 137:   }
 138:   batchOpen.value = false
 139: }
```

<a id="sectioneditor-49"></a>
## SectionEditor.vue:49

文件：`frontend/src/components/workspace/SectionEditor.vue`；基线查看命令：`git show 3aedaebebdb4a43f9ca2a2b63849e8ac7251284d:frontend/src/components/workspace/SectionEditor.vue`。

```text
  49: watch(
  50:   () => props.node,
  51:   (n) => {
  52:     if (!n) return
  53:     content.value = n.content || ''
  54:     savedContent.value = content.value
  55:     customInstruction.value = n.requirements?.join('；') || ''
  56:   },
  57:   { immediate: true }
  58: )
  59: 
  60: watch(content, () => {
  61:   clearSaveTimer()
  62:   if (generating.value || !dirty.value) return
  63:   saveTimer = setTimeout(autosave, 1500)
  64: })
  65: 
  66: // 已落库的正文同步回 store：切换章节再切回时编辑器不会加载旧内容
  67: function markSaved(nodeId, text, status) {
  68:   savedContent.value = text
  69:   projectStore.setSectionContent(nodeId, text, status)
  70: }
  71: 
  72: async function autosave() {
  73:   clearSaveTimer()
  74:   if (!props.node || generating.value || !dirty.value) return
  75:   const nodeId = props.node.id
  76:   const text = content.value
```

<a id="sectioneditor-177"></a>
## SectionEditor.vue:177

文件：`frontend/src/components/workspace/SectionEditor.vue`；基线查看命令：`git show 3aedaebebdb4a43f9ca2a2b63849e8ac7251284d:frontend/src/components/workspace/SectionEditor.vue`。

```text
 161:   ElMessage.info('已停止生成，已恢复原内容')
 162: }
 163: 
 164: async function polish() {
 165:   if (!props.node || !content.value.trim()) {
 166:     ElMessage.warning('章节尚无内容，请先撰写')
 167:     return
 168:   }
 169:   polishing.value = true
 170:   try {
 171:     const res = await api.polishSection(props.projectId, {
 172:       project_id: props.projectId,
 173:       section_id: props.node.id,
 174:       content: content.value,
 175:       polish_mode: 'de_ai',
 176:     })
 177:     content.value = res.polished_content
 178:     ai.noteMode(res.mode)
 179:     markSaved(props.node.id, res.polished_content, 'completed') // 后端已保存；润色不等于校审，需用户自行标记
 180:     ElMessage.success('降AI味润色完成' + (res.improvements?.length ? `：${res.improvements.slice(0, 2).join('；')}` : ''))
 181:   } catch (e) {
 182:     ElMessage.error('润色失败：' + e.message)
 183:   } finally {
 184:     polishing.value = false
 185:   }
```

<a id="sections-228"></a>
## sections.py:228

文件：`backend/app/api/sections.py`；基线查看命令：`git show 3aedaebebdb4a43f9ca2a2b63849e8ac7251284d:backend/app/api/sections.py`。

```text
 219: @router.post("/project/{project_id}/section/polish", response_model=PolishSectionResponse, summary="单章节深度降AI味与公文严肃化润色")
 220: def polish_section(project_id: str, req: PolishSectionRequest):
 221:     project = _get_project(project_id)
 222:     if not find_node(project.outline, req.section_id):
 223:         raise HTTPException(status_code=404, detail="未找到对应章节")
 224:     if not req.content.strip():
 225:         raise HTTPException(status_code=400, detail="章节尚无内容，请先撰写后再润色")
 226:     resp = quality_inspector.polish_section(req, project.facts)
 227:     # 润色是 AI 改写，不等于人工校审：状态为 completed，"已校审"只能由用户标记
 228:     _save_section_content(project_id, req.section_id, resp.polished_content, "completed", version_source="polish")
 229:     return resp
```

<a id="sections-84"></a>
## sections.py:84

文件：`backend/app/api/sections.py`；基线查看命令：`git show 3aedaebebdb4a43f9ca2a2b63849e8ac7251284d:backend/app/api/sections.py`。

```text
  77:             yield f"data: {json.dumps({'done': False, 'error': str(e)}, ensure_ascii=False)}\n\n"
  78:             return
  79: 
  80:         complete_text = strip_title_heading("".join(full_content), prompt_kw["section_title"])
  81:         if complete_text.strip():
  82:             try:
  83:                 # 按 project_id 重新读取最新项目写回：流式期间其他章节的编辑不会被旧快照覆盖
  84:                 status = _save_section_content(project_id, req.section_id, complete_text, "completed",
  85:                                                last_refs=refs, version_source="ai_generate")
  86:                 # content 为最终落库正文（去掉了重复的章节标题行），前端以此为准
  87:                 yield f"data: {json.dumps({'done': True, 'section_id': req.section_id, 'status': status, 'mode': llm_client.get_mode(), 'content': complete_text}, ensure_ascii=False)}\n\n"
  88:             except Exception as e:
  89:                 yield f"data: {json.dumps({'error': f'内容生成完成但保存失败: {e}'}, ensure_ascii=False)}\n\n"
  90:         else:
  91:             yield f"data: {json.dumps({'done': False, 'error': '生成内容为空'}, ensure_ascii=False)}\n\n"
```

<a id="proposals-89"></a>
## proposals.py:89

文件：`backend/app/services/proposals.py`；基线查看命令：`git show 3aedaebebdb4a43f9ca2a2b63849e8ac7251284d:backend/app/services/proposals.py`。

```text
  66: def compute_manifest(
  67:     project: Project, node: OutlineNode,
  68:     evidence: Optional[EvidenceSet] = None, asset_keys: Optional[List[str]] = None,
  69: ) -> Dict[str, Dict[str, str]]:
  70:     """
  71:     输入清单 {键: {label, sha}}。生成时传 evidence（取其中用到的资料）；采纳时传记录中的 asset_keys，
  72:     逐条按当前资料库重新计算（资料被删除时 sha 为 deleted）。
  73:     """
  74:     items = {it.id: it for it in rp.target_items(project.tender_analysis)}
  75:     manifest = {
  76:         "facts": {"label": "全局事实", "sha": _sha(project.facts.model_dump())},
  77:         "scoring_items": {"label": "本节承接的评分项",
  78:                           "sha": _sha([items[x].model_dump() if x in items else x for x in node.scoring_item_ids])},
  79:         "evidence_links": {"label": "评分项关联的企业资料",
  80:                            "sha": _sha({x: sorted(project.evidence_links.get(x, [])) for x in node.scoring_item_ids})},
  81:         "ref_settings": {"label": "知识库引用的锁定与排除",
  82:                          "sha": _sha({"pinned": sorted(node.pinned_refs), "excluded": sorted(node.excluded_refs)})},
  83:         "section": {"label": "本节标题路径与字数预算",
  84:                     "sha": _sha({"path": section_path(project.outline, node.id), "word_budget": node.word_budget})},
  85:     }
  86:     keys = asset_keys if asset_keys is not None else (evidence_asset_keys(evidence) if evidence else [])
  87:     for key in keys:
  88:         kind, asset_id = parse_key(key)
  89:         asset = asset_manager.get_asset(kind, asset_id)
  90:         if asset is None:
  91:             manifest[ASSET_PREFIX + key] = {"label": f"企业资料（已删除）：{asset_id}", "sha": "deleted"}
  92:             continue
  93:         fingerprint = {k: v for k, v in asset.items() if k not in ASSET_VOLATILE_FIELDS}
  94:         manifest[ASSET_PREFIX + key] = {"label": f"企业资料：{asset_name(kind, asset)}", "sha": _sha(fingerprint)}
  95:     return manifest
```

<a id="proposals-244"></a>
## proposals.py:244

文件：`backend/app/services/proposals.py`；基线查看命令：`git show 3aedaebebdb4a43f9ca2a2b63849e8ac7251284d:backend/app/services/proposals.py`。

```text
 220: 
 221: # ---------------- 创建 / 详情 / 采纳 / 放弃 ----------------
 222: 
 223: def propose(
 224:     project: Project, node: OutlineNode, content: str, evidence: EvidenceSet, *,
 225:     origin: str, task_id: str = "", parent_id: str = "",
 226:     refine: Optional[Dict[str, Any]] = None, base_revision: Optional[int] = None,
 227: ) -> Dict[str, Any]:
 228:     """
 229:     生成候选稿。project / node 为生成时读取的快照：base_revision 取 node.revision，输入清单与检查报告都按该快照计算
 230:     （生成期间用户改了正文或依据，采纳时会如实报告变化）。
 231:     base_revision 显式传入时以其为准：智能完善中断后继续执行，候选稿仍以起始时的正文为基准。
 232:     refine 为智能完善的轮次信息（存入 refine_json）。
 233:     """
 234:     try:
 235:         report = check_section(project, node, content, evidence).model_dump_json()
 236:         status = "checked"
 237:     except Exception:  # 检查异常时候选稿保持未检查状态，不能采纳
 238:         report, status = "", "draft"
 239:     row = SectionProposal(
 240:         id=f"prop_{uuid.uuid4().hex[:12]}", project_id=project.id, section_id=node.id,
 241:         parent_id=parent_id, task_id=task_id, origin=origin, content=content,
 242:         base_revision=node.revision if base_revision is None else base_revision,
 243:         refine_json=json.dumps(refine, ensure_ascii=False) if refine else "",
 244:         input_manifest_json=json.dumps(compute_manifest(project, node, evidence), ensure_ascii=False),
 245:         evidence_json=json.dumps(evidence.ref_records(), ensure_ascii=False),
 246:         report_json=report, status=status, created_at=now_str(), created_ts=time.time(),
 247:     )
 248:     return to_dict(proposal_store.create(row))
```

<a id="proposals-83"></a>
## proposals.py:83

文件：`backend/app/services/proposals.py`；基线查看命令：`git show 3aedaebebdb4a43f9ca2a2b63849e8ac7251284d:backend/app/services/proposals.py`。

```text
  66: def compute_manifest(
  67:     project: Project, node: OutlineNode,
  68:     evidence: Optional[EvidenceSet] = None, asset_keys: Optional[List[str]] = None,
  69: ) -> Dict[str, Dict[str, str]]:
  70:     """
  71:     输入清单 {键: {label, sha}}。生成时传 evidence（取其中用到的资料）；采纳时传记录中的 asset_keys，
  72:     逐条按当前资料库重新计算（资料被删除时 sha 为 deleted）。
  73:     """
  74:     items = {it.id: it for it in rp.target_items(project.tender_analysis)}
  75:     manifest = {
  76:         "facts": {"label": "全局事实", "sha": _sha(project.facts.model_dump())},
  77:         "scoring_items": {"label": "本节承接的评分项",
  78:                           "sha": _sha([items[x].model_dump() if x in items else x for x in node.scoring_item_ids])},
  79:         "evidence_links": {"label": "评分项关联的企业资料",
  80:                            "sha": _sha({x: sorted(project.evidence_links.get(x, [])) for x in node.scoring_item_ids})},
  81:         "ref_settings": {"label": "知识库引用的锁定与排除",
  82:                          "sha": _sha({"pinned": sorted(node.pinned_refs), "excluded": sorted(node.excluded_refs)})},
  83:         "section": {"label": "本节标题路径与字数预算",
  84:                     "sha": _sha({"path": section_path(project.outline, node.id), "word_budget": node.word_budget})},
  85:     }
  86:     keys = asset_keys if asset_keys is not None else (evidence_asset_keys(evidence) if evidence else [])
  87:     for key in keys:
  88:         kind, asset_id = parse_key(key)
  89:         asset = asset_manager.get_asset(kind, asset_id)
  90:         if asset is None:
  91:             manifest[ASSET_PREFIX + key] = {"label": f"企业资料（已删除）：{asset_id}", "sha": "deleted"}
  92:             continue
  93:         fingerprint = {k: v for k, v in asset.items() if k not in ASSET_VOLATILE_FIELDS}
  94:         manifest[ASSET_PREFIX + key] = {"label": f"企业资料：{asset_name(kind, asset)}", "sha": _sha(fingerprint)}
  95:     return manifest
```

<a id="tender-81"></a>
## tender.py:81

文件：`backend/app/api/tender.py`；基线查看命令：`git show 3aedaebebdb4a43f9ca2a2b63849e8ac7251284d:backend/app/api/tender.py`。

```text
  68: @router.post("/project/{project_id}/tender/apply", summary="将18项拆标结果应用到项目（返回承诺建议，不改动全局事实）")
  69: def apply_tender_analysis(project_id: str, analysis: TenderAnalysis18, tender_text: str = Body(default="", embed=True)):
  70:     def mutate(project):
  71:         project.tender_analysis = analysis
  72:         if analysis.purchaser_name and not project.client_name:
  73:             project.client_name = analysis.purchaser_name
  74:         # 全局事实是企业承诺，不在这里自动填写：工期/质保等只作为 commitment_suggestions 返回，由用户采纳
  75:         if project.stage == "created":
  76:             project.stage = "tender_analyzed"
  77:         return project
  78: 
  79:     project = project_store.update(project_id, mutate)
  80: 
  81:     if tender_text and tender_text.strip():
  82:         project_store.set_tender_text(project_id, tender_text)
  83: 
  84:     return {
  85:         "status": "success",
  86:         "stage": project.stage,
  87:         "facts": project.facts,
```

<a id="deviations-97"></a>
## deviations.py:97

文件：`backend/app/api/deviations.py`；基线查看命令：`git show 3aedaebebdb4a43f9ca2a2b63849e8ac7251284d:backend/app/api/deviations.py`。

```text
  88:     def _run(ctx):
  89:         answered = deviation_engine.batch_generate_responses(
  90:             pending, project.facts, progress=ctx, exclude=generation_filter(project))
  91:         by_clause = {it.clause_title: it for it in answered if it.response_status != "待生成"}
  92: 
  93:         def merge(latest):
  94:             # 写回最新偏离表：只回填仍为“待生成”的同一条款，生成期间的人工编辑优先
  95:             for item in latest.deviation_matrix:
  96:                 src = by_clause.get(item.clause_title)
  97:                 if src and item.response_status == "待生成":
  98:                     item.response_status = src.response_status
  99:                     item.response_detail = src.response_detail
 100:             return latest.deviation_matrix
 101: 
 102:         items = project_store.update(project_id, merge)
 103:         return {
 104:             "total_items": len(items),
 105:             "generated_count": len(by_clause),
 106:             "skipped_count": skipped,
```
