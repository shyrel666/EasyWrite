const { createApp, ref, computed, onMounted, watch } = Vue;

createApp({
  setup() {
    // ================= 核心状态定义 =================
    const activeTab = ref('workspace'); // workspace, tender, deviation, compliance, assets, knowledge, templates
    const viewMode = ref('preview');    // edit, preview
    const sidebarTab = ref('rag');       // rag, facts, prompt, assets
    
    // 项目与大纲
    const projectList = ref([]);
    const currentProject = ref(null);
    const selectedSection = ref(null);
    const editorContent = ref('');
    const customInstruction = ref('');
    const currentReferences = ref([]);
    
    // 异步加载与处理状态
    const isGenerating = ref(false);
    const isExporting = ref(false);
    const isAuditing = ref(false);
    const isQualityAuditing = ref(false);
    const isPolishing = ref(false);
    const isExtractingDeviations = ref(false);
    const isGeneratingDeviations = ref(false);
    const isAnalyzingTender = ref(false);
    const isSyncingTender = ref(false);

    // 报告数据
    const complianceReport = ref(null);
    const qualityReport = ref(null);
    const polishImprovements = ref([]);
    const tenderAnalysis = ref(null);
    const tenderInputText = ref('');

    // 知识库与检索
    const knowledgeStats = ref({ total_chunks: 0, total_documents: 0, available_tags: [] });
    const searchQuery = ref('');
    const searchResults = ref([]);
    
    // Word 模板管理
    const templateList = ref([]);
    const selectedTemplateId = ref('gov_standard');

    // 企业资产中台数据 (Yibiao-Web)
    const activeAssetSubTab = ref('qualifications'); // qualifications, personnel, cases, components
    const assetStats = ref({ total_qualifications: 0, total_personnel: 0, total_cases: 0, total_components: 0 });
    const qualificationList = ref([]);
    const personnelList = ref([]);
    const caseList = ref([]);
    const componentList = ref([]);
    const assetSearchQuery = ref('');

    // 弹窗状态
    const showNewProjectModal = ref(false);
    const showNewTemplateModal = ref(false);
    const showAddAssetModal = ref(false);
    const showAiSettingsModal = ref(false);

    // AI 模型配置与流式输出状态
    const showApiKey = ref(false);
    const isProbing = ref(false);
    const isGeneratingStream = ref(false);
    let streamAbortController = null;
    const aiProbeStatus = ref(null);
    const aiSettings = ref({
      provider: 'deepseek',
      api_key_masked: '',
      has_api_key: false,
      base_url: '',
      model: '',
      temperature: 0.3,
      max_tokens: 4096,
      presets: {}
    });
    const aiSettingsForm = ref({
      provider: 'deepseek',
      api_key: '',
      base_url: '',
      model: '',
      temperature: 0.3,
      max_tokens: 4096
    });

    const isFetchingModels = ref(false);
    const fetchModelsNotice = ref(null);
    const isCustomModelInput = ref(false);
    const customModelInput = ref('');

    const currentAvailableModels = computed(() => {
      const p = aiSettingsForm.value.provider;
      const preset = aiSettings.value.presets?.[p];
      let list = preset?.available_models ? [...preset.available_models] : [];
      if (aiSettingsForm.value.model && !list.includes(aiSettingsForm.value.model) && aiSettingsForm.value.model !== '__custom__') {
        list.unshift(aiSettingsForm.value.model);
      }
      return list;
    });
    const newProjectForm = ref({
      name: '',
      client_name: '',
      description: ''
    });

    const newAssetForm = ref({
      type: 'qualifications',
      name: '',
      category: '综合资质',
      cert_no: '',
      level: '标准级',
      issue_org: '',
      role: '高级架构师',
      years_of_experience: 10,
      education: '大学本科',
      professional_title: '高级工程师',
      certificates_str: 'PMP、软考高级',
      client_name: '',
      contract_amount: '',
      tags_str: '高可用、架构设计',
      summary: '',
      content: ''
    });

    const newTemplateForm = ref({
      name: '',
      primary_font: '宋体',
      heading_font: '黑体',
      theme_color: '#003366',
      margin_top: 2.54,
      margin_bottom: 2.54,
      margin_left: 3.0,
      margin_right: 2.54,
      header_text: '技术投标文件',
      description: ''
    });

    // ================= 计算属性 =================
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

    // 将 Markdown 正文解析渲染为漂亮公文 HTML
    const renderedHtmlContent = computed(() => {
      const text = editorContent.value || '*(暂无正文内容，请点击上方按钮进行智能撰写或补充)*';
      let html = text;

      html = html.replace(/^#### (.*$)/gim, '<h4>$1</h4>');
      html = html.replace(/^### (.*$)/gim, '<h3>$1</h3>');
      html = html.replace(/^## (.*$)/gim, '<h2>$1</h2>');
      html = html.replace(/^# (.*$)/gim, '<h1>$1</h1>');
      html = html.replace(/\*\*(.*?)\*\*/gim, '<strong>$1</strong>');

      const lines = html.split('\n');
      const formattedLines = [];
      let inTable = false;
      let tableRows = [];
      let inMermaid = false;
      let mermaidLines = [];

      for (let line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;

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
              `<div class="text-xs font-bold text-blue-900 mb-2 font-sans tracking-wide">【系统总体逻辑架构与数据交互流向图 (Mermaid 拓扑)】</div>` +
              `<pre class="mermaid text-left font-mono text-xs bg-white p-3 rounded border border-slate-200 overflow-x-auto text-slate-700">${mermaidLines.join('\n')}</pre>` +
              `<div class="text-xs text-slate-500 mt-2 font-serif italic">图：总体逻辑架构与数据交互流向图</div>` +
              `</div>`
            );
          } else {
            mermaidLines.push(trimmed);
          }
          continue;
        }

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
        if (r.replace(/[|\s-]/g, '') === '') continue;
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

    // ================= 项目管理与持久化 (B/S 协同) =================

    async function loadProjects() {
      try {
        const res = await fetch('/api/v1/projects');
        if (res.ok) {
          projectList.value = await res.json();
        }
      } catch (e) {
        console.error('获取项目列表失败:', e);
      }
    }

    async function fetchInitialProject() {
      try {
        await loadProjects();
        if (projectList.value && projectList.value.length > 0) {
          // 加载首个现有持久化项目
          await switchProject(projectList.value[0].id);
        } else {
          // 若没有任何项目，新建示范项目
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
          await loadProjects();
          loadDeviations();
        }
      } catch (e) {
        console.error('加载项目失败:', e);
      }
    }

    async function switchProject(projId) {
      try {
        const res = await fetch(`/api/v1/project/${projId}`);
        if (res.ok) {
          const proj = await res.json();
          currentProject.value = proj;
          tenderAnalysis.value = proj.tender_analysis || null;
          if (proj.outline && proj.outline.length) {
            selectSection(proj.outline[0]);
          }
          loadDeviations();
          showNewProjectModal.value = false;
        }
      } catch (e) {
        alert('切换项目失败: ' + e);
      }
    }

    async function deleteProject(projId) {
      if (!confirm('确定彻底删除该标书项目及其全部已编写章节吗？')) return;
      try {
        const res = await fetch(`/api/v1/project/${projId}`, { method: 'DELETE' });
        if (res.ok) {
          await loadProjects();
          if (currentProject.value?.id === projId) {
            if (projectList.value.length > 0) {
              await switchProject(projectList.value[0].id);
            } else {
              currentProject.value = null;
            }
          }
          alert('项目已安全删除！');
        }
      } catch (e) {
        alert('删除项目失败: ' + e);
      }
    }

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
        await loadProjects();
        loadDeviations();
        alert(`标书项目【${proj.name}】创建成功并已持久化保存！`);
      } catch (e) {
        alert('创建项目失败: ' + e);
      }
    }

    // ================= 招标文件 18 项结构化拆解台 (OpenBidKit) =================

    async function analyzeTender() {
      isAnalyzingTender.value = true;
      try {
        const textToAnalyze = tenderInputText.value || currentProject.value?.description || currentProject.value?.name;
        const blob = new Blob([textToAnalyze], { type: 'text/plain' });
        const formData = new FormData();
        // 模拟传递 docx 格式名称
        formData.append('file', blob, '招标文件正文.docx');
        
        // 调用提取 API
        const res = await fetch('/api/v1/tender/analyze', {
          method: 'POST',
          body: formData
        });
        if (res.ok) {
          const data = await res.json();
          tenderAnalysis.value = data;
          if (currentProject.value) {
            currentProject.value.tender_analysis = data;
          }
          alert(`招标文件 18 项要素拆解完成！抓取到最高限价【${data.budget_limit}】，★号废标红线 ${data.star_disqualification_items?.length || 0} 条。`);
        }
      } catch (e) {
        alert('拆解招标文件失败: ' + e);
      } finally {
        isAnalyzingTender.value = false;
      }
    }

    async function syncTenderToProjectFacts() {
      if (!currentProject.value || !tenderAnalysis.value) return;
      isSyncingTender.value = true;
      try {
        const res = await fetch(`/api/v1/project/${currentProject.value.id}/tender/sync-to-facts`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(tenderAnalysis.value)
        });
        if (res.ok) {
          const data = await res.json();
          currentProject.value.facts = data.facts;
          alert('18 项招标文件核心要素已一键同步并固化至本项目的【全局事实约束】中！');
        }
      } catch (e) {
        alert('同步失败: ' + e);
      } finally {
        isSyncingTender.value = false;
      }
    }

    async function syncTenderToDeviations() {
      if (!currentProject.value || !tenderAnalysis.value) return;
      try {
        const res = await fetch(`/api/v1/project/${currentProject.value.id}/tender/sync-to-deviations`, {
          method: 'POST'
        });
        if (res.ok) {
          const data = await res.json();
          alert(`成功将 ${data.added_count} 项★号废标条款全量同步至技术偏离表！`);
          loadDeviations();
        } else {
          const err = await res.json();
          alert('同步提示: ' + err.detail);
        }
      } catch (e) {
        alert('同步偏离表失败: ' + e);
      }
    }

    // ================= 标书编纂、大纲与降AI味润色 (YuduBid) =================

    function selectSection(node) {
      selectedSection.value = node;
      editorContent.value = node.content || '';
      currentReferences.value = [];
      polishImprovements.value = [];
    }

    function getSectionDisplayIndex() {
      if (!selectedSection.value) return '1';
      return selectedSection.value.id.replace('sec_', '').replace('_', '.');
    }

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

        setTimeout(() => {
          if (window.mermaid) {
            window.mermaid.init(undefined, document.querySelectorAll('.mermaid'));
          }
        }, 120);
      } catch (e) {
        alert('分章草拟失败: ' + e);
      } finally {
        isGenerating.value = false;
      }
    }

    async function generateCurrentSectionStream() {
      if (!selectedSection.value || !currentProject.value) return;
      isGeneratingStream.value = true;
      viewMode.value = 'edit';
      editorContent.value = '';
      selectedSection.value.content = '';
      currentReferences.value = [];
      polishImprovements.value = [];

      streamAbortController = new AbortController();

      try {
        const response = await fetch(`/api/v1/project/${currentProject.value.id}/section/generate/stream`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          signal: streamAbortController.signal,
          body: JSON.stringify({
            project_id: currentProject.value.id,
            section_id: selectedSection.value.id,
            section_title: selectedSection.value.title,
            section_path: selectedSection.value.path,
            requirements: selectedSection.value.requirements || [],
            custom_instruction: customInstruction.value
          })
        });

        if (!response.ok) {
          throw new Error('流式请求响应异常: ' + response.statusText);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop();

          for (const line of lines) {
            const trimmed = line.trim();
            if (trimmed.startsWith('data: ')) {
              try {
                const data = JSON.parse(trimmed.slice(6));
                if (data.token) {
                  editorContent.value += data.token;
                  selectedSection.value.content = editorContent.value;
                }
                if (data.done) {
                  selectedSection.value.status = 'completed';
                }
              } catch (parseErr) {
                // ignore
              }
            }
          }
        }

        setTimeout(() => {
          if (window.mermaid) {
            window.mermaid.init(undefined, document.querySelectorAll('.mermaid'));
          }
        }, 120);
      } catch (e) {
        if (e.name !== 'AbortError') {
          console.error('流式生成异常，切入同步草拟:', e);
          await generateCurrentSection();
        }
      } finally {
        isGeneratingStream.value = false;
        streamAbortController = null;
      }
    }

    function stopGeneratingStream() {
      if (streamAbortController) {
        streamAbortController.abort();
        isGeneratingStream.value = false;
        alert('已终止流式生成');
      }
    }

    async function generateAiCustomOutline() {
      if (!currentProject.value) return;
      if (!confirm('确定由 AI 根据当前招标需求自适应定制全新技术标大纲吗？现有未保存章节将被覆盖。')) return;
      try {
        const res = await fetch(`/api/v1/project/${currentProject.value.id}/outline/generate-ai`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ rfp_summary: tenderAnalysis.value ? JSON.stringify(tenderAnalysis.value) : currentProject.value.description })
        });
        if (res.ok) {
          const data = await res.json();
          currentProject.value.outline = data.outline;
          if (data.outline && data.outline.length > 0) {
            selectSection(data.outline[0]);
          }
          alert('AI 专属大纲已重新生成并更新！');
        }
      } catch (e) {
        alert('定制大纲失败: ' + e);
      }
    }

    function openAiSettingsModal() {
      aiSettingsForm.value = {
        provider: aiSettings.value.provider || 'deepseek',
        api_key: '',
        base_url: aiSettings.value.base_url || '',
        model: aiSettings.value.model || '',
        temperature: aiSettings.value.temperature !== undefined ? aiSettings.value.temperature : 0.3,
        max_tokens: aiSettings.value.max_tokens || 4096
      };
      aiProbeStatus.value = null;
      fetchModelsNotice.value = null;
      isCustomModelInput.value = false;
      showAiSettingsModal.value = true;
    }

    async function loadAiSettings() {
      try {
        const res = await fetch('/api/v1/ai/settings');
        if (res.ok) {
          const data = await res.json();
          aiSettings.value = data;
          aiSettingsForm.value = {
            provider: data.provider || 'deepseek',
            api_key: '',
            base_url: data.base_url || '',
            model: data.model || '',
            temperature: data.temperature !== undefined ? data.temperature : 0.3,
            max_tokens: data.max_tokens || 4096
          };
        }
      } catch (e) {
        console.error('加载 AI 配置失败:', e);
      }
    }

    function onProviderChange() {
      const p = aiSettingsForm.value.provider;
      const preset = aiSettings.value.presets?.[p];
      if (preset) {
        aiSettingsForm.value.base_url = preset.base_url || '';
        aiSettingsForm.value.model = preset.default_model || '';
      }
      fetchModelsNotice.value = null;
      isCustomModelInput.value = false;
    }

    async function fetchOnlineAiModels() {
      isFetchingModels.value = true;
      fetchModelsNotice.value = null;
      try {
        const res = await fetch('/api/v1/ai/models/fetch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            provider: aiSettingsForm.value.provider,
            api_key: aiSettingsForm.value.api_key || undefined,
            base_url: aiSettingsForm.value.base_url
          })
        });
        const data = await res.json();
        if (data.ok && data.models && data.models.length > 0) {
          const p = aiSettingsForm.value.provider;
          if (!aiSettings.value.presets) aiSettings.value.presets = {};
          if (!aiSettings.value.presets[p]) aiSettings.value.presets[p] = {};
          aiSettings.value.presets[p].available_models = data.models;
          if (!data.models.includes(aiSettingsForm.value.model)) {
            aiSettingsForm.value.model = data.models[0];
          }
          fetchModelsNotice.value = {
            ok: true,
            message: data.message || `已成功联网获取 ${data.count} 个最新实时模型！`
          };
        } else {
          fetchModelsNotice.value = {
            ok: false,
            message: data.message || data.error || '获取失败，已载入最新推荐模型列表'
          };
        }
      } catch (e) {
        fetchModelsNotice.value = {
          ok: false,
          message: '联网请求异常: ' + e
        };
      } finally {
        isFetchingModels.value = false;
      }
    }

    async function testAiProbe() {
      isProbing.value = true;
      aiProbeStatus.value = null;
      try {
        const res = await fetch('/api/v1/ai/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            api_key: aiSettingsForm.value.api_key || undefined,
            base_url: aiSettingsForm.value.base_url,
            model: aiSettingsForm.value.model
          })
        });
        const data = await res.json();
        aiProbeStatus.value = data;
      } catch (e) {
        aiProbeStatus.value = { ok: false, error: '网络请求异常: ' + e };
      } finally {
        isProbing.value = false;
      }
    }

    async function saveAiSettings() {
      try {
        const res = await fetch('/api/v1/ai/settings', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(aiSettingsForm.value)
        });
        if (res.ok) {
          const data = await res.json();
          aiSettings.value = data.settings;
          aiProbeStatus.value = null;
          showAiSettingsModal.value = false;
          alert('AI 模型与算力配置已更新并热加载生效！');
        }
      } catch (e) {
        alert('保存 AI 配置失败: ' + e);
      }
    }

    async function polishCurrentSection() {
      if (!selectedSection.value || !currentProject.value) return;
      if (!editorContent.value.trim()) {
        alert('当前章节为空，请先编写或生成正文！');
        return;
      }
      isPolishing.value = true;
      try {
        const res = await fetch(`/api/v1/project/${currentProject.value.id}/section/polish`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            project_id: currentProject.value.id,
            section_id: selectedSection.value.id,
            content: editorContent.value,
            polish_mode: 'de_ai'
          })
        });
        if (res.ok) {
          const data = await res.json();
          editorContent.value = data.polished_content;
          selectedSection.value.content = data.polished_content;
          selectedSection.value.status = 'reviewed';
          polishImprovements.value = data.improvements || [];
          alert('深度降AI味润色完成！已剔除常见AI虚浮套话，强化了具体工程参数与企业主体一致性。');
        }
      } catch (e) {
        alert('润色失败: ' + e);
      } finally {
        isPolishing.value = false;
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
        alert('章节正文已保存并标记为【已校审】！');
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
        alert('全局事实约束已更新并保存！后续各章节编纂将强制遵守最新事实。');
      } catch (e) {
        alert('更新事实失败: ' + e);
      }
    }

    function appendReferenceToEditor(refText) {
      editorContent.value += '\n\n【参考历史方案资产】：\n' + refText;
      alert('已成功将资产引入当前正文末尾！');
    }

    function insertAssetIntoEditor(assetText) {
      editorContent.value += '\n\n' + assetText;
      alert('已将企业中台标准资产段落插入当前章节！');
    }

    // ================= 技术偏离表工作台 =================

    async function loadDeviations() {
      if (!currentProject.value?.id) return;
      try {
        const res = await fetch(`/api/v1/project/${currentProject.value.id}/deviation`);
        if (res.ok) {
          const data = await res.json();
          currentProject.value.deviation_matrix = data.items || [];
        }
      } catch (e) {
        console.error('加载偏离表失败:', e);
      }
    }

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

    // ================= 八维质量与废标合规体检 (YuduBid / OpenBidKit) =================

    async function runEightDimensionAudit() {
      if (!currentProject.value) return;
      isQualityAuditing.value = true;
      try {
        const res = await fetch(`/api/v1/project/${currentProject.value.id}/quality/inspect`, {
          method: 'POST'
        });
        if (res.ok) {
          qualityReport.value = await res.json();
        }
      } catch (e) {
        alert('执行八维体检失败: ' + e);
      } finally {
        isQualityAuditing.value = false;
      }
    }

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

    // ================= 企业中台资产库 (Yibiao-Web) =================

    async function loadAssets() {
      try {
        const [statsRes, qualRes, personRes, caseRes, compRes] = await Promise.all([
          fetch('/api/v1/assets/stats'),
          fetch('/api/v1/assets/qualifications'),
          fetch('/api/v1/assets/personnel'),
          fetch('/api/v1/assets/cases'),
          fetch('/api/v1/assets/components')
        ]);

        if (statsRes.ok) assetStats.value = await statsRes.json();
        if (qualRes.ok) qualificationList.value = await qualRes.json();
        if (personRes.ok) personnelList.value = await personRes.json();
        if (caseRes.ok) caseList.value = await caseRes.json();
        if (compRes.ok) componentList.value = await compRes.json();
      } catch (e) {
        console.error('加载企业中台资产失败:', e);
      }
    }

    async function saveNewAsset() {
      const type = newAssetForm.value.type;
      let url = '';
      let payload = {};

      if (type === 'qualifications') {
        url = '/api/v1/assets/qualifications';
        payload = {
          id: `qual_${Date.now().toString(36)}`,
          name: newAssetForm.value.name,
          cert_no: newAssetForm.value.cert_no || 'CERT-2026-001',
          category: newAssetForm.value.category,
          level: newAssetForm.value.level,
          issue_org: newAssetForm.value.issue_org || '国家认证管理机构',
          summary: newAssetForm.value.summary || '具备高分投标资质'
        };
      } else if (type === 'personnel') {
        url = '/api/v1/assets/personnel';
        payload = {
          id: `person_${Date.now().toString(36)}`,
          name: newAssetForm.value.name,
          role: newAssetForm.value.role,
          years_of_experience: Number(newAssetForm.value.years_of_experience) || 8,
          education: newAssetForm.value.education,
          professional_title: newAssetForm.value.professional_title,
          certificates: newAssetForm.value.certificates_str.split(/[、,，\s]+/).filter(Boolean),
          intro: newAssetForm.value.summary || '拥有多年特大型项目建设经验'
        };
      } else if (type === 'cases') {
        url = '/api/v1/assets/cases';
        payload = {
          id: `case_${Date.now().toString(36)}`,
          project_name: newAssetForm.value.name,
          client_name: newAssetForm.value.client_name || '某采购人',
          contract_amount: newAssetForm.value.contract_amount || '1,000.00 万元',
          contract_category: newAssetForm.value.category,
          acceptance_status: '已终验合格并平稳投运',
          summary: newAssetForm.value.summary || '标杆示范工程'
        };
      } else if (type === 'components') {
        url = '/api/v1/assets/components';
        payload = {
          id: `comp_${Date.now().toString(36)}`,
          name: newAssetForm.value.name,
          category: newAssetForm.value.category,
          tags: newAssetForm.value.tags_str.split(/[、,，\s]+/).filter(Boolean),
          summary: newAssetForm.value.summary,
          content: newAssetForm.value.content || '### 方案设计\n方案设计论述...'
        };
      }

      try {
        const res = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (res.ok) {
          alert('企业中台资产新增成功！');
          showAddAssetModal.value = false;
          loadAssets();
        }
      } catch (e) {
        alert('保存资产失败: ' + e);
      }
    }

    async function deleteAssetItem(type, id) {
      if (!confirm('确定删除该项企业中台资产吗？')) return;
      try {
        const res = await fetch(`/api/v1/assets/${type}/${id}`, { method: 'DELETE' });
        if (res.ok) {
          loadAssets();
        }
      } catch (e) {
        alert('删除失败: ' + e);
      }
    }

    // ================= Word 模板与导出 =================

    async function loadTemplates() {
      try {
        const res = await fetch('/api/v1/templates/list');
        const data = await res.json();
        templateList.value = data.templates || [];
      } catch (e) {
        console.error('加载模板列表失败:', e);
      }
    }

    async function createCustomTemplate() {
      if (!newTemplateForm.value.name) {
        alert('请输入模板名称');
        return;
      }
      try {
        const res = await fetch('/api/v1/templates/create', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(newTemplateForm.value)
        });
        if (res.ok) {
          showNewTemplateModal.value = false;
          loadTemplates();
          alert('自定义 Word 模板已保存！');
        }
      } catch (e) {
        alert('创建模板失败: ' + e);
      }
    }

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
        setTimeout(() => { isExporting.value = false; }, 1500);
      }
    }

    // ================= 历史标书切片检索 =================

    async function loadKnowledgeStats() {
      try {
        const res = await fetch('/api/v1/knowledge/stats');
        const data = await res.json();
        knowledgeStats.value = data;
      } catch (e) {
        console.error('加载知识库统计失败:', e);
      }
    }

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
        alert(`历史标书【${data.filename}】入库成功！共识别 ${data.total_sections_parsed} 个大纲章节，生成 ${data.chunks_indexed} 个高质量切片。`);
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

    // ================= 生命周期挂载 =================
    onMounted(() => {
      if (window.mermaid) {
        window.mermaid.initialize({ startOnLoad: false, theme: 'neutral' });
      }
      fetchInitialProject();
      loadTemplates();
      loadAssets();
      loadKnowledgeStats();
      loadAiSettings();
    });

    return {
      activeTab,
      viewMode,
      sidebarTab,
      projectList,
      currentProject,
      selectedSection,
      editorContent,
      customInstruction,
      currentReferences,
      isGenerating,
      isExporting,
      isAuditing,
      isQualityAuditing,
      isPolishing,
      isExtractingDeviations,
      isGeneratingDeviations,
      isAnalyzingTender,
      isSyncingTender,
      complianceReport,
      qualityReport,
      polishImprovements,
      tenderAnalysis,
      tenderInputText,
      knowledgeStats,
      searchQuery,
      searchResults,
      templateList,
      selectedTemplateId,
      activeAssetSubTab,
      assetStats,
      qualificationList,
      personnelList,
      caseList,
      componentList,
      assetSearchQuery,
      showNewProjectModal,
      showNewTemplateModal,
      showAddAssetModal,
      showAiSettingsModal,
      showApiKey,
      isProbing,
      isGeneratingStream,
      aiProbeStatus,
      aiSettings,
      aiSettingsForm,
      newProjectForm,
      newAssetForm,
      newTemplateForm,
      completedSectionCount,
      totalSectionCount,
      renderedHtmlContent,
      loadProjects,
      switchProject,
      deleteProject,
      createNewProject,
      selectSection,
      getSectionDisplayIndex,
      generateCurrentSection,
      generateCurrentSectionStream,
      stopGeneratingStream,
      generateAiCustomOutline,
      loadAiSettings,
      openAiSettingsModal,
      onProviderChange,
      fetchOnlineAiModels,
      isFetchingModels,
      fetchModelsNotice,
      isCustomModelInput,
      customModelInput,
      currentAvailableModels,
      testAiProbe,
      saveAiSettings,
      polishCurrentSection,
      saveCurrentSection,
      saveGlobalFacts,
      appendReferenceToEditor,
      insertAssetIntoEditor,
      analyzeTender,
      syncTenderToProjectFacts,
      syncTenderToDeviations,
      extractDeviations,
      batchGenerateDeviations,
      injectDeviationsToOutline,
      countStarDeviations,
      countStatusDeviations,
      runComplianceAudit,
      runEightDimensionAudit,
      loadAssets,
      saveNewAsset,
      deleteAssetItem,
      exportWord,
      createCustomTemplate,
      handleFileUpload,
      testSearchKnowledge
    };
  }
}).mount('#app');
