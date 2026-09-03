const { createApp, ref, computed, onMounted, watch } = Vue;

createApp({
  setup() {
    // 状态定义
    const activeTab = ref('workspace');
    const viewMode = ref('preview');
    const sidebarTab = ref('rag');
    
    const currentProject = ref(null);
    const selectedSection = ref(null);
    const editorContent = ref('');
    const customInstruction = ref('');
    const currentReferences = ref([]);
    
    const isGenerating = ref(false);
    const isExporting = ref(false);
    const isAuditing = ref(false);
    const isExtractingDeviations = ref(false);
    const isGeneratingDeviations = ref(false);

    const complianceReport = ref(null);
    const knowledgeStats = ref({ total_chunks: 0, total_documents: 0, available_tags: [] });
    const searchQuery = ref('');
    const searchResults = ref([]);
    
    const templateList = ref([]);
    const selectedTemplateId = ref('gov_standard');

    const showNewProjectModal = ref(false);
    const showNewTemplateModal = ref(false);
    const newProjectForm = ref({
      name: '',
      client_name: '',
      description: ''
    });

    // 计算属性: 统计大纲节点完成度
    const completedSectionCount = computed(() => {
      if (!currentProject.value?.outline) return 0;
      let count = 0;
      function traverse(nodes) {
        for (const n of nodes) {
          if (n.status === 'completed' || n.status === 'reviewed') count++;
          if (n.children) traverse(n.children);
        }
      }
      traverse(currentProject.value.outline);
      return count;
    });

    const totalSectionCount = computed(() => {
      if (!currentProject.value?.outline) return 0;
      let count = 0;
      function traverse(nodes) {
        for (const n of nodes) {
          count++;
          if (n.children) traverse(n.children);
        }
      }
      traverse(currentProject.value.outline);
      return count;
    });

    // 计算属性: 将 Markdown 正文解析渲染为漂亮公文 HTML
    const renderedHtmlContent = computed(() => {
      const text = editorContent.value || '*(暂无正文内容，请点击上方按钮进行智能撰写)*';
      let html = text;

      // 替换 Markdown 标题
      html = html.replace(/^### (.*$)/gim, '<h3>$1</h3>');
      html = html.replace(/^## (.*$)/gim, '<h2>$1</h2>');
      html = html.replace(/^# (.*$)/gim, '<h1>$1</h1>');

      // 替换加粗
      html = html.replace(/\*\*(.*?)\*\*/gim, '<strong>$1</strong>');

      // 替换普通段落 (两端无标签的普通行换成 <p>)
      const lines = html.split('\n');
      const formattedLines = [];
      let inTable = false;
      let tableRows = [];
      let inMermaid = false;
      let mermaidLines = [];

      for (let line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;

        // Mermaid 绘图块
        if (trimmed.startsWith('```mermaid')) {
          inMermaid = true;
          mermaidLines = [];
          continue;
        }
        if (inMermaid) {
          if (trimmed.startsWith('```')) {
            inMermaid = false;
            formattedLines.push(
              `<div class="my-4 p-4 rounded-lg bg-slate-50 border border-slate-200 text-center">` +
              `<div class="text-xs font-bold text-blue-900 mb-2 font-sans tracking-wide">【系统架构与业务流向拓扑图】</div>` +
              `<pre class="mermaid text-left font-mono text-xs bg-white p-3 rounded border border-slate-200 overflow-x-auto text-slate-700">${mermaidLines.join('\n')}</pre>` +
              `<div class="text-xs text-slate-500 mt-2 font-serif italic">图：总体逻辑架构与数据交互流向图</div>` +
              `</div>`
            );
          } else {
            mermaidLines.push(trimmed);
          }
          continue;
        }

        // Markdown 表格处理
        if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
          inTable = true;
          tableRows.push(trimmed);
          continue;
        } else if (inTable) {
          formattedLines.push(renderHtmlTable(tableRows));
          tableRows = [];
          inTable = false;
        }

        if (trimmed.startsWith('<h') || trimmed.startsWith('<div')) {
          formattedLines.push(trimmed);
        } else if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
          formattedLines.push(`<div class="ml-8 my-1 flex items-start text-sm"><span class="mr-2 text-slate-400">●</span><span>${trimmed.substring(2)}</span></div>`);
        } else {
          formattedLines.push(`<p>${trimmed}</p>`);
        }
      }

      if (inTable && tableRows.length) {
        formattedLines.push(renderHtmlTable(tableRows));
      }

      return formattedLines.join('');
    });

    function renderHtmlTable(rows) {
      if (rows.length < 2) return '';
      let html = '<table class="my-4 border-collapse w-full text-sm">';
      let isHeader = true;
      for (let r of rows) {
        if (r.replace(/[|\s-]/g, '') === '') continue; // 分割行跳过
        const cells = r.split('|').map(c => c.trim()).filter((_, idx, arr) => idx > 0 && idx < arr.length - 1);
        html += '<tr>';
        for (let cell of cells) {
          if (isHeader) {
            html += `<th class="bg-slate-100 border border-slate-300 px-3 py-2 font-bold">${cell}</th>`;
          } else {
            html += `<td class="border border-slate-300 px-3 py-2">${cell}</td>`;
          }
        }
        html += '</tr>';
        isHeader = false;
      }
      html += '</table>';
      return html;
    }

    // ================= 初始化与数据交互 =================

    async function fetchInitialProject() {
      try {
        // 先检测是否有现有项目，若无则新建默认项目
        const res = await fetch('/api/v1/project/create', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: '2026年市级智慧水务一体化综合调度系统建设项目',
            client_name: '某市水务环境集团有限公司',
            description: '★ 必须具备自主可控软件著作权。支持国产达梦数据库。提供7×24小时现场技术支持。'
          })
        });
        const proj = await res.json();
        currentProject.value = proj;
        if (proj.outline && proj.outline.length) {
          selectSection(proj.outline[0]);
        }
        loadDeviations();
      } catch (e) {
        console.error('加载项目失败:', e);
      }
    }

    async function loadTemplates() {
      try {
        const res = await fetch('/api/v1/templates/list');
        const data = await res.json();
        templateList.value = data.templates || [];
      } catch (e) {
        console.error('加载模板列表失败:', e);
      }
    }

    async function loadKnowledgeStats() {
      try {
        const res = await fetch('/api/v1/knowledge/stats');
        const data = await res.json();
        knowledgeStats.value = data;
      } catch (e) {
        console.error('加载知识库统计失败:', e);
      }
    }

    async function loadDeviations() {
      if (!currentProject.value?.id) return;
      try {
        const res = await fetch(`/api/v1/project/${currentProject.value.id}/deviation/extract`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({})
        });
        const data = await res.json();
        if (data.items) {
          currentProject.value.deviation_matrix = data.items;
        }
      } catch (e) {
        console.error('加载偏离表失败:', e);
      }
    }

    function selectSection(node) {
      selectedSection.value = node;
      editorContent.value = node.content || '';
      currentReferences.value = [];
    }

    function getSectionDisplayIndex() {
      if (!selectedSection.value) return '1';
      return selectedSection.value.id.replace('sec_', '').replace('_', '.');
    }

    // ================= 核心 AI 生成流水线 =================

    async function generateCurrentSection() {
      if (!selectedSection.value || !currentProject.value) return;
      isGenerating.value = true;
      try {
        const res = await fetch(`/api/v1/project/${currentProject.value.id}/section/generate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            project_id: currentProject.value.id,
            section_id: selectedSection.value.id,
            section_title: selectedSection.value.title,
            section_path: selectedSection.value.path,
            requirements: selectedSection.value.requirements || [],
            custom_instruction: customInstruction.value
          })
        });
        const data = await res.json();
        editorContent.value = data.generated_content;
        selectedSection.value.content = data.generated_content;
        selectedSection.value.status = 'completed';
        currentReferences.value = data.reference_sources || [];

        // 刷新渲染 Mermaid 图表
        setTimeout(() => {
          if (window.mermaid) {
            window.mermaid.init(undefined, document.querySelectorAll('.mermaid'));
          }
        }, 100);
      } catch (e) {
        alert('分章生成失败: ' + e);
      } finally {
        isGenerating.value = false;
      }
    }

    async function saveCurrentSection() {
      if (!selectedSection.value || !currentProject.value) return;
      try {
        await fetch(`/api/v1/project/${currentProject.value.id}/section`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            section_id: selectedSection.value.id,
            content: editorContent.value
          })
        });
        selectedSection.value.content = editorContent.value;
        selectedSection.value.status = 'reviewed';
        alert('章节内容已保存并标记为【已校审】！');
      } catch (e) {
        alert('保存失败: ' + e);
      }
    }

    async function saveGlobalFacts() {
      if (!currentProject.value) return;
      try {
        await fetch(`/api/v1/project/${currentProject.value.id}/facts`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(currentProject.value.facts)
        });
        alert('全局事实约束已同步更新！后续章节编写将自动遵循最新事实。');
      } catch (e) {
        alert('更新全局事实失败: ' + e);
      }
    }

    function appendReferenceToEditor(refText) {
      editorContent.value += '\n\n【参考历史成熟方案】：\n' + refText;
      alert('已成功将历史方案资产引入当前正文末尾！');
    }

    // ================= 偏离表操作 =================

    async function extractDeviations() {
      if (!currentProject.value) return;
      isExtractingDeviations.value = true;
      try {
        const res = await fetch(`/api/v1/project/${currentProject.value.id}/deviation/extract`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({})
        });
        const data = await res.json();
        currentProject.value.deviation_matrix = data.items || [];
        alert(`成功提取 ${data.total_items} 条技术条款！`);
      } catch (e) {
        alert('提取失败: ' + e);
      } finally {
        isExtractingDeviations.value = false;
      }
    }

    async function batchGenerateDeviations() {
      if (!currentProject.value) return;
      isGeneratingDeviations.value = true;
      try {
        const res = await fetch(`/api/v1/project/${currentProject.value.id}/deviation/generate`, {
          method: 'POST'
        });
        const data = await res.json();
        currentProject.value.deviation_matrix = data.items || [];
        alert('AI 点对点技术偏离响应批量生成完成！已标注“完全满足/正偏离”。');
      } catch (e) {
        alert('生成偏离响应失败: ' + e);
      } finally {
        isGeneratingDeviations.value = false;
      }
    }

    async function injectDeviationsToOutline() {
      if (!currentProject.value) return;
      try {
        // 自动注入到 第4章 或 当前章节
        const targetId = selectedSection.value?.id || 'sec_4_1';
        const res = await fetch(`/api/v1/project/${currentProject.value.id}/deviation/generate?auto_inject_section_id=${targetId}`, {
          method: 'POST'
        });
        const data = await res.json();
        if (selectedSection.value && selectedSection.value.id === targetId) {
          editorContent.value = data.table_markdown;
        }
        alert(`已成功将最新技术偏离表完整注入标书章节【${targetId}】！`);
      } catch (e) {
        alert('回填失败: ' + e);
      }
    }

    function countStarDeviations() {
      return (currentProject.value?.deviation_matrix || []).filter(it => it.is_star).length;
    }

    function countStatusDeviations(status) {
      return (currentProject.value?.deviation_matrix || []).filter(it => it.response_status === status).length;
    }

    // ================= 废标合规自检 =================

    async function runComplianceAudit() {
      if (!currentProject.value) return;
      isAuditing.value = true;
      try {
        const res = await fetch(`/api/v1/project/${currentProject.value.id}/compliance/check`, {
          method: 'POST'
        });
        complianceReport.value = await res.json();
      } catch (e) {
        alert('合规审查失败: ' + e);
      } finally {
        isAuditing.value = false;
      }
    }

    // ================= 标书 Word 导出 =================

    async function exportWord() {
      if (!currentProject.value) return;
      isExporting.value = true;
      try {
        const url = `/api/v1/project/${currentProject.value.id}/export?template_id=${selectedTemplateId.value}`;
        const a = document.createElement('a');
        a.href = url;
        a.download = `${currentProject.value.name}_技术标书.docx`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
      } catch (e) {
        alert('导出失败: ' + e);
      } finally {
        setTimeout(() => { isExporting.value = false; }, 1200);
      }
    }

    // ================= 知识库管理 =================

    async function handleFileUpload(event) {
      const file = event.target.files[0];
      if (!file) return;
      const formData = new FormData();
      formData.append('file', file);
      try {
        const res = await fetch('/api/v1/knowledge/upload', {
          method: 'POST',
          body: formData
        });
        const data = await res.json();
        alert(`历史标书【${data.filename}】入库成功！共识别 ${data.total_sections_parsed} 个大纲章节，生成 ${data.chunks_indexed} 个高质量面包屑切片。`);
        loadKnowledgeStats();
      } catch (e) {
        alert('上传解析失败: ' + e);
      }
    }

    async function testSearchKnowledge() {
      if (!searchQuery.value) return;
      try {
        const res = await fetch('/api/v1/knowledge/search', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: searchQuery.value, top_k: 5 })
        });
        const data = await res.json();
        searchResults.value = data.results || [];
      } catch (e) {
        alert('检索失败: ' + e);
      }
    }

    // ================= 新建项目 =================

    async function createNewProject() {
      if (!newProjectForm.value.name) {
        alert('请输入标书项目名称');
        return;
      }
      try {
        const res = await fetch('/api/v1/project/create', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(newProjectForm.value)
        });
        const proj = await res.json();
        currentProject.value = proj;
        showNewProjectModal.value = false;
        if (proj.outline && proj.outline.length) {
          selectSection(proj.outline[0]);
        }
        loadDeviations();
        alert(`项目【${proj.name}】已成功创建并初始化大纲树！`);
      } catch (e) {
        alert('创建项目失败: ' + e);
      }
    }

    // 生命周期挂载
    onMounted(() => {
      if (window.mermaid) {
        window.mermaid.initialize({ startOnLoad: false, theme: 'neutral' });
      }
      fetchInitialProject();
      loadTemplates();
      loadKnowledgeStats();
    });

    return {
      activeTab,
      viewMode,
      sidebarTab,
      currentProject,
      selectedSection,
      editorContent,
      customInstruction,
      currentReferences,
      isGenerating,
      isExporting,
      isAuditing,
      isExtractingDeviations,
      isGeneratingDeviations,
      complianceReport,
      knowledgeStats,
      searchQuery,
      searchResults,
      templateList,
      selectedTemplateId,
      showNewProjectModal,
      showNewTemplateModal,
      newProjectForm,
      completedSectionCount,
      totalSectionCount,
      renderedHtmlContent,
      selectSection,
      getSectionDisplayIndex,
      generateCurrentSection,
      saveCurrentSection,
      saveGlobalFacts,
      appendReferenceToEditor,
      extractDeviations,
      batchGenerateDeviations,
      injectDeviationsToOutline,
      countStarDeviations,
      countStatusDeviations,
      runComplianceAudit,
      exportWord,
      handleFileUpload,
      testSearchKnowledge,
      createNewProject
    };
  }
}).mount('#app');
