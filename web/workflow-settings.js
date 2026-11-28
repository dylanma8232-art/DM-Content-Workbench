/* Loaded after dashboard.js. Project saving and run actions remain with the dashboard. */
(() => {
  'use strict';

  const scope = '当前项目后续新导入与采集批次；已有批次需重算保留旧版本';
  const sections = [
    {id: 'cleaningConfig', title: '清洗规则', fields: [
      ['min_length', '最短正文保留长度（字）', 'number', 1, 1, 10000, '例如：30，少于 30 字的正文会被排除。'],
      ['dedup_mode', '正文去重方式', 'select', 'exact', [['exact', '完全相同才去重'], ['similar', '相似正文去重'], ['off', '不去重']], null, '例如：转载内容较多时选择「相似正文去重」。'],
      ['dedup_threshold', '相似度阈值', 'number', 0.85, 0.7, 0.99, '仅相似正文去重使用。例如：0.90 表示达到 90% 相似度才去重。', 0.01],
      ['exclude_terms', '排除词（选填）', 'text', '', 2000, null, '多个词用逗号或换行分隔。例如：广告, 招聘；正文命中任一词会被排除。'],
      ['remove_links', '移除正文中的链接', 'checkbox', false, null, null, '例如：去掉正文中的 https://example.com，再判断长度和重复。原始素材仍保留。']
    ]},
    {id: 'analysisConfig', title: '分析规则', fields: [
      ['analysis_engine', '分析方式', 'select', 'evidence', [['evidence', '整理原句证据'], ['model', '使用 AI 分析']], null, '整理原句证据无需模型服务；AI 分析需要先配置内容理解服务。'],
      ['evidence_count', '每条素材最多提取几条证据', 'number', 3, 1, 5, '例如：3，每条素材最多保留三条可追溯表述。'],
      ['analysis_max_tokens', 'AI 分析输出长度上限', 'number', 2000, 256, 8000, '例如：2000，限制 AI 汇总研究结果时的输出文本单位数；与生产稿件的生成上限分别设置。'],
      ['focus', '原句提取优先关键词（选填）', 'text', '', 200, null, '例如：交付周期、成本、用户体验；留空按素材内容提取。']
    ]},
    {id: 'topicConfig', title: '选题规则', fields: [
      ['topics', '跨素材聚类主题词（选填）', 'text', '', 500, null, '多个词用逗号分隔。例如：周末, 排班, 会员留存, AI 工作流。'],
      ['topic_limit', '最多推荐几个选题', 'number', 10, 1, 20, '例如：10，按推荐顺序保留最多十个候选。'],
      ['min_topic_sources', '选题最少来源组数', 'number', 1, 1, 10, '例如：2，要求至少两个可识别来源组；来源组数不代表已核实相互独立。']
    ]},
    {id: 'writingConfig', title: '生产规则', fields: [
      ['audience', '默认目标读者受众', 'text', '', 200, null, '例如：一线产品经理与内容运营团队。'],
      ['style', '默认成稿文体', 'text', '文章', 100, null, '例如：公众号深度长文、行业洞察周报。'],
      ['target_words', '目标成稿字数', 'number', 1200, 100, 10000, '例如：1200；字数是写作目标，材料不足时需要补充证据。'],
      ['writing_requirements', '补充写作要求（选填）', 'textarea', '', 4000, null, '例如：先给结论，再列证据；保留来源编号，不补造数据。'],
      ['outline', '写作提纲（选填）', 'textarea', '', 4000, null, '例如：背景、主要发现、不同观点、仍需核实的问题。每行一项。'],
      ['generation_max_tokens', '单次生成长度上限', 'number', 2000, 256, 8000, '例如：2000，限制一次 AI 输出的文本单位数，与中文字数并不相等。']
    ]},
    {id: 'archiveConfig', title: '归档规则', fields: [
      ['archive_library_id', '审核后手动归档到线上资料库（选填）', 'select', '', [['', '不归档到线上资料库']], null, '审核通过后，可手动将稿件归档到已连接的 Notion 或飞书资料库。例如：飞书 · 团队成稿库。'],
      ['notion_database', 'Notion 自动快照数据库 ID（选填）', 'text', '', 100, null, '填写已授权给集成的 Notion 数据库 ID 或链接；留空仅保存在本机。']
    ]}
  ];

  function help(text) {
    return element('p', text, 'configHelp');
  }

  function localJump(id) {
    const target = byId(id);
    if (!target) return;
    if (window.matchMedia('(max-width: 700px)').matches) window.configurationCenter?.open(id);
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    target.scrollIntoView({block: 'start', behavior: reduced ? 'auto' : 'smooth'});
    target.querySelector('input, select, textarea')?.focus({preventScroll: true});
  }

  function updateHelp() {
    const form = byId('rules');
    if (!form) return;
    const modelHelp = byId('workflowModelHelp');
    if (modelHelp) {
      const configured = typeof connectionCache !== 'undefined' && Boolean(connectionCache.model);
      modelHelp.textContent = configured
        ? '内容理解服务已配置，使用 AI 分析前可在「内容处理能力」检测连接。AI 解析不等于作者原话或已核实事实。'
        : '使用 AI 分析需先在「内容处理能力」配置并检测服务。可以先选择「整理原句证据」；两种方式都保留可追溯依据。';
    }
    const threshold = byId('workflowHelp-dedup_threshold');
    if (threshold) threshold.textContent = form.elements.dedup_mode.value === 'similar'
      ? '例如：0.90 表示达到 90% 相似度才去重；阈值越高，保留的相似内容越多。'
      : '仅选择「相似正文去重」时使用。例如：0.90 表示达到 90% 相似度才去重。';
  }

  function buildForm() {
    const form = byId('rules');
    if (!form || form.dataset.workflowSettings === 'ready') return;
    const existing = new Map(Array.from(form.elements).filter(node => node.name).map(node => [node.name, node]));
    const oldChildren = Array.from(form.children);
    const save = form.querySelector('button[type="submit"], button:not([type])') || element('button');
    const content = document.createDocumentFragment();
    const scopeHelp = help(scope);
    scopeHelp.id = 'workflowConfigScope';
    content.append(scopeHelp);
    form.setAttribute('aria-describedby', [form.getAttribute('aria-describedby'), scopeHelp.id].filter(Boolean).join(' '));

    for (const section of sections) {
      const group = element('fieldset', '', 'stageSettings');
      group.id = section.id;
      group.append(element('legend', section.title));
      for (const [name, caption, type, fallback, minOrOptions, max, example, step] of section.fields) {
        let control = existing.get(name);
        if (!control) {
          control = element(type === 'textarea' ? 'textarea' : type === 'select' ? 'select' : 'input');
          control.name = name;
          if (type === 'select') {
            for (const [value, label] of minOrOptions) control.add(new Option(label, value));
          }
          if (type === 'checkbox') control.checked = fallback;
          else control.value = String(fallback);
        }
        if (!['select', 'textarea'].includes(type)) control.type = type;
        if (type === 'number') {
          control.min = String(minOrOptions);
          control.max = String(max);
          control.step = String(step || 1);
          control.required = true;
        } else if (type === 'text' || type === 'textarea') {
          control.maxLength = minOrOptions;
          if (type === 'textarea') control.rows = 4;
        }
        if (!control.id) control.id = 'workflowField-' + name;
        const label = element('label', caption);
        label.htmlFor = control.id;
        label.append(control);
        const description = help(example);
        description.id = 'workflowHelp-' + name;
        control.setAttribute('aria-describedby', [control.getAttribute('aria-describedby'), description.id].filter(Boolean).join(' '));
        if (name === 'notion_database') {
          const advanced = element('details');
          advanced.append(element('summary', '高级选项：Notion 自动 JSON 快照'), label, description,
            help('自动快照保存运行数据，与审核后手动归档稿件分别设置。'));
          group.append(advanced);
        } else if (name === 'analysis_max_tokens') {
          const advanced = element('details');
          advanced.append(element('summary', '高级选项：AI 分析输出预算'), label, description);
          group.append(advanced);
        } else {
          const field=element('div','','workflowField');
          if(type==='textarea'||type==='checkbox')field.classList.add('workflowFieldWide');
          field.append(label,description);group.append(field);
        }
      }
      if (section.id === 'analysisConfig') {
        const status = help('');
        status.id = 'workflowModelHelp';
        status.setAttribute('aria-live', 'polite');
        const engine = group.querySelector('[name="analysis_engine"]');
        engine.setAttribute('aria-describedby', engine.getAttribute('aria-describedby') + ' ' + status.id);
        group.append(status);
      }
      content.append(group);
    }
    // Preserve any additional incumbent fields, as well as the form's own submit handler.
    save.remove();
    for (const child of oldChildren) {
      if (child.querySelector('input, select, textarea') || child.matches('input, select, textarea')) content.append(child);
    }
    save.type = 'submit';
    save.textContent = '保存项目默认规则';
    const actions = element('div', '', 'formActions');
    actions.append(save);
    content.append(actions);
    form.replaceChildren(content);
    form.dataset.workflowSettings = 'ready';
    form.addEventListener('change', updateHelp);

    const nav = element('nav', '', 'stageSettings');
    nav.id = 'workflowSettingsNav';
    nav.setAttribute('aria-label', '阶段规则跳转');
    for (const section of sections) {
      const button = element('button', section.title, 'ghost');
      button.type = 'button';
      button.setAttribute('aria-controls', section.id);
      button.onclick = () => localJump(section.id);
      nav.append(button);
    }
    form.before(nav);
    updateHelp();
  }

  function sync() {
    buildForm();
    const form = byId('rules');
    if (!form) return;
    const settings = {...currentProject().settings, ...projectRuleDrafts.get(projectId)};
    // applyDefaults assigns .value; checkboxes also need their actual checked state.
    const value = settings.remove_links;
    form.elements.remove_links.checked = value === true || value === 'true' || value === 1;
    const target = form.elements.archive_library_id;
    const selected = settings.archive_library_id == null ? '' : String(settings.archive_library_id);
    if (selected && !Array.from(target.options).some(option => option.value === selected)) {
      target.add(new Option('已保存的资料库 #' + selected + '（打开设置后读取名称）', selected));
    }
    target.value = selected;
    updateHelp();
  }

  let archiveRequest = 0;
  async function refreshArchiveTargets() {
    const form = byId('rules');
    if (!form) return;
    const requestedProject = projectId;
    const requestNumber = ++archiveRequest;
    const target = form.elements.archive_library_id;
    let status = byId('workflowArchiveStatus');
    if (!status) {
      status = help('');
      status.id = 'workflowArchiveStatus';
      status.setAttribute('role', 'status');
      byId('archiveConfig').append(status);
      target.setAttribute('aria-describedby', target.getAttribute('aria-describedby') + ' ' + status.id);
    }
    status.textContent = '正在读取当前项目的线上资料库…';
    const initialValue = target.value;
    try {
      const result = await api('/api/projects/' + requestedProject + '/online-libraries');
      if (requestedProject !== projectId || requestNumber !== archiveRequest) return;
      const libraries = Array.isArray(result) ? result : result.connections;
      if (!Array.isArray(libraries)) throw Error('资料库列表格式无效');
      const configured = (projectRuleDrafts.get(projectId) || currentProject().settings)?.archive_library_id;
      const selected = target.value !== initialValue ? target.value : configured == null ? target.value : String(configured);
      target.replaceChildren(new Option('不归档到线上资料库', ''));
      for (const library of libraries) {
        if (!['notion', 'feishu'].includes(library.provider) || !Number.isInteger(library.id)) continue;
        target.add(new Option((library.provider === 'notion' ? 'Notion' : '飞书') + ' · ' + library.name, String(library.id)));
      }
      const found = Array.from(target.options).some(option => option.value === selected);
      if (selected && !found) target.add(new Option('已保存的资料库 #' + selected + '（连接不可用，请重新选择）', selected));
      target.value = selected;
      status.textContent = selected && !found ? '已保存的归档连接当前不可用，请检查连接或选择其他资料库。'
        : target.options.length === 1 ? '当前项目尚未连接线上资料库。可先在「连接线上资料库」添加 Notion 或飞书。'
        : '归档目标只用于审核后手动保存稿件；连接是否可写需在实际归档时确认。';
    } catch (error) {
      if (requestedProject !== projectId || requestNumber !== archiveRequest) return;
      status.textContent = '未能读取归档目标，请重新打开设置重试。' + error.message;
    }
  }

  function mountRun(run, container) {
    if (!container || !run?.payload) return null;
    container.querySelector(':scope > [data-workflow-summary]')?.remove();
    const config = run.payload.config || {};
    const section = sections[Math.max(0, Math.min(4, Number(run.stage || 1) - 1))];
    const summary = element('section', '', 'settingsSummary');
    summary.dataset.workflowSummary = '';
    summary.append(element('h3', '本批次' + section.title), help('本批次使用创建时保存的配置。' + scope + '。'));
    const values = element('dl');
    for (const [name, caption, type, , options] of section.fields) {
      let value = config[name];
      if (value === undefined || value === null) value = '未记录（沿用创建时默认值）';
      else if (type === 'checkbox') value = value === true || value === 'true' || value === 1 ? '是' : '否';
      else if (type === 'select') value = options.find(option => option[0] === value)?.[1] || value;
      else if (value === '') value = '未填写';
      values.append(element('dt', caption), element('dd', String(value)));
    }
    const details = element('details');
    details.append(element('summary', '查看本批次配置'), values);
    const configure = element('button', '配置本阶段', 'secondary');
    configure.type = 'button';
    configure.setAttribute('aria-controls', section.id);
    configure.onclick = async () => {
      configure.disabled = true;
      try {
        await openConfigSection(section.id);
      } catch (error) {
        let message = summary.querySelector('[role="alert"]');
        if (!message) {
          message = help('');
          message.setAttribute('role', 'alert');
          summary.append(message);
        }
        message.textContent = '无法打开配置，请重试。' + error.message;
      } finally {
        configure.disabled = false;
      }
    };
    summary.append(details, configure);
    container.append(summary);
    return summary;
  }

  const rows = value => value == null ? [] : Array.isArray(value) ? value : [value];
  const text = value => typeof value === 'string' ? value : value && typeof value === 'object'
    ? String(value.text ?? value.quote ?? value.summary ?? value.title ?? '') : String(value ?? '');

  function renderResearch(run, container) {
    if (!container) return null;
    container.querySelector(':scope > [data-workflow-research]')?.remove();
    const research = run?.payload?.research;
    if (!research || typeof research !== 'object') return null;
    const panel = element('section', '', 'stageCard');
    panel.dataset.workflowResearch = '';
    panel.append(element('h2', '研究结果与原始依据'), help('材料表述尚需核实；AI 解析和画面观察不是作者原话。未发现冲突不代表不存在冲突。'));
    const ledger = research.ledger || research.evidence || {};
    const evidence = Array.isArray(ledger) ? ledger.map((entry, index) => [String(entry?.id ?? index + 1), entry]) : Object.entries(ledger);
    const lookup = new Map(evidence);
    const insights = run.payload.insights || [];
    function appendRefs(parent, value) {
      const refs = rows(value);
      if (!refs.length) return;
      const refsBox = element('details');
      refsBox.append(element('summary', '引用依据 · ' + refs.length + ' 条'));
      for (const ref of refs) {
        const id = typeof ref === 'object' && ref !== null ? String(ref.id ?? ref.ref ?? '') : String(ref);
        const source = lookup.get(id) || (typeof ref === 'object' ? ref : null);
        refsBox.append(element('p', '引用：' + id));
        if (source) {
          refsBox.append(element('p', source.label || '提取内容（请核对原文）', 'configHelp'), element('blockquote', text(source)));
          if (source.material_id != null) refsBox.append(help('素材 #' + source.material_id));
        } else refsBox.append(help('当前结果未附此引用的原文，请回到素材核对。'));
      }
      parent.append(refsBox);
    }
    for (const [caption, value] of [
      ['研究摘要', research.summary], ['素材发现', research.findings ?? research.material_findings],
      ['相互冲突的表述', research.conflicts], ['信息缺口', research.gaps]
    ]) {
      const group = element('section');
      group.append(element('h3', caption));
      const entries = rows(value);
      if (!entries.length) group.append(help('当前结果未提供此项。'));
      for (const entry of entries) {
        const insight = insights.find(item => item.id === entry?.material_id);
        const kind = entry?.source_kind || insight?.source_kind;
        const isAI = research.engine === 'model' || kind === 'analysis' || kind === 'ai';
        const label = isAI ? 'AI 分析（非作者原话）' : kind === 'quote' ? '作者原文摘录（材料表述）' : '材料表述整理（请核对原文）';
        const item = element('div');
        item.append(help(label), element(kind === 'quote' && !isAI ? 'blockquote' : 'p', text(entry)));
        appendRefs(item, entry?.refs ?? entry?.evidence_ids);
        group.append(item);
      }
      panel.append(group);
    }
    const originals = element('details');
    originals.append(element('summary', '查看证据原文 · ' + evidence.length + ' 条'));
    for (const [id, entry] of evidence) {
      originals.append(element('h4', id + (entry?.material_id != null ? ' · 素材 #' + entry.material_id : '')),
        help(entry?.label || '提取内容（请核对原文）'), element('blockquote', text(entry)));
    }
    if (!evidence.length) originals.append(help('当前结果未附证据原文，请回到素材核对引用。'));
    panel.append(originals);
    for (const limitation of rows(research.limitations)) panel.append(help(text(limitation)));
    container.append(panel);
    return panel;
  }

  function renderDraftDiff(run, container, compareVersion) {
    if (!container) return null;
    container.querySelector(':scope > [data-workflow-diff]')?.remove();
    container.querySelector(':scope > [data-review-history]')?.remove();
    const drafts = run?.payload?.drafts;
    if (!Array.isArray(drafts) || !drafts.length) return null;
    const panel = element('details', '', 'draftDiff');
    panel.dataset.workflowDiff = '';
    const reviews = run.payload.reviews || [];
    const lastReview = [...reviews].reverse().find(review => review.version >= 1 && review.version < drafts.length);
    const base = compareVersion || lastReview?.version || drafts.length - 1;
    panel.open = !!compareVersion || (drafts.length > 1 && !reviews.some(review => review.version === drafts.length));
    panel.append(element('summary', drafts.length < 2 ? '版本对比 · 保存新版本后可查看差异' : '版本对比 · 版本 ' + base + ' 与最新版'));
    if (drafts.length < 2) {
      panel.append(help('当前仅有一个已保存版本。编辑并保存新版本后，可在这里查看改动。'));
    } else {
      const previous = String(drafts[base - 1]);
      const latest = String(drafts.at(-1));
      const before = previous.split(/\r?\n/);
      const after = latest.split(/\r?\n/);
      let prefix = 0;
      let suffix = 0;
      while (prefix < before.length && prefix < after.length && before[prefix] === after[prefix]) prefix++;
      while (suffix < before.length - prefix && suffix < after.length - prefix && before[before.length - 1 - suffix] === after[after.length - 1 - suffix]) suffix++;
      panel.append(help('比较已保存版本 ' + base + ' 与版本 ' + drafts.length + '；不包含尚未保存的编辑。共同开头和结尾标为相同，中间改动按行显示。'));
      if (lastReview) panel.append(help('上次审核版本 ' + lastReview.version + ' · ' + lastReview.decision + (lastReview.note ? ' · ' + lastReview.note : '')));
      if (lastReview && lastReview.version !== drafts.length - 1) {
        const alternate = base === lastReview.version ? drafts.length - 1 : lastReview.version;
        const toggle = element('button', '改为对照版本 ' + alternate, 'secondary');
        toggle.type = 'button';
        toggle.onclick = () => renderDraftDiff(run, container, alternate)?.querySelector('button')?.focus();
        panel.append(toggle);
      }
      const diff = element('pre');
      function addLines(lines, label, className, tag) {
        for (const line of lines) {
          const row = element(tag, label + '：' + line + '\n', className);
          row.dataset.change = className;
          diff.append(row);
        }
      }
      addLines(before.slice(0, prefix), '相同', 'same', 'span');
      addLines(before.slice(prefix, before.length - suffix), '删除', 'removed', 'del');
      addLines(after.slice(prefix, after.length - suffix), '新增', 'added', 'ins');
      addLines(before.slice(before.length - suffix), '相同', 'same', 'span');
      if (previous === latest) panel.append(help('两版内容相同。'));
      panel.append(diff);
      for (const [label, value] of [['对照版本 ' + base, previous], ['最新版 · 版本 ' + drafts.length, latest]]) {
        const full = element('details');
        full.append(element('summary', label + '全文'), element('pre', value));
        panel.append(full);
      }
    }
    container.append(panel);
    if (reviews.length) {
      const history = element('details', '', 'workFold');
      history.dataset.reviewHistory = '';
      history.append(element('summary', `审核记录（${reviews.length} 条）`));
      for (const review of [...reviews].reverse()) {
        const entry = element('section');
        entry.append(element('strong', `版本 ${review.version} · ${review.decision}`));
        if (review.at) entry.append(help(review.at + ' UTC'));
        if (review.note) entry.append(element('p', review.note));
        history.append(entry);
      }
      container.append(history);
    }
    return panel;
  }

  window.workflowSettings = {sync, mountRun, renderResearch, renderDraftDiff, refreshArchiveTargets};
  buildForm();
})();
