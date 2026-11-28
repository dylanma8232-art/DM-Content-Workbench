/* DM Content Workbench · Modern Interaction & Operation-Flow Enhancements
   Non-intrusive layer: never replaces existing IDs, forms, or API handlers. */
(() => {
  const byId = id => document.getElementById(id);

  // 1. Contextual Content-Flow Strip (采集 -> 素材 -> 选题 -> 成稿 -> 发布)
  const flowSteps = [
    { navId: 'channelNav', label: '采集素材', hint: '渠道采集 / 本地导入' },
    { navId: 'materialsNav', label: '素材库', hint: '清洗与原始依据' },
    { navId: 'topicsNav', label: '选题库', hint: '策划与选题筛选' },
    { navId: 'draftsNav', label: '成稿库', hint: '编辑与人工审核' },
    { navId: 'publishingNav', label: '发布台', hint: '多平台发布准备' },
  ];

  const main = document.querySelector('main.stageMain');
  let flowStrip = null;

  function mountFlowStrip() {
    if (!main || byId('dmFlowStrip')) return;
    flowStrip = document.createElement('nav');
    flowStrip.id = 'dmFlowStrip';
    flowStrip.className = 'dmFlowStrip';
    flowStrip.setAttribute('aria-label', '内容生产链路快捷切换');
    flowStrip.hidden = true;

    flowSteps.forEach((step, idx) => {
      if (idx > 0) {
        const sep = document.createElement('span');
        sep.className = 'flowSep';
        sep.setAttribute('aria-hidden', 'true');
        flowStrip.append(sep);
      }
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'flowStep';
      btn.dataset.targetNav = step.navId;
      btn.title = step.hint;
      btn.textContent = step.label;
      btn.addEventListener('click', () => byId(step.navId)?.click());
      flowStrip.append(btn);
    });

    const notice = byId('notice');
    if (notice && notice.parentNode === main) {
      notice.insertAdjacentElement('afterend', flowStrip);
    } else {
      main.prepend(flowStrip);
    }
  }

  function syncFlowStrip() {
    if (!flowStrip) return;
    const activeNav = document.querySelector('#workspaceNavigation button[aria-current="page"]');
    const activeId = activeNav?.id || '';
    const isPipelineView = flowSteps.some(s => s.navId === activeId) || activeId === 'workNav';
    const setupVisible = Boolean(byId('setup') && !byId('setup').hidden);
    const detailVisible = Boolean(byId('detail') && !byId('detail').hidden);

    const nextHidden = !(isPipelineView || setupVisible || detailVisible);
    if (flowStrip.hidden !== nextHidden) {
      flowStrip.hidden = nextHidden;
    }
    for (const btn of flowStrip.querySelectorAll('.flowStep')) {
      const target = btn.dataset.targetNav;
      const isCurrent = target === activeId || (setupVisible && target === 'channelNav');
      const prev = btn.getAttribute('aria-current') === 'step';
      if (isCurrent && !prev) btn.setAttribute('aria-current', 'step');
      else if (!isCurrent && prev) btn.removeAttribute('aria-current');
    }
  }

  // 2. Native <dialog> Command Palette (⌘K / Ctrl+K)
  const commands = [
    { id: 'cmd-overview', label: '账号运营总览', hint: '查看粉丝、阅读、互动与平台表现', icon: 'overview', run: () => byId('home')?.click() },
    { id: 'cmd-workflow', label: '内容工作流', hint: '查看五阶段流水线与每日批次进度', icon: 'workflow', run: () => byId('workNav')?.click() },
    { id: 'cmd-import', label: '导入写作素材', hint: '上传文稿、表格、PDF、音视频或粘贴文字', icon: 'import', run: () => byId('heroImportBtn')?.click() },
    { id: 'cmd-collect', label: '采集管理', hint: '平台账号/话题跟踪、Notion/飞书、RSS 与 API', icon: 'collect', run: () => byId('channelNav')?.click() },
    { id: 'cmd-materials', label: '打开素材库', hint: '浏览原始材料、OCR 与画面解析依据', icon: 'materials', run: () => byId('materialsNav')?.click() },
    { id: 'cmd-topics', label: '打开选题库', hint: '筛选与推进内容选题', icon: 'topic', run: () => byId('topicsNav')?.click() },
    { id: 'cmd-drafts', label: '打开成稿库', hint: '编辑、对比版本与审核成稿', icon: 'draft', run: () => byId('draftsNav')?.click() },
    { id: 'cmd-publish', label: '打开发布台', hint: '整理附件与目标账号，准备多平台发布', icon: 'publish', run: () => byId('publishingNav')?.click() },
    { id: 'cmd-settings', label: '配置管理', hint: '模型、ASR/OCR、工作流规则与本机存储', icon: 'settings', run: () => byId('projectSettings')?.click() },
    { id: 'cmd-new-project', label: '新建独立工作项目', hint: '按栏目或专题隔离素材、稿件与日报', icon: 'plus', run: () => byId('quickNewProject')?.click() },
    { id: 'cmd-report', label: '填写账号日报', hint: '账号总览 · 录入粉丝、阅读与互动数据', icon: 'edit', run: handle(async () => {
      const targetProject = projectId;
      const reading = home();
      const targetView = navigationVersion;
      await reading;
      if (projectId === targetProject && navigationVersion === targetView) editReport();
    }) },
    { id: 'cmd-theme', label: '切换深色 / 浅色外观', hint: '在暖石白工作台与黑曜石深色模式间切换', icon: 'settings', run: () => toggleTheme() },
  ];

  let dialog = null;
  let searchInput = null;
  let listBox = null;
  let activeIdx = 0;
  let filtered = commands;

  function renderPaletteList(query = '') {
    if (!listBox) return;
    const q = query.trim().toLowerCase();
    filtered = q
      ? commands.filter(c => (c.label + ' ' + c.hint).toLowerCase().includes(q))
      : commands;
    activeIdx = 0;
    listBox.replaceChildren();
    searchInput.removeAttribute('aria-activedescendant');
    if (!filtered.length) {
      const empty = document.createElement('div');
      empty.className = 'cmdEmpty';
      empty.textContent = '未找到匹配的操作入口';
      listBox.append(empty);
      return;
    }
    filtered.forEach((cmd, idx) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.id = cmd.id;
      btn.tabIndex = -1;
      btn.className = 'cmdItem';
      btn.setAttribute('role', 'option');
      btn.setAttribute('aria-selected', idx === activeIdx ? 'true' : 'false');

      const left = document.createElement('span');
      left.className = 'cmdItemTitle';
      if (window.DMIcons?.icon) left.append(window.DMIcons.icon(cmd.icon));
      const text = document.createElement('span');
      text.textContent = cmd.label;
      left.append(text);

      const right = document.createElement('span');
      right.className = 'cmdItemHint';
      right.textContent = cmd.hint;

      btn.append(left, right);
      btn.addEventListener('mouseenter', () => setActiveIdx(idx));
      btn.addEventListener('click', () => executeCommand(cmd));
      listBox.append(btn);
    });
    searchInput.setAttribute('aria-activedescendant', filtered[activeIdx].id);
  }

  function setActiveIdx(idx) {
    if (!listBox || !filtered.length) return;
    activeIdx = (idx + filtered.length) % filtered.length;
    searchInput.setAttribute('aria-activedescendant', filtered[activeIdx].id);
    const items = listBox.querySelectorAll('.cmdItem');
    items.forEach((el, i) => {
      el.setAttribute('aria-selected', i === activeIdx ? 'true' : 'false');
      if (i === activeIdx) el.scrollIntoView({ block: 'nearest' });
    });
  }

  function executeCommand(cmd) {
    if (!cmd) return;
    dialog?.close();
    cmd.run();
  }

  function openPalette() {
    if (!dialog) mountPalette();
    if (!dialog || dialog.open) return;
    searchInput.value = '';
    renderPaletteList('');
    dialog.showModal();
    searchInput.focus();
  }

  function mountPalette() {
    if (byId('dmCommandPalette')) return;
    dialog = document.createElement('dialog');
    dialog.id = 'dmCommandPalette';
    dialog.setAttribute('aria-label', '快速跳转与工作台命令');

    const head = document.createElement('div');
    head.className = 'cmdHead';

    const avatar = document.createElement('img');
    avatar.src = '/brand/chestnut-avatar.webp';
    avatar.alt = '';
    avatar.width = 28;
    avatar.height = 28;

    searchInput = document.createElement('input');
    searchInput.type = 'search';
    searchInput.setAttribute('role', 'combobox');
    searchInput.setAttribute('aria-expanded', 'true');
    searchInput.setAttribute('aria-controls', 'dmCommandResults');
    searchInput.setAttribute('aria-autocomplete', 'list');
    searchInput.setAttribute('aria-label', '搜索栏目或操作');
    searchInput.placeholder = '栗子快搜：跳转栏目、导入素材、新建项目…';
    searchInput.autocomplete = 'off';
    searchInput.addEventListener('input', () => renderPaletteList(searchInput.value));
    searchInput.addEventListener('keydown', e => {
      if (e.isComposing || e.keyCode === 229) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        dialog.close();
        return;
      }
      if (e.key === 'ArrowDown') { e.preventDefault(); setActiveIdx(activeIdx + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setActiveIdx(activeIdx - 1); }
      else if (e.key === 'Enter' && filtered[activeIdx]) { e.preventDefault(); executeCommand(filtered[activeIdx]); }
    });

    const escBtn = document.createElement('button');
    escBtn.type = 'button';
    escBtn.className = 'cmdEsc';
    escBtn.textContent = 'ESC';
    escBtn.setAttribute('aria-label', '关闭快速跳转');
    escBtn.addEventListener('click', () => dialog.close());

    head.append(avatar, searchInput, escBtn);

    listBox = document.createElement('div');
    listBox.className = 'cmdList';
    listBox.id = 'dmCommandResults';
    listBox.setAttribute('aria-label', '匹配的栏目与操作');
    listBox.setAttribute('role', 'listbox');

    const foot = document.createElement('div');
    foot.className = 'cmdFoot';
    foot.innerHTML = '<span>↑↓ 选择 · Enter 跳转</span><span>DM 内容工作台 · 栗子 IP</span>';

    dialog.append(head, listBox, foot);
    dialog.addEventListener('click', e => {
      if (e.target === dialog) dialog.close();
    });
    document.body.append(dialog);
  }

  function mountSidebarTrigger() {
    const cluster = document.querySelector('.brandCluster');
    if (!cluster || byId('dmCmdTrigger')) return;
    const isMac = /mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.id = 'dmCmdTrigger';
    btn.className = 'cmdTriggerBtn';
    btn.setAttribute('aria-label', '打开快速跳转面板');
    btn.innerHTML = `<span class="cmdLeft"><svg class="uiIcon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg><span>快速跳转</span></span><kbd>${isMac ? '⌘K' : 'Ctrl K'}</kbd>`;
    btn.addEventListener('click', openPalette);
    cluster.append(btn);
  }

  // Global keyboard shortcut ⌘K / Ctrl+K
  document.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      if (dialog?.open) dialog.close();
      else openPalette();
    }
  });

  // 3. Dark Studio Mode Toggle (UX-03)
  function applyTheme(theme) {
    if (theme === 'dark') document.documentElement.dataset.dmTheme = 'dark';
    else delete document.documentElement.dataset.dmTheme;
    const btn = byId('dmThemeToggle');
    if (btn) btn.textContent = theme === 'dark' ? '浅色外观' : '深色外观';
  }

  function toggleTheme() {
    const next = document.documentElement.dataset.dmTheme === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    try { localStorage.setItem('dm.workbench.theme', next); } catch {}
  }

  function mountThemeToggle() {
    try {
      const saved = localStorage.getItem('dm.workbench.theme');
      if (saved === 'dark') applyTheme('dark');
    } catch {}
    const lights = document.querySelector('.topRight .lights');
    if (!lights || byId('dmThemeToggle')) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.id = 'dmThemeToggle';
    btn.className = 'light';
    btn.title = '切换浅色或深色工作台外观';
    btn.textContent = document.documentElement.dataset.dmTheme === 'dark' ? '浅色外观' : '深色外观';
    btn.addEventListener('click', toggleTheme);
    lights.append(btn);
  }

  // 4. Collection Live Data-Flow Pipeline Bar (UX-04)
  function decorateTaskPipelines() {
    const list = byId('taskList');
    if (!list) return;
    for (const row of list.querySelectorAll('article.sourceItem')) {
      if (row.querySelector('.dmCollectFlowBar')) continue;
      const statusEl = row.querySelector('.sourceStatus');
      if (!statusEl) continue;
      const text = statusEl.textContent || '';
      const isReading = text.includes('正在提取与解析');
      const isBrowser = text.includes('需要平台登录或浏览器继续');
      const isDone = text.includes('本轮已完成');
      const isFailed = text.includes('采集失败');

      const bar = document.createElement('div');
      bar.className = 'dmCollectFlowBar';
      bar.setAttribute('aria-hidden', 'true');
      if (isReading) bar.dataset.active = 'true';

      const steps = [
        ['1. 列表探针', isDone ? 'done' : isReading ? 'active' : isBrowser ? 'warn' : ''],
        ['2. 详情与展开', isDone ? 'done' : isBrowser ? 'warn' : isReading ? 'active' : ''],
        ['3. 字幕 / 语音转写', isDone ? 'done' : isReading ? 'active' : ''],
        ['4. 画面 OCR 与入库', isDone ? 'done' : isFailed ? 'warn' : '']
      ];
      steps.forEach(([label, state], idx) => {
        if (idx > 0) {
          const wire = document.createElement('span');
          wire.className = 'cfWire';
          bar.append(wire);
        }
        const node = document.createElement('span');
        node.className = 'cfNode' + (state ? ' ' + state : '');
        node.textContent = label;
        bar.append(node);
      });
      statusEl.insertAdjacentElement('afterend', bar);
    }
  }

  // 5. Floating Basket Dock Sync & Escape Key Dismissal for Inline Modals (UX-07, UX-09)
  function syncBasketDock() {
    const review = document.querySelector('#library .lib-review');
    const form = document.querySelector('#library .lib-collection');
    if (form && review) {
      form.dataset.hasItems = String(!review.hidden);
    }
  }

  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !dialog?.open) {
      const reportForm = byId('reportForm');
      if (reportForm && !reportForm.hidden && !byId('overview')?.hidden) {
        byId('closeReport')?.click();
        return;
      }
      const platformSetup = byId('platformSetup');
      if (platformSetup && !platformSetup.hidden && !byId('overview')?.hidden) {
        byId('closePlatformSetup')?.click();
      }
    }
  });

  // Boot & observe route/view changes cleanly
  mountSidebarTrigger();
  mountThemeToggle();
  mountFlowStrip();
  syncFlowStrip();
  decorateTaskPipelines();

  const taskList = byId('taskList');
  if (taskList) {
    new MutationObserver(() => decorateTaskPipelines()).observe(taskList, { childList: true });
  }
  const libRoot = byId('library');
  if (libRoot) {
    new MutationObserver(() => syncBasketDock()).observe(libRoot, { childList: true, subtree: true, attributes: true, attributeFilter: ['hidden'] });
  }

  const nav = byId('workspaceNavigation');
  if (nav) {
    new MutationObserver(() => syncFlowStrip()).observe(nav, { attributes: true, subtree: true, attributeFilter: ['aria-current'] });
  }
  if (main) {
    const watchedIds = new Set(['setup', 'detail', 'workflow', 'channels', 'library', 'publishing', 'overview', 'settings', 'welcome']);
    new MutationObserver(records => {
      if (records.some(r => r.target instanceof Element && watchedIds.has(r.target.id))) {
        syncFlowStrip();
      }
    }).observe(main, { attributes: true, subtree: true, attributeFilter: ['hidden'] });
  }
})();
