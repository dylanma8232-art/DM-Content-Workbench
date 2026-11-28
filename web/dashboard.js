const byId = id => document.getElementById(id);
const form = byId('importForm');
const fields = form.elements;
const stages = ['1 · 采集数据', '2 · 清洗数据', '3 · 分析数据', '4 · 确认选题', '5 · 生产成稿'];
const sourceKindNames = {rss: 'RSS / Atom', web: '公开网页', api: 'JSON API', notion: 'Notion 库'};

const sourceKindSpecs = {
  rss: {
    label: '订阅源 HTTPS 地址',
    placeholder: 'https://example.com/feed.xml',
    guide: '填写公开可访问的 HTTPS RSS 或 Atom 订阅链接，系统会自动解析文章标题、全文/摘要与发布链接，并按内容哈希去重。'
  },
  web: {
    label: '目标网页 HTTPS 地址',
    placeholder: 'https://example.com/articles/post-1',
    guide: '填写公开 HTTPS 文章或公告网页地址，自动剥离脚本、导航栏与页脚，提取干净正文入库；支持定时监测页面内容更新。'
  },
  api: {
    label: 'JSON API 接口 HTTPS 地址',
    placeholder: 'https://api.example.com/v1/articles',
    guide: '填写返回 JSON 记录的 HTTPS 接口。支持嵌套数组路径（如 data.items），最多连续读取 10 页。需要 Bearer 令牌时，先到「配置 → 授权与接口凭据」保存密钥，再在这里填写凭据名称。'
  },
  notion: {
    label: '已授权的 Notion 数据库 ID 或链接',
    placeholder: '粘贴 Notion Database 链接或 32 位 ID',
    guide: '可直接使用「线上资料库」里的 Notion 连接。这里是旧版定时来源入口，授权可在「配置 → 授权与接口凭据」保存，不需要修改配置文件。'
  }
};

const ALL_FILE_ACCEPT = '.csv,.json,.txt,.md,.pdf,.mp3,.wav,.m4a,.ogg,.flac,.mp4,.mov,.mkv,.webm';

let active = null;
let filename = '';
let extractedFrom = null;
let projectId = 1;
let editingSource = null;
const projectSourceDrafts = new Map();
let sourceChanged = false;
let projectRows = [];
const projectRuleDrafts = new Map();
let pendingRuleEdits = false;
let overviewData = null;
let overviewRequest = 0;
let setupComplete = false;
let route = null;
let navigationVersion = 0;
let historyRequest = 0;
const panelNames = {overview:'账号总览', workflow:'内容进度', channels:'采集管理', setup:'手动导入', publishing:'发布台', settings:'配置', welcome:'项目初始化'};
const libraryNames = {materials:'素材库', topics:'选题库', drafts:'成稿库'};

function recordRoute(panel, options = {}, replace = false) {
  const next = {project: projectId, panel, ...options};
  if (JSON.stringify(route) === JSON.stringify(next)) return;
  ++navigationVersion;
  const method = replace || !route || JSON.stringify(history.state?.workspace) === JSON.stringify(next) ? 'replaceState' : 'pushState';
  const url = new URL(location.href);
  url.searchParams.set('project', projectId);
  url.searchParams.set('view', panel === 'library' ? options.kind : panel);
  for (const key of ['section', 'run', 'filter', 'shelf']) {
    if (options[key] != null) url.searchParams.set(key, options[key]);
    else url.searchParams.delete(key);
  }
  history[method]({...history.state, workspace: next}, '', url);
  route = next;
}

async function restoreRoute(saved) {
  switch (saved?.panel) {
    case 'library': return window.openLibrary(saved.kind, saved.options);
    case 'workflow': return openWorkflow(saved.filter, saved.shelf);
    case 'channels': return openChannels(saved.section);
    case 'settings': return renderSettings(saved.section);
    case 'publishing': return window.openPublishing();
    case 'setup': return importDraft?.project === projectId ? view('setup') : openImportView();
    case 'welcome': return view('welcome');
    case 'detail': return openRun(saved.run, saved.origin)();
    default: return home();
  }
}

function switchProject(id) {
  if (id !== projectId) {
    if (sourceChanged && window.confirm('保留「' + currentProject().name + '」未保存的来源配置？切回此项目后可继续编辑。取消将放弃这些修改。')) {
      projectSourceDrafts.set(projectId, {id: editingSource, fields: Object.fromEntries(new FormData(byId('source')))});
    }
    if (pendingRuleEdits) {
      if (!window.confirm('保留「' + currentProject().name + '」未保存的工作流规则？切回此项目后可继续编辑。取消将放弃这些修改。')) projectRuleDrafts.delete(projectId);
    }
    pendingRuleEdits = false;
    if (importDraft?.project === projectId && importDraft.baseline) {
      const snapshot = captureImportForm();
      if (JSON.stringify(snapshot) !== importDraft.baseline || byId('file').files.length) {
        if (window.confirm('保留本项目的导入草稿吗？确定后可在本次会话切回继续；取消将放弃草稿。尚未读取的文件需重新选择。')) {
          projectImportDrafts.set(projectId, snapshot);
        } else projectImportDrafts.delete(projectId);
      }
    }
    resetImportDraft(null);
  }
  projectId = id;
  byId('project').value = String(id);
  window.DMSelect?.refresh(byId('project'));
  rememberProject();
  resetSourceEditor();
  const sourceDraft = projectSourceDrafts.get(id);
  if (sourceDraft) {
    editingSource = sourceDraft.id;
    selectSourceKind(sourceDraft.fields.kind);
    for (const [key, value] of Object.entries(sourceDraft.fields)) byId('source').elements[key].value = value;
    if (editingSource != null) {
      byId('saveSource').textContent = '保存来源修改';
      byId('cancelSourceEdit').hidden = false;
    }
    projectSourceDrafts.delete(id);
    sourceChanged = true;
  }
  applyDefaults();
}

window.addEventListener('popstate', async event => {
  const saved = event.state?.workspace;
  if (!projectRows.some(row => row.id === saved?.project)) return;
  ++navigationVersion;
  if (saved.project !== projectId) switchProject(saved.project);
  try { await Promise.all([restoreRoute(saved), historyList()]); }
  catch (error) { notify(error.message); }
});
const stageNames = ['待清洗', '待分析', '待选题', '待写稿', '已成稿'];
let connectionCache = {
  model: null,
  notion: false,
  transcription: false,
  ffmpeg: false,
  ocr_enabled: false,
  ocr_tools: false,
  storage: 'local'
};

const nextAction = ['执行清洗', '分析素材', '确认选题', '生产内容', '查看稿件'];
const panels = ['welcome', 'overview', 'workflow', 'channels', 'setup', 'settings', 'detail', 'library', 'publishing'];

function element(tag, text = '', className = '') {
  const node = document.createElement(tag);
  if (text !== '') node.textContent = text;
  if (className) node.className = className;
  return node;
}

function notify(message, tone = 'error') {
  const box = byId('notice');
  box.dataset.tone = tone;
  box.textContent = message || '';
}

function request(path, method = 'GET', body) {
  return api('/api/runs' + path, method, body);
}

async function api(path, method = 'GET', body) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: {'Content-Type': 'application/json'},
      body: body === undefined ? undefined : JSON.stringify(body)
    });
  } catch {
    throw Error('连接工作台失败，请检查工作台是否仍在运行，并刷新确认操作结果。');
  }
  const result = await response.json().catch(() => {
    throw Error(`工作台返回了无法读取的响应（HTTP ${response.status}），请刷新确认操作结果。`);
  });
  if (!response.ok) {
    if (typeof result?.detail === 'string') throw Error(result.detail);
    throw Error(response.status === 422 ? '输入无效，请检查必填字段、格式和数量限制。' : `工作台请求失败（HTTP ${response.status}），请刷新确认操作结果。`);
  }
  return result;
}

function handle(action) {
  return async event => {
    event?.preventDefault();
    const control = event?.submitter || event?.currentTarget;
    if (control && !control.closest('#workspaceNavigation')) control.disabled = true;
    notify('', 'info');
    try {
      await action(event);
    } catch (error) {
      notify(error.message, 'error');
    } finally {
      if (control) control.disabled = false;
    }
  };
}

function currentProject() {
  return projectRows.find(row => row.id === projectId) || {id: projectId, name: '当前项目', settings: {}};
}

byId('mobileNavToggle').onclick=()=>{
  const expanded=byId('mobileNavToggle').getAttribute('aria-expanded')!=='true';
  byId('mobileNavToggle').setAttribute('aria-expanded',String(expanded));
  byId('mobileNavToggle').textContent=expanded?'收起栏目':'全部栏目';
  byId('mobileNavToggle').closest('.topBar').classList.toggle('navExpanded',expanded);
};
byId('workspaceNavigation').addEventListener('keydown', event => {
  const toggle = byId('mobileNavToggle');
  if (event.key === 'Escape' && toggle.getAttribute('aria-expanded') === 'true') {
    toggle.click();
    toggle.focus();
  }
});
function view(id, options = {}) {
  ++navigationVersion;
  recordRoute(id, options);
  if (id === 'welcome') prepareWelcome().catch(error => notify(error.message));
  const toggle = byId('mobileNavToggle');
  if (toggle.offsetWidth && byId('workspaceNavigation').contains(document.activeElement)) {
    queueMicrotask(() => {
      if (route.panel !== id) return;
      const root = id === 'channels' || id === 'setup' ? byId('collectionHeader') : byId(id);
      const heading = root.querySelector('h1, h2') || root;
      heading.tabIndex = -1;
      heading.focus();
    });
  }
  byId('mobileNavToggle').setAttribute('aria-expanded','false');
  byId('mobileNavToggle').textContent='全部栏目';
  byId('mobileNavToggle').closest('.topBar').classList.remove('navExpanded');
  if (id !== 'channels') window.collectionTasks?.close();
  for (const panel of panels) byId(panel).hidden = panel !== id;
  const inCollection = id === 'channels' || id === 'setup';
  byId('collectionHeader').hidden = !inCollection;
  byId('channelMode').setAttribute('aria-pressed', String(id === 'channels'));
  byId('sourceImportBtn').setAttribute('aria-pressed', String(id === 'setup'));
  if (id !== 'detail') {
    active = null;
    for (const row of byId('history').querySelectorAll('[aria-current="true"]')) {
      row.setAttribute('aria-current', 'false');
    }
  }
  for (const button of byId('home').closest('nav').querySelectorAll('[aria-current]')) {
    button.removeAttribute('aria-current');
  }
  for (const [button, panel] of [['home', 'overview'], ['workNav', 'workflow'], ['channelNav', 'channels'], ['publishingNav', 'publishing'], ['projectSettings', 'settings']]) {
    const current = id === 'detail' ? options.origin?.panel : id;
    if (current === panel || (button === 'channelNav' && current === 'setup')) byId(button).setAttribute('aria-current', 'page');
    else byId(button).removeAttribute('aria-current');
  }
  if (id === 'detail' && options.origin?.panel === 'library') byId(options.origin.kind + 'Nav')?.setAttribute('aria-current', 'page');
}

function setMediaMode(acceptFilter, defaultFormat) {
  const cards = document.querySelectorAll('#localMediaModes .mediaModeCard');
  cards.forEach(card => {
    card.classList.toggle('active', card.dataset.accept === acceptFilter);
  });
  byId('file').accept = acceptFilter || ALL_FILE_ACCEPT;
  if (defaultFormat) {
    fields.format.value = defaultFormat;
    mapping();
  }
}

let importDraft;
const projectImportDrafts = new Map();

function captureImportForm() {
  const values = {};
  for (const control of form.elements) {
    if (control.name && control.type !== 'file') values[control.name] = control.value;
  }
  return {values, filename, extractedFrom, accept: byId('file').accept,
    mode: document.querySelector('#localMediaModes .active')?.dataset.accept};
}

function resetImportDraft(project = projectId) {
  for (const control of importDraft?.pending || []) control.disabled = false;
  importDraft = {project, pending: new Set()};
}

function handleImport(action) {
  return async event => {
    event.preventDefault();
    const draft = importDraft;
    const control = event.submitter || event.currentTarget;
    const current = () => draft === importDraft && draft?.project === projectId;
    if (!current() || byId('setup').hidden || draft.pending.has(control)) return;
    if (draft.pending.has(byId('file'))) {
      notify('正在读取文件，请等待正文填入后再操作。', 'info');
      return;
    }
    draft.pending.add(control);
    control.disabled = true;
    notify('', 'info');
    try {
      await action(current);
    } catch (error) {
      if (current() && !byId('setup').hidden) notify(error.message, 'error');
    } finally {
      draft.pending.delete(control);
      if (draft === importDraft) control.disabled = false;
    }
  };
}

function openImportView(options = {}) {
  resetImportDraft();
  form.reset();
  filename = '';
  extractedFrom = null;
  applyImportDefaults();
  byId('previewResult').textContent = '';
  setMediaMode(options.accept || '.csv,.json,.txt,.md', options.format || 'auto');
  if (!options.accept) byId('file').accept = ALL_FILE_ACCEPT;
  importDraft.baseline = JSON.stringify(captureImportForm());
  const saved = projectImportDrafts.get(projectId);
  if (saved) {
    setMediaMode(saved.mode);
    byId('file').accept = saved.accept;
    for (const [name, value] of Object.entries(saved.values)) fields[name].value = value;
    window.DMSelect?.refresh(fields.format);
    filename = saved.filename;
    extractedFrom = saved.extractedFrom;
    projectImportDrafts.delete(projectId);
  }
  mapping();
  active = null;
  view('setup');
  notify('', 'info');
}
window.openImportView = openImportView;

document.querySelectorAll('#localMediaModes .mediaModeCard').forEach(card => {
  card.onclick = () => {
    setMediaMode(card.dataset.accept);
  };
});

async function historyList() {
  const history = byId('history');
  const countNode = byId('historyCount');
  const requestedProject = projectId;
  const token = ++historyRequest;
  const runs = await request('?project_id=' + requestedProject);
  if (requestedProject !== projectId || token !== historyRequest) return;
  history.replaceChildren();
  if (countNode) countNode.textContent = runs.length ? runs.length + ' 项' : '0 项';
  const recentLimit = 10;
  for (const row of runs.slice(0, recentLimit)) {
    const link = element('button', '');
    link.type = 'button';
    const label = element('span', row.name);
    const created = element('time', batchDate(row.created).toLocaleString('zh-CN', {hour12:false}));
    created.dateTime = row.created.replace(' ', 'T') + 'Z';
    label.append(' · ', created);
    link.append(label, element('span', row.stage + '/5', 'histStage'));
    link.setAttribute('aria-current', String(active?.id === row.id));
    link.onclick = handle(async () => {
      history.closest('details').open = false;
      await openRun(row.id)();
    });
    history.append(link);
  }
  if (runs.length > recentLimit) {
    const all = element('button', '查看全部 ' + runs.length + ' 个批次');
    all.type = 'button';
    all.onclick = handle(() => {
      history.closest('details').open = false;
      return openWorkflow('0');
    });
    history.append(all);
  }
}

function makeReviewBadge(statusText) {
  const tone = statusText.includes('通过') ? 'ok' : statusText.includes('退回') ? 'err' : 'warn';
  return element('span', statusText, 'badge ' + tone);
}

// Keep unsaved editor text when the user follows a source or opens another workspace.
const unsavedDrafts = new Map();
const pendingVariants = new Map();

async function show(run, selectedCandidate, origin = route?.panel === 'detail' ? route.origin : route) {
  if (run.project_id !== projectId) return;
  view('detail', {run:run.id, origin});
  const version = navigationVersion;
  const showUpdate = updated => version === navigationVersion && show(updated);
  active = run;
  const data = run.payload;
  const path = '/' + run.id;
  const replayWithPreview = async () => {
    const projects = await api('/api/projects');
    if (version !== navigationVersion) return;
    const rules = projects.find(project => project.id === run.project_id).settings;
    const changes = [];
    for (const [key, value] of Object.entries(rules)) {
      if (data.config[key] === value) continue;
      const field = byId('rules').elements.namedItem(key);
      const label = field?.labels?.[0]?.firstChild?.textContent.trim() || key;
      changes.push(label + '：' + (data.config[key] ?? '未记录') + ' → ' + (value ?? '未设置'));
    }
    if (data.config.manual_decisions) changes.push('本批人工清洗决定将在新批次按规则重新判断。');
    if (window.confirm(['从 #' + run.id + ' 创建「' + run.name + ' · 重算」，从待清洗阶段开始。',
      ...(changes.length ? changes : ['项目规则与原批一致。'])].join('\n'))) {
      await showUpdate(await request(path + '/replay', 'POST', rules));
    }
  };

  byId('runName').textContent = run.name;
  byId('runInfo').textContent = '已完成 ' + run.stage + ' / 5 个阶段 · ' + data.materials.length + ' 条源记录 · 来源：' + (data.config.filename || run.name);
  byId('raw').textContent = JSON.stringify({配置: data.config, 素材: data.materials}, null, 2);

  const quickLinks = byId('detailQuickLinks');
  quickLinks.replaceChildren();
  const fromList = origin?.panel === 'workflow' && origin.filter != null;
  const originName = origin?.panel === 'library' ? libraryNames[origin.kind] : panelNames[origin?.panel];
  const backHome = element('button', fromList ? '返回阶段列表' : '返回' + (originName || '工作台首页'), 'ghost');
  backHome.type = 'button';
  backHome.onclick = handle(() => restoreRoute(origin));
  const viewBatch = element('button', '查看本批素材', 'secondary');
  viewBatch.type = 'button';
  viewBatch.onclick = handle(() => window.openLibrary('materials', {run_id: run.id}));
  quickLinks.append(viewBatch, backHome);
  renderRunRelations(run);
  if (run.stage >= 3) {
    const viewTopics = element('button', '在选题库核对', 'ghost');
    viewTopics.type = 'button';
    viewTopics.onclick = handle(() => window.openLibrary('topics', {run_id: run.id}));
    quickLinks.append(viewTopics);
  }
  if (run.stage === 5) {
    const viewDraft = element('button', '在成稿库查看', 'ghost');
    viewDraft.type = 'button';
    viewDraft.onclick = handle(() => window.openLibrary('drafts', {run_id: run.id, key: 'draft'}));
    quickLinks.append(viewDraft);
  }

  window.workflowSettings?.mountRun(run, quickLinks);
  byId('stages').replaceChildren(...stages.map((title, index) => {
    const item = element('li', title);
    if (index < run.stage) item.className = 'done';
    else if (index === run.stage) item.className = 'current';
    item.append(element('span', index < run.stage ? '已完成' : index === run.stage ? '当前操作步' : '待推进'));
    return item;
  }));

  const task = byId('task');
  task.replaceChildren();

  /* Primary Stage Action Card */
  const primaryCard = element('section', '', 'stageCard primaryStage');
  const previous = ['','已导入 ' + data.materials.length + ' 条素材','已完成清洗',
    '已提取 ' + (data.insights?.length || 0) + ' 条素材证据','已确认选题','已保存版本 ' + (data.drafts?.length || 0)];
  primaryCard.append(element('p', [stageNames[run.stage - 1], previous[run.stage], '下一步 · ' + nextAction[run.stage - 1]].join(' ｜ '), 'cardHint'));
  if (run.stage < 5) {
    const stageTitles = [
      '',
      '当前阶段 · 执行数据清洗与去重',
      '当前阶段 · 提取可追溯原句证据',
      '当前阶段 · 核对候选并确认正式选题',
      '当前阶段 · 配置读者、文体与引擎生成初稿'
    ];
    const head = element('div', '', 'stageCardHead');
    head.append(element('h2', data.topic ? data.topic.title : stageTitles[run.stage]));
    primaryCard.append(head);

    if (run.stage === 1) {
      primaryCard.append(element(
        'p',
        '本批共载入 ' + data.materials.length + ' 条原始记录。点击下方按钮将按「最短保留 ' + (data.config.min_length || 1) + ' 字、' + ({off:'不去重', similar:'相似正文去重', exact:'全文精确去重'}[data.config.dedup_mode] || '全文精确去重') + '」执行清洗。',
        'cardHint'
      ));
    } else if (run.stage === 2 && data.cleaned) {
      const keptCount = data.cleaned.filter(row => row.reason === '保留').length;
      primaryCard.append(element('p', data.config.analysis_engine === 'model'
        ? '本批使用内容理解模型：' + (connectionCache.model || '尚未配置，请先连接模型服务') + '。检测失败后可保留本批输入，在此重试。'
        : '本批按证据规则分析，可离线提取原句。', 'fieldHelp'));
      primaryCard.append(element(
        'p',
        '清洗已完成：保留 ' + keptCount + ' 条有效素材，剔除 ' + (data.cleaned.length - keptCount) + ' 条过短或重复记录。下一步提取核心证据句' + (data.config.focus ? '（优先匹配关键词：' + data.config.focus + '）' : '') + '。',
        'cardHint'
      ));
    }

    window.stageTools?.mountCleaning(run, primaryCard);
    let candidate, title;
    if (run.stage === 3) {
      if (!data.insights.length || (data.recommendations && !data.recommendations.length)) {
        primaryCard.append(element('p', data.insights.length ? '当前素材尚未达到选题配置中的来源条件。可补充资料，或调整来源门槛后新建重算批次。' : '当前批次没有通过清洗的有效素材，无法提取证据。你可以调低最短字数规则后重算，或导入新材料。', 'cardHint'));
        const actions = element('div', '', 'formActions');
        const again = element('button', '按当前项目规则重算', 'primary');
        again.type = 'button';
        again.onclick = handle(replayWithPreview);
        const editRuleBtn = element('button', '去修改项目清洗规则', 'ghost');
        editRuleBtn.type = 'button';
        editRuleBtn.onclick = handle(() => openConfigSection('rulesCard'));
        actions.append(again, editRuleBtn);
        primaryCard.append(actions);
        task.append(primaryCard);
        await historyList();
        return;
      }
      candidate = element('select', '');
      const choices = data.recommendations || data.insights.map(row => ({
        key: String(row.id),
        title: data.materials[row.id - 1].title,
        ids: [row.id],
        reason: '有可追溯原句'
      }));
      for (const row of choices) candidate.add(new Option(row.title, row.key));
      if (choices.some(row => row.key === selectedCandidate)) candidate.value = selectedCandidate;

      title = element('input', '');
      title.placeholder = '留空沿用候选标题，也可直接改写正式选题名';
      title.maxLength = 200;

      const pair = element('div', '', 'pair');
      for (const [caption, input] of [['选择候选选题线索', candidate], ['自定义正式标题（选填）', title]]) {
        const label = element('label', caption);
        label.append(input);
        pair.append(label);
      }
      primaryCard.append(pair);

      const rationale = element('div', '', 'rationaleBox');
      const compare = element('select');
      const comparison = element('div', '', 'pair');
      if (choices.length > 1) {
        const label = element('label', '对照候选');
        label.append(compare);
        primaryCard.append(label, comparison);
      }
      const explain = () => {
        const choice = choices.find(row => row.key === candidate.value) || choices[0];
        if (choice) rationale.textContent = '推荐依据：' + choice.reason + ' · 关联源素材 ' + choice.ids.map(id => '#' + id).join('、');
        if (!choice || choices.length < 2) return;
        const otherKey = compare.value;
        const others = choices.filter(row => row !== choice);
        compare.replaceChildren(...others.map(row => new Option(row.title, row.key)));
        const other = others.find(row => row.key === otherKey) || others[0];
        compare.value = other.key;
        comparison.replaceChildren();
        for (const item of [choice, other]) {
          const section = element('section');
          section.append(element('h4',item.title),element('p',item.reason));
          for (const id of item.ids) {
            const source = data.materials.find(row => row.id === id);
            section.append(element('p','#' + id + ' ' + (source?.title || '') + ' · ' + (source?.provenance?.source || data.config.filename || run.name)));
          }
          comparison.append(section);
        }
        const shared = choice.ids.filter(id => other.ids.includes(id));
        rationale.append(element('p','共同引用素材：' + (shared.map(id => '#' + id).join('、') || '无')));
      };
      candidate.onchange = compare.onchange = explain;
      explain();
      primaryCard.append(rationale);
    }

    const advanceBtn = element('button', ['', '执行清洗', '提取证据摘录', '确认选题', '生成草稿'][run.stage], 'primary');
    advanceBtn.type = 'button';
    let production = {};
    if (run.stage === 4) {
      const cited = new Set(data.topic.ids || [data.topic.id]);
      primaryCard.append(element('p','写稿依据 · ' + data.materials.filter(row => cited.has(row.id)).map(row => '#' + row.id + ' ' + row.title).join('、'), 'cardHint'));
      if (data.topic?.reason) {
        primaryCard.append(element('p', '已立项选题依据：' + data.topic.reason, 'cardHint'));
      }
      const engine = element('select', '');
      engine.add(new Option('本地证据摘录拼装（零外部凭据、严格引用原句）', 'local'));
      engine.add(new Option(
        connectionCache.model ? '在线 AI 大模型生成（当前：' + connectionCache.model + '）' : '在线 AI 大模型生成（尚未配置 DM_MODEL_*）',
        'model'
      ));
      if (connectionCache.model) engine.value = 'model';

      const audience = element('input', '');
      audience.value = data.config.audience || '';
      audience.placeholder = '例如：科技从业者、产品团队、社区读者';
      const style = element('input', '');
      style.value = data.config.style || '文章';

      const styleChipBar = element('div', '', 'presetSection');
      const chipRow = element('div', '', 'presetRow');
      chipRow.append(element('span', '点选成稿文体：', 'presetLabel'));
      for (const preset of ['文章', '公众号深度长文', '行业洞察周报', '小红书图文笔记', '播客逐字口播稿', '研究简报']) {
        const chip = element('span', preset, 'presetChip');
        chip.setAttribute('role', 'button');
        chip.tabIndex = 0;
        chip.onclick = () => { style.value = preset; };
        chip.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); style.value = preset; } };
        chipRow.append(chip);
      }
      styleChipBar.append(chipRow);
      primaryCard.append(styleChipBar);

      const engineLabel = element('label', '生成引擎选择');
      engineLabel.append(engine);
      primaryCard.append(engineLabel);

      const grid = element('div', '', 'pair');
      for (const [name, control] of [['目标受众画像', audience], ['稿件形式与文体', style]]) {
        const label = element('label', name);
        label.append(control);
        grid.append(label);
      }
      primaryCard.append(grid);
      const target_words = element('input'); target_words.type='number'; target_words.min='100'; target_words.max='10000'; target_words.value=data.config.target_words||1200;
      const writing_requirements = element('textarea'); writing_requirements.maxLength=4000; writing_requirements.rows=3; writing_requirements.value=data.config.writing_requirements||'';
      const outline = element('textarea'); outline.maxLength=4000; outline.rows=4; outline.value=data.config.outline||'';
      for (const [caption, input] of [['目标篇幅（字，作为写作目标）',target_words],['写作提纲（选填）',outline],['额外写作要求（选填）',writing_requirements]]) {
        const label=element('label',caption);label.append(input);primaryCard.append(label);
      }
      production = {engine, audience, style, target_words, writing_requirements, outline};
    }

    advanceBtn.onclick = handle(async () => {
      if (window.stageTools?.hasPending(run.id)) throw Error('请先保存人工筛选，再继续分析。');
      for (const input of Object.values(production)) if (!input.reportValidity()) return;
      const values=Object.fromEntries(Object.entries(production).map(([key,input])=>[key,input.type==='number'?Number(input.value):input.value]));
      const label = advanceBtn.textContent;
      advanceBtn.textContent = ['','正在清洗…','正在分析…','正在确认选题…','正在生成草稿…'][run.stage];
      advanceBtn.setAttribute('aria-busy', 'true');
      try {
        await showUpdate(await request(path + '/advance', 'POST', {...values,stage:run.stage,candidate_key:candidate?.value||null,title:title?.value||''}));
      } finally {
        advanceBtn.removeAttribute('aria-busy');
        advanceBtn.textContent = label;
      }
    });
    primaryCard.append(advanceBtn);
  } else {
    const versionReviews = (data.reviews || []).filter(item => item.version === data.drafts.length);
    const latestDecision = versionReviews.at(-1);
    const statusLabel = latestDecision ? latestDecision.decision : '待审核';
    const head = element('div', '', 'stageCardHead');
    head.append(element('h2', data.topic ? data.topic.title : run.name), makeReviewBadge('版本 ' + data.drafts.length + ' · ' + statusLabel));
    primaryCard.append(head);

    const label = element('label', '编辑 Markdown · 版本 ' + data.drafts.length);
    const editor = element('textarea', '');
    editor.rows = 12;
    const draftKey = `${run.project_id}/${run.id}/${data.drafts.length}`;
    editor.value = unsavedDrafts.get(draftKey) ?? data.drafts.at(-1);
    label.append(editor);
    primaryCard.append(label);

    const editorActions = element('div', '', 'formActions');
    const save = element('button', '保存版本', 'primary');
    save.type = 'button';
    save.onclick = handle(async () => {
      const submitted = editor.value;
      const updated = await request(path + '/draft', 'PUT', {version: data.drafts.length, content: submitted});
      const currentText = unsavedDrafts.get(draftKey) ?? editor.value;
      unsavedDrafts.delete(draftKey);
      if (currentText !== submitted) {
        unsavedDrafts.set(`${run.project_id}/${run.id}/${updated.payload.drafts.length}`, currentText);
      }
      await showUpdate(updated);
    });

    const download = element('a', '导出已保存版本', 'download');
    download.href = '/api/runs' + path + '/export?version=' + data.drafts.length;

    const variant = element('button', '用同一选题制作另一份稿件', 'secondary');
    variant.type = 'button';
    variant.onclick = handle(async () => {
      const slot = 'dm-variant-' + draftKey;
      let key = pendingVariants.get(slot);
      if (!key) {
        try { key = sessionStorage.getItem(slot); } catch {}
        key ||= Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2,'0')).join('');
        pendingVariants.set(slot, key);
        try { sessionStorage.setItem(slot, key); } catch {}
      }
      const result = await request(path + '/variant', 'POST', {request_key:key, source_version:data.drafts.length});
      pendingVariants.delete(slot);
      try { sessionStorage.removeItem(slot); } catch {}
      await showUpdate(result);
    });

    editorActions.append(save, download, variant);
    primaryCard.append(editorActions);
    if (latestDecision?.decision === '通过') {
      const publish = element('button', '准备发布已审核版本', 'secondary');
      publish.onclick = () => window.openPublishing({run_id: run.id, version: data.drafts.length, title: data.topic?.title || run.name, body: data.drafts.at(-1)});
      editorActions.append(publish);
    }
    attachDraftWebhook(primaryCard, run.id, data.drafts.length, latestDecision?.decision === '通过');

    const review = element('details', '', 'workFold');
    review.id = 'currentDraftReview';
    review.open = !latestDecision;
    review.append(element('summary', '审核稿件 · 版本 ' + data.drafts.length));
    if (!data.reviews?.length) review.append(element('p','首次审核'));
    window.workflowSettings?.renderDraftDiff(run, review);
    review.append(element('p', latestDecision
      ? '当前版本 · ' + latestDecision.decision + (latestDecision.note ? ' · ' + latestDecision.note : '')
      : '当前版本待审核'));
    const note = element('textarea', '');
    note.rows = 2;
    note.maxLength = 1000;
    note.placeholder = '填写审核意见或修订说明（选填）';
    const reviewLabel = element('label', '审核备注');
    reviewLabel.append(note);
    review.append(reviewLabel);
    const verdictBar = element('div', '', 'formActions');
    let reviewing = false;
    for (const verdict of ['通过', '退回']) {
      const btn = element('button', verdict, verdict === '通过' ? 'primary' : '');
      btn.type = 'button';
      btn.onclick = async () => {
        if (reviewing || editor.value !== data.drafts.at(-1)) return;
        reviewing = true;
        updateEditState();
        notify('', 'info');
        try {
          const updated = await request(path + '/review', 'POST', {
            version: data.drafts.length, expected_review_count: versionReviews.length,
            decision: verdict, note: note.value
          });
          await showUpdate(updated);
        } catch (error) {
          notify(error.message, 'error');
        } finally {
          reviewing = false;
          if (editor.isConnected) updateEditState();
        }
      };
      verdictBar.append(btn);
    }
    const editStatus = element('p', '', 'fieldHelp');
    editStatus.setAttribute('role', 'status');
    const updateEditState = () => {
      const changed = editor.value !== data.drafts.at(-1);
      if (changed) unsavedDrafts.set(draftKey, editor.value);
      else unsavedDrafts.delete(draftKey);
      editStatus.textContent = changed ? '正文有未保存的修改。请先保存新版本，再审核或派生稿件。' : '';
      variant.disabled = changed;
      for (const button of verdictBar.children) button.disabled = changed || reviewing;
    };
    updateEditState();
    editor.addEventListener('input', updateEditState);
    editorActions.before(editStatus);
    review.append(verdictBar);
    primaryCard.append(review);

    const versions = element('details', '', 'workFold');
    versions.append(element('summary', '查看已保存版本'));
    data.drafts.forEach((draft, index) => {
      const block = element('div', '', 'versionBlock');
      const link = element('a', '导出版本 ' + (index + 1), 'download');
      link.href = '/api/runs' + path + '/export?version=' + (index + 1);
      block.append(element('h3', '版本 ' + (index + 1)), element('pre', draft), link);
      versions.append(block);
    });
    primaryCard.append(versions);
    window.draftArchive?.attach(primaryCard, run);
  }
  task.append(primaryCard);
  window.workflowSettings?.renderResearch(run, task);

  /* Evidence & Cleaning Inspection Card */
  if (data.cleaned || data.insights) {
    const evidenceCard = element('section', '', 'stageCard');
    const head = element('div', '', 'stageCardHead');
    head.append(element('h2', '分析证据与清洗对照记录'));
    evidenceCard.append(head);

    if (data.themes?.length) {
      const themes = element('details', '', 'workFold');
      themes.open = true;
      themes.append(element('summary', '跨素材聚类主题命中'));
      for (const theme of data.themes) {
        themes.append(element('p', theme.topic + ' · 命中 ' + theme.sources.length + ' 条 · ' + theme.sources.map(id => '#' + id).join('、')));
      }
      evidenceCard.append(themes);
    }

    if (data.insights) {
      const details = element('details', '', 'workFold');
      details.open = run.stage === 3 || run.stage === 4;
      details.append(element('summary', '证据摘录 · ' + data.insights.length + ' 条'));
      for (const row of data.insights) {
        details.append(element('blockquote', '#' + row.id + (row.matched ? ' [命中关键词] · ' : ' · ') + row.quote));
      }
      evidenceCard.append(details);
    }

    if (data.cleaned) {
      const valid = data.cleaned.filter(row => row.reason === '保留').length;
      const details = element('details', '', 'workFold');
      details.open = run.stage === 2;
      details.append(element('summary', '清洗结果 · 保留 ' + valid + ' 条，剔除 ' + (data.cleaned.length - valid) + ' 条'));
      for (const row of data.cleaned) {
        details.append(element('p', '#' + row.id + ' [' + row.reason + '] · ' + row.body));
      }
      evidenceCard.append(details);
    }

    task.append(evidenceCard);
  }

  /* Run Operations, Replay & Notion Snapshot Card */
  const opsCard = element('section', '', 'stageCard');
  const opsHead = element('div', '', 'stageCardHead');
  opsHead.append(element('h2', '批次重算与 Notion 远端归档'));
  opsCard.append(opsHead);

  if (data.config.notion_database) {
    const syncWrap = element('div', '', 'syncBar');
    syncWrap.id = 'currentSync';
    const status = element('p', '');
    const syncBtns = element('div', '', 'formActions');
    syncBtns.style.marginTop = '0';
    const check = element('button', '刷新同步状态', 'ghost');
    check.type = 'button';
    const update = async () => {
      const jobs = await request(path + '/sync');
      status.textContent = 'Notion 自动版本同步 · ' + (jobs.length ? jobs.map(job => job.state + (job.error ? '（' + job.error + '）' : '')).join(' / ') : '暂无排队任务');
    };
    check.onclick = handle(update);
    const retry = element('button', '重试待处理同步');
    retry.type = 'button';
    retry.onclick = handle(async () => {
      await request(path + '/sync', 'POST', {});
      await update();
    });
    syncBtns.append(check, retry);
    syncWrap.append(status, syncBtns);
    opsCard.append(syncWrap);
    await update();
  }

  const replayBar = element('div', '', 'formActions');
  const replay = element('button', '按当前项目默认规则重新运行');
  replay.type = 'button';
  replay.onclick = handle(replayWithPreview);
  replayBar.append(replay);
  opsCard.append(replayBar);

  const delivery = element('details', '', 'workFold');
  delivery.append(element('summary', '手动投递本运行快照到指定 Notion 库'));
  const destination = element('input', '');
  destination.placeholder = '输入授权给集成的 Notion 数据库 ID 或链接';
  const destLabel = element('label', '目标 Notion 数据库 ID');
  destLabel.append(destination);
  const submit = element('button', '保存快照并回读核验', 'primary');
  submit.type = 'button';
  const report = element('p', '');
  const listReceipts = async () => {
    const rows = await request(path + '/receipts');
    report.textContent = rows.length
      ? rows.map(row => row.status === 'verified' ? '已回读核验 · Notion 页面 ' + row.page : '待对账').join('\n')
      : '当前批次尚未手动投递过 Notion 快照。';
  };
  delivery.ontoggle = handle(async () => {
    if (delivery.open) await listReceipts();
  });
  submit.onclick = handle(async () => {
    await request(path + '/notion', 'POST', {database: destination.value});
    await listReceipts();
  });
  delivery.append(destLabel, submit, report);
  opsCard.append(delivery);

  task.append(opsCard);
  await historyList();
}

function importFormat() {
  if (fields.format.value !== 'auto') return fields.format.value;
  const content = fields.content.value.trim();
  if (content.startsWith('[') && content.endsWith(']')) {
    try {
      const rows = JSON.parse(content);
      if (rows.length && rows.every(row => row && typeof row === 'object' && !Array.isArray(row))) return 'json';
    } catch { /* Bracketed prose is plain text. */ }
  }
  const header = content.split(/\r?\n/, 1)[0];
  if (header.length < content.length && header.split(',').some(column => /^(?:body|"body")$/.test(column))) return 'csv';
  return 'txt';
}

function mapping() {
  const structured = ['csv', 'json'].includes(importFormat());
  byId('mapping').hidden = !structured;
  fields.body_field.required = fields.title_field.required = structured;
}
fields.format.onchange = mapping;
fields.content.oninput = mapping;

byId('channelMode').onclick = handle(() => byId('channels').hidden && openChannels());
byId('sourceLightBtn').onclick = handle(() => openConfigSection('collectionTasks'));
byId('heroImportBtn').onclick = () => openImportView();

byId('file').onchange = handleImport(async current => {
  const file = byId('file').files[0];
  if (!file) return;
  let format = file.name.split('.').pop().toLowerCase();
  let content;
  let extracted = null;
  if (['md', 'csv', 'json', 'txt'].includes(format)) {
    if (file.size > 4000000) throw Error('请选择小于 4 MB 的文本文件');
    content = await file.text();
    if (!current()) return;
    setMediaMode('.csv,.json,.txt,.md');
    byId('file').accept = ALL_FILE_ACCEPT;
  } else {
    const limit = format === 'pdf' ? 10000000 : 25000000;
    if (file.size > limit) throw Error('文件超过 ' + limit / 1000000 + ' MB');
    setMediaMode(format === 'pdf' ? '.pdf' : '.mp3,.wav,.m4a,.ogg,.flac,.mp4,.mov,.mkv,.webm');
    byId('file').accept = ALL_FILE_ACCEPT;
    const upload = new FormData();
    upload.append('file', file);
    notify('正在解析提取正文…', 'info');
    const response = await fetch('/api/import-extract', {method: 'POST', body: upload});
    const result = await response.json();
    if (!current()) return;
    if (!response.ok) throw Error(result.detail);
    content = result.content;
    extracted = result.type;
    format = 'json';
    fields.body_field.value = 'body';
    fields.title_field.value = 'title';
    notify('正文提取完成，可预览清洗效果或直接导入。', 'ok');
  }
  if (content.length > 1000000) throw Error('单次导入最多 100 万字符');
  fields.format.value = format;
  fields.content.value = content;
  filename = file.name;
  extractedFrom = extracted;
  if (!fields.name.value) fields.name.value = filename.slice(0, 100);
  mapping();
});

byId('sample').onclick = () => {
  resetImportDraft();
  form.reset();
  extractedFrom = null;
  byId('previewResult').textContent = '';
  applyImportDefaults();
  importDraft.baseline = JSON.stringify(captureImportForm());
  fields.name.value = '社区阅读访谈（合成示例）';
  fields.format.value = 'csv';
  fields.focus.value = '开放时间';
  fields.content.value = 'title,body\n周末读书会,读者希望延长周末开放时间。\n重复访谈,读者希望延长周末开放时间。\n借阅体验,读者建议在入口展示新书清单。';
  filename = '合成示例.csv';
  mapping();
  notify('已填充合成示例数据，可点击「预览清洗结果」或「导入并打开批次」。', 'ok');
};

byId('quickSampleBtn').onclick = () => {
  openImportView();
  byId('sample').click();
};

function importValues() {
  const values = Object.fromEntries(new FormData(form));
  values.project_id = projectId;
  values.format = importFormat();
  values.extracted_from = extractedFrom;
  values.min_length = Number(values.min_length);
  values.filename = filename || '手工录入';
  return values;
}

form.onsubmit = handleImport(async current => {
  const version = navigationVersion;
  const run = await request('', 'POST', importValues());
  if (!current() || version !== navigationVersion) return;
  importDraft.baseline = null;
  await show(run);
  if (active?.id === run.id && !byId('detail').hidden) notify('已打开本次导入的批次。点击「查看本批素材」查看导入内容。', 'ok');
});

byId('previewImport').onclick = handleImport(async current => {
  if (!form.reportValidity()) return;
  const preview = await api('/api/import-preview', 'POST', importValues());
  if (!current()) return;
  byId('previewResult').textContent = [
    '共 ' + preview.总数 + ' 条 · 保留 ' + preview.保留 + ' 条 · 排除 ' + (preview.总数 - preview.保留) + ' 条',
    ...preview.前五条.map(row => '#' + row.id + '　[' + row.reason + ']　' + row.body.slice(0, 120))
  ].join('\n');
});

function applyImportDefaults() {
  const settings = currentProject().settings;
  fields.focus.value = settings?.focus ?? '';
  fields.min_length.value = settings?.min_length ?? 1;
}

function applyDefaults() {
  const settings = {...currentProject().settings, ...projectRuleDrafts.get(projectId)};
  pendingRuleEdits = projectRuleDrafts.has(projectId);
  for (const [key, value] of Object.entries(settings)) {
    const input = byId('rules').elements[key];
    if (input) { if (input.type==='checkbox') input.checked=Boolean(value); else input.value=value??''; }
  }
  window.workflowSettings?.sync();
}

function rememberProject() {
  try { sessionStorage.setItem('dm-current-project', String(projectId)); } catch { /* Storage is optional. */ }
}

async function loadProjects(restoreSelection = false) {
  projectRows = await api('/api/projects');
  if (restoreSelection) {
    let saved;
    try { saved = Number(sessionStorage.getItem('dm-current-project')); } catch { /* Use the setup project. */ }
    if (projectRows.some(row => row.id === saved)) projectId = saved;
  }
  rememberProject();
  byId('project').replaceChildren(...projectRows.map(row => new Option(row.name, row.id)));
  byId('project').value = String(projectId);
  window.DMSelect?.refresh?.(byId('project'));
  applyDefaults();
}

byId('project').onchange = handle(async () => {
  const current = route?.panel === 'detail' ? route.origin : route;
  switchProject(Number(byId('project').value));
  const next = {panel: current?.panel, kind: current?.kind, section: current?.section};
  await Promise.all([restoreRoute(next), historyList()]);
});

/* Preset Chips in #rulesCard */
document.querySelectorAll('#rulesCard .presetChip').forEach(chip => {
  const applyPreset = () => {
    const targetName = chip.dataset.fillTarget;
    const input = byId('rules').elements[targetName];
    if (input) {
      input.value = chip.dataset.fillValue;
      input.dispatchEvent(new Event('input', {bubbles: true}));
      input.focus();
    }
  };
  chip.onclick = applyPreset;
  chip.onkeydown = event => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      applyPreset();
    }
  };
});

const connectionChecks = {};

function renderEnvMatrix(connections) {
  connectionCache = connections;
  const headerBtn = byId('headerStorageBtn');
  const ocrReady = Boolean(connections.ocr_enabled && connections.ocr_tools);
  const asrReady = Boolean(connections.transcription && connections.ffmpeg);
  const configured = Boolean(connections.model && asrReady && connections.image_ocr);
  if (headerBtn) {
    headerBtn.textContent = configured ? '内容处理配置齐全' : '内容处理待完善';
    headerBtn.title = '配置状态不代表连接已验证，请在设置中检测并试用素材。';
  }

  const setupOcr = byId('setupOcrBadge');
  const setupAsr = byId('setupAsrBadge');
  if (setupOcr) {
    setupOcr.textContent = ocrReady ? '文档文字识别工具可用' : '文字提取可用 · 扫描文档待设置';
    setupOcr.className = 'badge ' + (ocrReady ? 'ok' : '');
  }
  if (setupAsr) {
    setupAsr.textContent = asrReady ? '讲话转文字参数已配置 · 待试用' : '讲话转文字待设置';
    setupAsr.className = 'badge ' + (asrReady ? '' : 'warn');
  }

  byId('connectionStatus').textContent = '内容理解：' + (connections.model ? '配置存在，连接请检测' : '待设置') + ' · 视频讲话转文字：' + (asrReady ? '参数齐全，效果待试用' : '待完善') + ' · 图片文字识别：' + (connections.image_ocr ? '本机工具可用' : '待安装工具');
  byId('storageLocation').textContent = '当前保存方式：工作台电脑上的本机数据库。';
  byId('welcomeProcessingStatus').textContent = connections.model ? '内容理解服务已配置。可先继续创建项目，再到设置页检测连接；视频转写和图片识别会分别提示是否可用。' : '尚未连接内容理解服务。你可以先整理文字素材；需要 AI 分析或图片理解时，在下方连接服务。';

  const matrix = byId('envMatrix');
  if (!matrix) return;
  const advancedOpen = matrix.querySelector('details[data-advanced]')?.open || false;
  matrix.replaceChildren();
  const advanced = element('details', '', 'operatorAdvanced');
  advanced.dataset.advanced = '';
  advanced.open = advancedOpen;
  advanced.append(element('summary', '更多能力：Notion 与扫描 PDF'));
  const advancedItems = element('div', '', 'envMatrix');
  advanced.append(advancedItems);
  const items = [
    {
      key: 'model', title: '内容理解', ready: Boolean(connections.model),
      desc: '理解素材、提炼重点并生成草稿。图片和视频画面的理解还取决于所连接服务的能力。',
      technical: '模型：' + (connections.model || '未配置'),
      vars: ['DM_MODEL_BASE_URL', 'DM_MODEL_NAME', 'DM_MODEL_KEY']
    },
    {
      key: 'transcription', title: '视频讲话转文字', ready: asrReady,
      desc: '把视频或音频里的讲话整理成文字。检测只确认参数和本机工具，请再上传一段短音频验证效果。',
      technical: 'ASR 接口：' + (connections.transcription ? '配置存在' : '未配置') + ' · FFmpeg：' + (connections.ffmpeg ? '已安装' : '未安装'),
      vars: ['DM_ASR_BASE_URL', 'DM_ASR_MODEL', 'DM_ASR_KEY', 'ffmpeg']
    },
    {
      key: 'image', title: '图片文字识别', ready: Boolean(connections.image_ocr),
      desc: connections.image_ocr ? '本机已有识别工具，可导入含文字的图片试用；识别效果以实际图片为准。' : '需要安装本机识别工具，安装后可提取图片中的文字。',
      technical: '图片 OCR 使用本机 Tesseract 或 macOS Vision；不依赖扫描 PDF 开关，也没有独立连接检测接口。',
      vars: []
    },
    {
      key: 'notion', title: 'Notion 素材与备份', ready: Boolean(connections.notion), advanced: true,
      desc: '从 Notion 读取素材或备份成稿。检测前填写已授权的数据库链接；写入权限需在首次备份时验证。',
      vars: ['DM_NOTION_TOKEN']
    },
    {
      key: 'ocr', title: '扫描 PDF 文字识别', ready: ocrReady, advanced: true,
      desc: '提取扫描文档中的文字。检测只确认本机工具和开关，识别效果请用一份扫描文档验证。',
      technical: 'Poppler/Tesseract：' + (connections.ocr_tools ? '已安装' : '未安装') + ' · OCR 开关：' + (connections.ocr_enabled ? '已开启' : '未开启'),
      vars: ['DM_OCR_ENABLED=1', 'DM_OCR_LANG', 'pdftoppm', 'tesseract']
    }
  ];

  for (const item of items) {
    const card = element('div', '', 'envCard');
    const top = element('div', '', 'sourceItemHead');
    const checked = connectionChecks[item.key];
    const status = checked?.status || (item.key === 'image'
      ? (item.ready ? '本机工具可用' : '待安装工具')
      : (item.ready ? '配置存在 · 尚未检测' : '待完善'));
    const badge = element('span', status, 'badge ' + (checked?.ok || (item.key === 'image' && item.ready) ? 'ok' : ''));
    badge.dataset.connectionStatus = item.key;
    top.append(element('strong', item.title), badge);
    card.append(top, element('p', item.desc));

    const result = element('p', checked?.summary || '', 'fieldHelp');
    result.dataset.connectionSummary = item.key;
    result.setAttribute('aria-live', 'polite');
    card.append(result);
    const technical = element('details');
    technical.append(element('summary', '技术信息'));
    if (item.technical) technical.append(element('p', item.technical));
    const varsRow = element('div', '', 'envVars');
    for (const v of item.vars) varsRow.append(element('code', v));
    if (item.vars.length) {
      technical.append(varsRow, element('p', ['model','transcription'].includes(item.key) ? '可在上方连接本地服务，或填写并保存自有服务。设置保存后立即生效。' : '此项为高级配置，由维护人员设置后重启服务。'));
    }
    const rawResult = element('p', checked?.message || '', 'fieldHelp');
    rawResult.dataset.connectionDetail = item.key;
    technical.append(rawResult);
    card.append(technical);

    if (item.key !== 'image') {
      const foot = element('div', '', 'envCardFoot');
      const checkBtn = element('button', '检测连接', 'ghost');
      checkBtn.type = 'button';
      checkBtn.onclick = () => {
        const checkForm = byId('connectionCheck');
        const advancedCheck = byId('advancedConnectionCheck');
        if (advancedCheck) advancedCheck.open = true;
        checkForm.elements.target.value = item.key;
        updateConnectionCheck();
        if (item.key === 'notion') {
          checkForm.scrollIntoView({block: 'center', behavior: 'smooth'});
          checkForm.elements.database.focus();
        } else {
          checkForm.requestSubmit();
        }
      };
      foot.append(checkBtn);
      const recover = element('button', '查看对应配置', 'ghost');
      recover.type = 'button';
      recover.dataset.recover = item.key;
      recover.hidden = checked?.ok !== false;
      recover.onclick = handle(() => openConfigSection(checkTargets[item.key][1]));
      foot.append(recover);
      card.append(foot);
    }
    (item.advanced ? advancedItems : matrix).append(card);
  }
  matrix.append(advanced);
}

function selectSourceKind(kind) {
  invalidateSourcePreview();
  const select = byId('source').elements.kind;
  select.value = kind;
  document.querySelectorAll('#sourceKindCards .kindCard').forEach(card => {
    card.classList.toggle('active', card.dataset.kind === kind);
  });
  const spec = sourceKindSpecs[kind] || sourceKindSpecs.rss;
  byId('sourceAddressLabel').textContent = spec.label;
  byId('source').elements.address.placeholder = spec.placeholder;
  byId('sourceKindGuide').textContent = spec.guide;
  byId('notionFields').hidden = !['notion', 'api'].includes(kind);
  byId('apiFields').hidden = kind !== 'api';
  byId('source').elements.title_field.value = kind === 'notion' ? 'Name' : 'title';
  byId('source').elements.body_field.value = kind === 'notion' ? 'Body' : 'body';
}

document.querySelectorAll('#sourceKindCards .kindCard').forEach(card => {
  card.onclick = () => { selectSourceKind(card.dataset.kind); sourceChanged = true; };
});
byId('source').elements.kind.onchange = event => selectSourceKind(event.target.value);

let settingsOrigin=null;
async function renderSettings(section = window.configurationCenter?.current) {
  const alreadyOpen=!byId('settings').hidden && route?.project === projectId;
  if(!alreadyOpen){
    settingsOrigin={route, panel:panels.find(id=>!byId(id).hidden)||'overview',run:active,scroll:window.scrollY,project:projectId};
    const names={overview:'账号总览',workflow:'内容工作流',channels:'采集管理',detail:'当前批次',library:'内容库',welcome:'项目初始化',setup:'手动导入',publishing:'发布台'};
    if(settingsOrigin.panel==='library')settingsOrigin.library=window.captureLibraryView();
    byId('backFromSettings').textContent='返回'+(settingsOrigin.library?.name||names[settingsOrigin.panel]||'刚才的页面');
    byId('backFromSettings').hidden=false;
  }
  view('settings', {section});
  window.configurationCenter?.open(section);
  if(!alreadyOpen)applyDefaults();
  const version = navigationVersion;
  const results = await Promise.allSettled([
    api('/api/connections').then(renderEnvMatrix), loadConnectorSettings(), window.refreshOnlineLibraries?.(),
    window.refreshServiceCredentials?.(), window.workflowSettings?.refreshArchiveTargets?.()
  ]);
  if (version !== navigationVersion) return;
  const failed = results.find(result => result.status === 'rejected');
  if (failed) notify('部分配置读取失败：'+failed.reason.message+'。重新打开配置可重试。');
  if(!alreadyOpen)window.workflowSettings?.sync();
  window.draftArchive?.refreshSettings(projectId, currentProject().settings);
  byId('connectionResult').textContent = '';
  updateConnectionCheck();
  window.configurationCenter?.showCurrent();
}

async function openChannels(section = window.sourceWorkspace?.current) {
  view('channels', {section});
  window.sourceWorkspace?.open(section);
  await Promise.all([showSources(),window.collectionTasks.open(projectId),window.refreshOnlineLibraries?.()]);
}
async function openConfigSection(sectionId, options = {}) {
  if(['sourcesCard','collectionTasks','onlineLibraries'].includes(sectionId)){
    if(byId('channels').hidden) {
      const loading = openChannels(sectionId);
      const version = navigationVersion;
      await loading;
      if (version !== navigationVersion) return;
    } else window.sourceWorkspace?.open(sectionId);
  }else {
    const loading = renderSettings(sectionId);
    const version = navigationVersion;
    await loading;
    if (version !== navigationVersion) return;
  }
  if (options.sourceKind) {
    selectSourceKind(options.sourceKind);
  }
  if (!sectionId) return;
  window.configurationCenter?.open(sectionId);
  const target = byId(sectionId)||(['reportConnector','draftConnector'].includes(sectionId)?byId('thirdParty'):null);
  if (!target) return;
  let ancestor = target;
  while (ancestor) {
    if (ancestor.tagName === 'DETAILS') ancestor.open = true;
    ancestor = ancestor.parentElement;
  }
  const sourceTarget=['sourcesCard','collectionTasks','onlineLibraries'].includes(sectionId);
  (sourceTarget?byId('collectionHeader'):target).scrollIntoView({block:'start',behavior:'instant'});
  const focusTarget = options.focusField
    ? target.querySelector(`[name="${options.focusField}"]`)
    : (target.matches('input, select, textarea, button') ? target : target.querySelector('input, select, textarea'));
  if (options.focusField) focusTarget?.focus({preventScroll: true});
}
window.openConfigSection = openConfigSection;

byId('backFromSettings').onclick=handle(async()=>{
  if(!settingsOrigin||settingsOrigin.project!==projectId){await home();return;}
  const origin=settingsOrigin;settingsOrigin=null;
  if(origin.library){await origin.library.restore();return;}
  if (origin.panel === 'detail' && origin.run) { await show(origin.run, undefined, origin.route.origin); return; }
  if (origin.panel === 'setup' && importDraft?.project === projectId) view('setup');
  else if (origin.route && origin.route.project === projectId) await restoreRoute(origin.route);
  else await home();
  if(origin.panel==='overview')await refreshSyncControls(projectId, reportRequest);
  if(origin.panel==='publishing')await window.openPublishing();
  window.scrollTo({top:origin.scroll,behavior:'instant'});
});
byId('projectSettings').onclick = handle(() => renderSettings());
byId('headerStorageBtn').onclick = handle(() => openConfigSection('connectionSettings'));
byId('storageSettings').onsubmit = handle(async () => {
  const field = byId('storageSettings').elements.directory;
  const value = field.value.trim();
  if (!value) throw Error('请先填写新文件夹的完整路径；不更换位置则无需保存。');
  const progress = byId('storageProgress');
  progress.textContent = '正在复制并校验数据库，校验通过后切换目录…';
  try {
    await api('/api/storage','PUT',{directory:value});
  } catch (error) {
    progress.textContent = error.message + '。可运行 python backup.py location 核对当前位置，再选择空目录重试。';
    return;
  }
  progress.textContent = '数据位置已确认，后续读写使用所选目录。';
  field.value = '';
  renderEnvMatrix(await api('/api/connections'));
  notify('数据目录已保存，后续读写使用新位置。','ok');
});
byId('quickNewProject').onclick = handle(() => openConfigSection('newProject', {focusField: 'name'}));

const hints = {
  model: '发送一条简短内容验证理解服务是否能响应，可能产生少量用量。尚未设置时，可在上方连接本机服务，或填写自有服务。',
  notion: '粘贴已授权的 Notion 数据库链接，检测读取权限。写入权限需在首次备份时验证。',
  transcription: '检查讲话转文字所需的参数和本机工具。通过后，请上传一段短音频验证实际转写效果。',
  ocr: '检查扫描文档识别工具是否已安装并启用。通过后，请导入一份扫描 PDF 验证识别效果。'
};

const checkTargets = {
  model: ['内容理解模型', 'processingConnection', '检查模型地址、名称及密钥'],
  transcription: ['语音转写', 'processingConnection', '检查语音服务的地址、模型及 FFmpeg'],
  ocr: ['扫描 PDF 识别', 'scanSettingsPanel', '检查 OCR 开关、Poppler 和 Tesseract'],
  notion: ['Notion 读取', 'serviceCredentials', '核对集成密钥、数据库链接及读取授权']
};

function checkSummary(target, summary) {
  return checkTargets[target][0] + ' · ' + new Date().toLocaleString() + ' · ' + summary;
}

function updateConnectionCheck() {
  const select = byId('connectionCheck').elements.target;
  const labels = {model: '内容理解', transcription: '视频讲话转文字', ocr: '扫描 PDF 文字识别', notion: 'Notion 数据库读取'};
  for (const option of select.options) {
    if (labels[option.value]) option.textContent = labels[option.value];
  }
  const target = select.value;
  byId('checkDatabase').hidden = target !== 'notion';
  byId('connectionHint').textContent = hints[target] || '';
  byId('connectionResult').textContent = connectionChecks[target]?.summary || '';
}

byId('connectionCheck').elements.target.onchange = updateConnectionCheck;
byId('connectionCheck').onsubmit = handle(async () => {
  const config = Object.fromEntries(new FormData(byId('connectionCheck')));
  if (connectionChecks[config.target]?.pending) return;
  const showCheck = (check) => {
    if (!check.pending) check.summary = checkSummary(config.target, check.summary);
    connectionChecks[config.target] = check;
    const recover = byId('envMatrix').querySelector('[data-recover="' + config.target + '"]');
    if (recover) recover.hidden = check.ok !== false;
    byId('envMatrix')?.querySelectorAll('[data-connection-status], [data-connection-summary], [data-connection-detail]').forEach(node => {
      if (node.dataset.connectionStatus === config.target) {
        node.textContent = check.status;
        node.className = 'badge ' + (check.ok ? 'ok' : '');
      }
      if (node.dataset.connectionSummary === config.target) node.textContent = check.summary;
      if (node.dataset.connectionDetail === config.target) node.textContent = check.message || '';
    });
    if (byId('connectionCheck').elements.target.value === config.target) {
      byId('connectionResult').textContent = check.summary;
    }
  };
  showCheck({pending: true, status: '正在检测', summary: '正在检测，请稍候…'});
  try {
    const result = await api('/api/connections/check', 'POST', config);
    const summaries = {
      model: '内容理解服务已响应，可以开始试用素材。图片和视频画面支持情况需另行试用。',
      notion: '该数据库可以读取。写入权限需在首次备份时验证。',
      transcription: '参数与本机工具可用；尚未验证转写服务，请上传一段短音频试用。',
      ocr: '识别工具已启用，请导入扫描 PDF 验证识别效果。'
    };
    showCheck({ok: true, status: config.target === 'model' ? '连接已验证' : config.target === 'notion' ? '读取已验证' : '工具检查通过 · 待试用', summary: summaries[config.target], message: result.message});
  } catch (error) {
    showCheck({ok: false, status: '检测未通过', summary: error.message + '。' + checkTargets[config.target][2] + '后重新检测。', message: error.message});
  }
});

byId('newProject').onsubmit = handle(async () => {
  const name = byId('newProject').elements.name.value.trim();
  const version = navigationVersion;
  const project = await api('/api/projects', 'POST', {name});
  if (version !== navigationVersion) { await loadProjects(); return; }
  projectId = project.id;
  resetSourceEditor();
  await loadProjects();

  byId('newProject').reset();
  if (version !== navigationVersion) return;
  await home();
  notify('已创建并切换到「'+name+'」。','ok');
});

byId('restorePage').onsubmit = handle(async () => {
  const version = navigationVersion;
  const form = {
    project_id: projectId,
    page_id: byId('restorePage').elements.page_id.value
  };
  const snapshot = await api('/api/notion/restore?preview=true', 'POST', form);
  if (version !== navigationVersion || projectId !== form.project_id) return;
  const source = snapshot.name + ' · ' + (snapshot.version ? '稿件版本 ' + snapshot.version : '尚未成稿');
  if (!window.confirm('从 Notion 页面 ' + snapshot.page + ' 恢复“' + source + '”到项目“' + currentProject().name + '”，并新建批次？')) return;
  const restored = await api('/api/notion/restore', 'POST', {...form, preview_id:snapshot.preview_id});
  if (version === navigationVersion) await show(restored);
});

function rememberRuleEdits() {
  const values = Object.fromEntries(new FormData(byId('rules')));
  values.remove_links = byId('rules').elements.remove_links.checked;
  projectRuleDrafts.set(projectId, values);
  pendingRuleEdits = true;
}
byId('rules').addEventListener('input', rememberRuleEdits);
byId('rules').onsubmit = handle(async () => {
  const id = projectId;
  const savedDraft = projectRuleDrafts.get(id);
  const rules = Object.fromEntries(new FormData(byId('rules')));
  for (const key of ['min_length','evidence_count','analysis_max_tokens','topic_limit','min_topic_sources','target_words','generation_max_tokens','dedup_threshold']) if (rules[key]!==undefined) rules[key]=Number(rules[key]);
  rules.remove_links = byId('rules').elements.remove_links?.checked || false;
  rules.archive_library_id = rules.archive_library_id ? Number(rules.archive_library_id) : null;
  await api('/api/projects/' + id, 'PUT', rules);
  if (projectRuleDrafts.get(id) === savedDraft) projectRuleDrafts.delete(id);
  if (id !== projectId) return;
  pendingRuleEdits = false;
  await loadProjects();
  notify('项目默认规则已保存，后续导入与采集将自动生效', 'ok');
});

function sourceValues() {
  const values = Object.fromEntries(new FormData(byId('source')));
  return {...values, minutes: Number(values.minutes), project_id: projectId};
}

let sourcePreviewVersion = 0;
function invalidateSourcePreview() {
  ++sourcePreviewVersion;
  byId('sourcePreview').textContent = '';
  byId('source').querySelector('[aria-invalid="true"]')?.removeAttribute('aria-invalid');
}
for (const eventName of ['input', 'change']) {
  byId('source').addEventListener(eventName, () => { sourceChanged = true; invalidateSourcePreview(); });
}

byId('previewSource').onclick = handle(async () => {
  if (!byId('source').reportValidity()) return;
  invalidateSourcePreview();
  const revision = sourcePreviewVersion;
  const values = sourceValues();
  const current = () => revision === sourcePreviewVersion && values.project_id === projectId;
  byId('sourcePreview').textContent = '正在试采…';
  let preview;
  try {
    preview = await api('/api/sources/preview', 'POST', values);
  } catch (error) {
    if (!current()) return;
    byId('sourcePreview').textContent = '试采失败：' + error.message;
    const fields = byId('source').elements;
    const fieldErrors = [
      [/下一页/, 'next_path'], [/记录路径/, 'records_path'], [/ID 字段|稳定 ID/, 'id_field'],
      [/正文/, 'body_field'], [/授权变量|凭据名称/, 'token_alias'], [/地址|HTTPS/, 'address']
    ];
    const match = fieldErrors.find(([pattern]) => pattern.test(error.message));
    const field = match && fields[match[1]];
    if (field && field.offsetWidth) {
      field.setAttribute('aria-invalid', 'true');
      field.focus();
    }
    return;
  }
  if (!current()) return;
  byId('sourcePreview').textContent = [
    '试采成功 · 共读取 ' + preview.count + ' 条记录（预览前 ' + preview.sample.length + ' 条）：',
    ...preview.sample.map(row => '• ' + row.title + ' — ' + row.body.slice(0, 120))
  ].join('\n');
});

function resetSourceEditor() {
  editingSource = null;
  sourceChanged = false;
  byId('source').reset();
  byId('saveSource').textContent='保存并添加采集源';
  byId('cancelSourceEdit').hidden=true;
  byId('sourcePreview').textContent='';
  selectSourceKind('rss');
}
byId('sourceImportBtn').onclick = () => {
  if (importDraft?.project === projectId) view('setup');
  else openImportView();
};
byId('cancelSourceEdit').onclick=resetSourceEditor;
byId('source').onsubmit = handle(async () => {
  const id=projectId;
  await api(editingSource ? '/api/sources/'+editingSource : '/api/sources', editingSource ? 'PUT' : 'POST', sourceValues());
  if (projectId!==id) return;
  resetSourceEditor();
  byId('sourcePreview').textContent = '';
  await showSources();
  notify('在线采集源已保存，可点击「立即刷新」拉取首批数据', 'ok');
});

async function showSources() {
  const requestedProject = projectId;
  const rows = await api('/api/sources?project_id=' + requestedProject);
  if (requestedProject !== projectId) return;
  const list = byId('sources');
  list.replaceChildren();
  if (!rows.length) {
    list.append(element('p', '当前项目尚未添加在线采集源。在下方选择 RSS、公开网页、JSON API 或 Notion 数据库即可试采接入。', 'cardHint'));
    return;
  }
  for (const row of rows) {
    const section = element('section', '', 'sourceItem');
    section.dataset.sourceId = row.id;
    const head = element('div', '', 'sourceItemHead');
    const titleWrap = element('div', '', 'sourceItemTitle');
    titleWrap.append(
      element('h3', row.name),
      ...(row.platform ? [element('span', row.platform, 'badge')] : []),
      element('span', sourceKindNames[row.kind] || row.kind, 'badge accent')
    );
    head.append(
      titleWrap,
      element('span', row.minutes > 0 ? '每 ' + row.minutes + ' 分钟自动抓取' : '仅手动刷新', 'badge ' + (row.minutes > 0 ? 'ok' : ''))
    );
    section.append(head);
    if (row.address) section.append(element('span', row.address, 'sourceAddress'));
    section.append(element('p', '最近状态：' + row.status, 'sourceStatus'));

    const controls = element('div', '', 'sourceControls');
    const edit = element('button','修改来源连接','ghost'); edit.type='button';
    edit.onclick=()=>{
      sourceChanged=false;
      editingSource=row.id;selectSourceKind(row.kind);
      for (const key of ['name','kind','address','minutes','platform','title_field','body_field','records_path','id_field','next_path','token_alias']) {
        const field=byId('source').elements[key];if(field)field.value=row[key]??'';
      }
      byId('saveSource').textContent='保存来源修改';byId('cancelSourceEdit').hidden=false;
      byId('sourcePreview').textContent='正在修改「'+row.name+'」。历史素材保留，新的配置用于后续采集。';
      byId('source').scrollIntoView({block:'start'});byId('source').elements.name.focus({preventScroll:true});
    };
    const remove=element('button','移除来源','ghost');remove.type='button';
    remove.onclick=handle(async()=>{
      if(!window.confirm('移除「'+row.name+'」的采集配置？已经导入的素材和稿件会保留。'))return;
      await api('/api/sources/'+row.id+'?project_id='+requestedProject,'DELETE');
      if(projectId===requestedProject){if(editingSource===row.id)resetSourceEditor();await showSources();}
    });
    const refreshBtn = element('button', '立即刷新抓取', 'secondary');
    refreshBtn.type = 'button';
    refreshBtn.onclick = handle(async () => {
      const version = navigationVersion;
      try {
        const result = await api('/api/sources/' + row.id + '/refresh', 'POST', {});
        if (version !== navigationVersion || projectId !== requestedProject || byId('channels').hidden || byId('sourcesCard').hidden) return;
        if (result.run) await show(result.run);
        else notify('刷新完成：' + result.status, 'ok');
      } finally {
        if (projectId === requestedProject) await Promise.all([showSources(), historyList()]);
      }
    });

    controls.append(refreshBtn,edit,remove);
    section.append(controls);
    list.append(section);
  }
}

function line(container, title, note, action) {
  const row = element(action ? 'button' : 'div', '', 'overviewRow');
  if (action) row.type = 'button';
  row.append(element('strong', title), element('small', note));
  if (action) row.onclick = handle(action);
  container.append(row);
  return row;
}

const listPageSize = 20;
function listPager(container, offset, total, change) {
  if (total <= listPageSize) return;
  const pager = element('nav', '', 'formActions');
  pager.setAttribute('aria-label', '列表分页');
  for (const [label, next] of [['上一页', offset-listPageSize], ['下一页', offset+listPageSize]]) {
    const control = element('button', label, 'secondary');
    control.type = 'button';
    control.disabled = next < 0 || next >= total;
    control.onclick = () => change(next);
    if (next > offset) pager.append(element('small', (offset+1) + '–' + Math.min(offset+listPageSize,total) + ' / ' + total));
    pager.append(control);
  }
  container.append(pager);
}

const workflowPages = new WeakMap();
function workflowList(container, filter, rows, renderRow) {
  const scope = projectId + '/' + filter;
  const saved = workflowPages.get(container);
  const focus = document.activeElement;
  const focused = saved?.scope === scope && container.contains(focus) && focus.closest('[data-work-key]')?.dataset.workKey;
  const index = focused ? rows.findIndex(row => String(row.key ?? row.id) === focused) : -1;
  const first = index >= 0 ? Math.floor(index/listPageSize)*listPageSize : saved?.scope === scope ? saved.offset : 0;
  const offset = Math.min(first, Math.max(0, Math.floor((rows.length-1)/listPageSize)*listPageSize));
  workflowPages.set(container, {scope, offset});
  const opened = new Set([...container.querySelectorAll('details[open]')].map(detail => detail.dataset.workKey));
  container.replaceChildren();
  for (const row of rows.slice(offset,offset+listPageSize)) {
    const control = renderRow(row);
    control.dataset.workKey = row.key ?? row.id;
    if (opened.has(control.dataset.workKey)) control.open = true;
    if (control.dataset.workKey === focused) (control.querySelector(focus.tagName) || control).focus({preventScroll:true});
  }
  listPager(container, offset, rows.length, next => {
    workflowPages.set(container,{scope,offset:next});
    workflowList(container,filter,rows,renderRow);
    (container.firstChild?.querySelector('summary') || container.firstChild)?.focus();
  });
}

function openRun(id, origin = undefined) {
  return async () => {
    const version = ++navigationVersion;
    const run = await request('/' + id);
    if (version === navigationVersion) await show(run, undefined, origin);
  };
}

function drawRuns() {
  const target = byId('runRows');
  const filter = Number(byId('runFilter').value);
  const rows = overviewData.runs.filter(row => !filter || row.stage === filter);
  workflowList(target, filter, rows, row =>
    line(target, row.name, [batchCalendarDay(row.created), stageNames[row.stage - 1], relationNote(row)].filter(Boolean).join(' · '), async () => {
      await openRun(row.id, {...route, filter:String(filter)})();
    }));
  if (!rows.length) target.append(element('p', '暂无符合当前筛选阶段的批次。'));
}

function selectShelf(id) {
  for (const button of document.querySelectorAll('[data-shelf]')) {
    const selected = button.dataset.shelf === id;
    button.setAttribute('aria-pressed', String(selected));
    byId(button.dataset.shelf).hidden = !selected;
  }
}
for (const button of document.querySelectorAll('[data-shelf]')) {
  button.onclick = () => {
    selectShelf(button.dataset.shelf);
    recordRoute('workflow', {...route, shelf:button.dataset.shelf}, true);
  };
}
selectShelf('recentMaterials');

for (const entry of document.querySelectorAll('[data-media]')) {
  const mode = {text: 0, pdf: 1, audio: 2}[entry.dataset.media];
  entry.onclick = () => openImportView({accept: document.querySelectorAll('.mediaModeCard')[mode].dataset.accept});
}

function relationNote(row) {
  const notes = [];
  if (row.sources.length) notes.push('源批次：' + row.sources.map(source => source.name).join('、'));
  if (row.collections.length) notes.push('关联专题：' + row.collections.map(topic => topic.name + '（' + stageNames[topic.stage - 1] + '）').join('、'));
  return notes.join(' · ');
}

function usedInDraft(row) {
  return row.collections.some(topic => topic.stage === 5);
}

function localCalendarDay(date) {
  return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
}

function batchDate(timestamp) {
  // SQLite CURRENT_TIMESTAMP is UTC, without an explicit offset.
  const iso = timestamp.replace(' ', 'T');
  return new Date(/Z$|[+-]\d\d:\d\d$/i.test(iso) ? iso : iso + 'Z');
}
function batchCalendarDay(timestamp) {
  return localCalendarDay(batchDate(timestamp));
}

function drawProduction(data) {
  const complete = data.by_stage[4];
  const used = data.runs.filter(row => row.stage < 5 && usedInDraft(row)).length;
  byId('progressCaption').textContent = (data.runs.length - complete - used) + ' 个待推进批次 · ' + complete + ' 个已成稿批次' + (used ? ' · ' + used + ' 个源批次已用于成稿专题' : '');
  const mix = byId('stageMix');
  mix.replaceChildren();
  for (const [index, count] of data.by_stage.entries()) {
    if (!count) continue;
    const segment = element('i');
    segment.style.flexGrow = count;
    segment.style.background = 'var(--stage' + index + ')';
    mix.append(segment);
  }
  const days = new Map();
  for (let offset = 6; offset >= 0; offset--) {
    const day = new Date();
    day.setDate(day.getDate() - offset);
    days.set(localCalendarDay(day), 0);
  }
  for (const row of data.runs) {
    const day = batchCalendarDay(row.created);
    if (days.has(day)) days.set(day, days.get(day) + 1);
  }
  const max = Math.max(1, ...days.values());
  const trend = byId('weekTrend');
  trend.replaceChildren();
  for (const [day, count] of days) {
    const column = element('div');
    column.title = day + ' · ' + count + ' 个批次';
    const bar = element('i');
    bar.style.height = Math.max(2, count / max * 40) + 'px';
    column.append(element('small', String(count)), bar, element('span', day.slice(5).replace('-', '/')));
    trend.append(column);
  }
}

function indexRunRelations(runs) {
  const byId = new Map(runs.map(row => [row.id, row]));
  for (const row of runs) { row.collections = []; row.copies = []; }
  for (const row of runs) {
    row.parentRun = byId.get(row.parent);
    if (row.parentRun) row.parentRun.copies.push(row);
    row.sources = (row.source_ids ?? []).map(id => byId.get(id)).filter(Boolean);
    for (const source of row.sources) source.collections.push(row);
  }
  return byId;
}

async function renderRunRelations(run) {
  const relations = byId('workflowRelations');
  relations.replaceChildren();
  relations.hidden = true;
  let summary;
  try {
    const data = await api('/api/projects/' + run.project_id + '/overview');
    if (active !== run) return;
    summary = indexRunRelations(data.runs).get(run.id);
  } catch {
    if (active === run) {
      relations.textContent = '批次关系暂时无法读取，重新打开批次可重试。';
      relations.hidden = false;
    }
    return;
  }
  if (!summary) return;
  for (const [label, rows] of [['源批次', summary.sources], ['关联专题', summary.collections],
    ['派生自', summary.parentRun ? [summary.parentRun] : []], ['派生批次', summary.copies]]) {
    for (const row of rows) {
      const link = element('button', label + ' · ' + row.name, 'ghost');
      link.type = 'button';
      link.onclick = handle(openRun(row.id));
      relations.append(link);
    }
  }
  if (summary.parentRun) relations.prepend(element('p', (summary.derivation === 'variant' ? '选题变体' : summary.derivation === 'replay' ? '重算副本' : '派生批次') + (run.payload.source_version ? ' · 来源稿版本 ' + run.payload.source_version : '')));
  relations.hidden = !relations.childElementCount;
}

function drawOverview(data) {
  data.project = projectId;
  data.byId = indexRunRelations(data.runs);
  overviewData = data;
  const hasWork = data.runs.length > 0;
  byId('firstProject').hidden = hasWork;
  byId('workflow').querySelector('.focusBento').hidden = !hasWork;
  byId('overviewNote').textContent = currentProject().name;
  drawDailyRuns();

  byId('totalRunsVal').textContent = data.runs.length + ' 个批次（含 ' + data.runs.filter(row => row.format === 'collection').length + ' 个合并专题）';
  drawProduction(data);
  const pipeline = byId('pipeline');
  pipeline.replaceChildren();
  for (const [index, count] of data.by_stage.entries()) {
    const button = element('button');
    const latest = data.runs.find(row => row.stage === index + 1);
    button.style.setProperty('--stage', 'var(--stage' + index + ')');
    button.append(element('small', stageNames[index]), element('strong', String(count)), element('span', latest ? latest.topic || latest.name : '暂无批次'));
    button.onclick = () => {
      byId('runFilter').value = String(index + 1);
      window.DMSelect?.refresh(byId('runFilter'));
      drawRuns();
      const drawer = byId('runFilter').closest('details');
      drawer.open = true;
      recordRoute('workflow', {...route, filter:byId('runFilter').value}, true);
      drawer.scrollIntoView({block: 'nearest'});
    };
    pipeline.append(button);
  }
  const focus = data.runs.find(row => row.stage === 5 ? row.review !== '通过' : !usedInDraft(row)) || data.runs.find(row => row.stage === 5);
  const spot = byId('focusSpotlight');
  const title = focus ? focus.topic || focus.name : '导入第一份素材';
  byId('coverTitle').textContent = focus ? title : currentProject().name;
  byId('coverDate').textContent = focus ? batchCalendarDay(focus.created).replaceAll('-', '.') : '项目素材册';
  const approved = focus?.stage === 5 && focus.review === '通过';
  const action = approved ? '查看已审核稿件' : focus?.stage === 5 ? '审阅稿件' : focus ? nextAction[focus.stage - 1] : '选择素材';
  const button = element('button', action);
  button.append(DMIcons.icon('arrow'));button.classList.add('withIcon');
  button.onclick = focus ? handle(openRun(focus.id)) : () => openImportView();
  spot.replaceChildren(element('h3', title), element('p', focus ? (approved ? '审核通过' : stageNames[focus.stage - 1]) + ' · ' + focus.materials + ' 条素材' : '从采访记录、PDF 或一段录音开始。'), button);

  if (focus && relationNote(focus)) spot.insertBefore(element('p', relationNote(focus)), button);
  const todo = byId('todo');
  const queue = byId('editorialQueue');
  const entries = queue.querySelector('.rowList');
  const actions = [];
  const addAction = (key,title,note,action) => actions.push({key, render:() => line(entries,title,note,action)});
  for (const row of data.runs) {
    const checkCleaning = row.stage === 2 && !row.cleaning_reviewed && row.materials > row.kept && !usedInDraft(row);
    const checkDraft = row.stage === 5 && row.review !== '通过';
    if (!checkCleaning && !checkDraft) continue;
    const action = checkCleaning ? '核对清洗 · ' + (row.materials - row.kept) + ' 条被剔除' : (row.review === '退回' ? '查看退回意见' : '审核稿件') + ' · 版本 ' + row.versions;
    addAction('run-' + row.id, row.topic || row.name, action, async () => {
      await openRun(row.id)();
      if (active?.id !== row.id || byId('detail').hidden) return;
      const fold = byId(checkCleaning ? 'currentCleaningReview' : 'currentDraftReview');
      if (!fold) return;
      fold.open = true;
      fold.querySelector('summary').focus();
      fold.scrollIntoView({block:'start'});
    });
  }
  todo.replaceChildren();
  for (const row of data.conflicts) addAction('sync-' + row.id, row.name, '处理 Notion 同步异常', async () => {
    await openRun(row.id)();
    if (active?.id === row.id) byId('currentSync')?.querySelector('button:last-child')?.focus();
  });
  for (const failure of data.failures) addAction('source-' + failure.source_id, failure.name, '检查采集来源 · ' + failure.error, async () => {
    await openConfigSection('sourcesCard');
    if (projectId === data.project && route.panel === 'channels') byId('sources').querySelector('[data-source-id="' + failure.source_id + '"] button')?.focus();
  });
  for (const item of data.collection_actions || []) {
    const platform = DMPlatforms.find(row => row.id === item.platform)?.label || item.platform;
    addAction('task-' + item.id, platform + ' · ' + item.query, item.status === 'browser' ? '查看需授权任务' : '查看采集失败任务', async () => {
      await openConfigSection('collectionTasks');
      if (projectId !== data.project || route.panel !== 'channels') return;
      await window.collectionTasks.focusTask(item.id);
    });
  }
  for (const item of data.publish_failures || []) {
    actions.push({key:'publish-' + item.id, render:() => {
      const target = element('details', '', 'workFold');
      target.append(element('summary', item.title + ' · ' + item.platform + ' · ' + item.account + ' · 处理发布失败'));
      line(target, '将目标 #' + item.id + ' 恢复为待发布', '发布任务 #' + item.post_id, async () => {
        await api('/api/projects/' + data.project + '/publishing/targets/' + item.id, 'POST', {status:'pending'});
        if (projectId === data.project && route.panel === 'workflow') await refreshOverview();
      });
      entries.append(target);
      return target;
    }});
  }
  workflowList(entries,'todo',actions,action => action.render());
  queue.hidden = !actions.length;
  queue.querySelector('summary').textContent = '工作流待办（' + actions.length + '）';
  const exceptions = data.conflicts.length + data.failures.length;
  byId('batchHistory').closest('details').querySelector('summary').textContent = exceptions ? '采集与同步 · ' + exceptions + ' 项异常' : '采集与同步记录';

  const batches = byId('batchHistory');
  batches.replaceChildren();
  for (const batch of data.batches) {
    const summary = batch.error
      ? '失败 · ' + batch.error
      : '读取 ' + batch.received + ' · 新增 ' + batch.added + ' · 更新 ' + batch.updated + ' · 重复 ' + batch.duplicates;
    line(
      batches,
      batch.name,
      batchDate(batch.created).toLocaleString('zh-CN', {hour12:false}) + ' · ' + summary,
      batch.run_id ? () => window.openLibrary('materials', {run_id: batch.run_id}) : null
    );
  }
  if (!batches.children.length) batches.append(element('p', '下次导入文件或刷新在线来源后，将在此记录完整批次明细。'));

  drawRuns();
}

const shelfDefs = [
  ['materials', 'recentMaterials', '暂无素材，可在「采集管理 → 手动导入」添加。'],
  ['topics', 'candidateShelf', '完成素材清洗与证据分析后，将在此展示推荐候选选题。'],
  ['drafts', 'outputs', '确认选题并生成初稿后，将在此展示最近修订的稿件。']
];

async function refreshOverview() {
  const token = ++overviewRequest;
  const id = projectId;
  const base = '/api/projects/' + id;
  const current = () => token === overviewRequest && id === projectId && !byId('workflow').hidden;
  await Promise.all([
    api(base + '/overview').then(data => {
      if (current()) drawOverview(data);
    }).catch(error => {
      if (current()) byId('overviewNote').textContent = '批次概要读取失败，可点击刷新。' + error.message;
    }),
    ...shelfDefs.map(async ([kind, target, emptyText]) => {
      const shelf = byId(target);
      try {
        const result = await api(base + '/library/' + kind + '?limit=3&compact=true&preview=true' + (kind === 'topics' ? '&status=' + encodeURIComponent('候选') : ''));
        if (!current()) return;
        shelf.replaceChildren();
        for (const item of result.items) {
          const note = kind === 'drafts' ? '版本 ' + item.version + ' · ' + item.status : kind === 'topics' ? item.reason : item.source + ' · ' + item.status;
          line(shelf, item.title, note, () => window.openLibrary(kind, {run_id: item.run_id, key: item.key}));
        }
        if (!result.items.length) shelf.append(element('p', emptyText));
      } catch {
        if (current()) shelf.textContent = '此库摘要暂时无法读取，可点击刷新重试。';
      }
    })
  ]);
}

document.addEventListener('visibilitychange', () => {
  if (!document.hidden && route?.panel === 'workflow') refreshOverview();
});

async function openWorkflow(filter, shelf) {
  if (typeof filter !== 'string') filter = undefined;
  if (!shelfDefs.some(([, id]) => id === shelf)) shelf = 'recentMaterials';
  active = null;
  view('workflow', {filter, shelf});
  byId('overviewNote').textContent = '正在读取项目状态与配置…';
  for (const id of ['pipeline', 'todo', 'focusSpotlight', 'outputs', 'runRows', 'batchHistory', 'recentMaterials', 'candidateShelf']) {
    byId(id).replaceChildren();
  }
  byId('runFilter').closest('details').open = filter != null;
  byId('firstProject').hidden = true;
  byId('editorialQueue').hidden = true;
  byId('workflow').querySelector('.focusBento').hidden = true;
  for (const id of ['totalRunsVal', 'progressCaption', 'stageMix', 'weekTrend', 'dailyRuns']) byId(id).replaceChildren();
  byId('recentPanel').hidden = false;
  for (const [, target] of shelfDefs) byId(target).textContent = '正在读取…';
  selectShelf(shelf);
  overviewData = null;
  byId('runFilter').value = filter || '0';
  window.DMSelect?.refresh(byId('runFilter'));
  await Promise.all([refreshOverview(), historyList()]);
}

for (const [index, title] of stageNames.entries()) {
  byId('runFilter').add(new Option(title, index + 1));
}
byId('runFilter').onchange = () => {
  recordRoute('workflow', {...route, filter:byId('runFilter').value}, true);
  if (overviewData) drawRuns();
};

function drawDailyRuns() {
  const list = byId('dailyRuns');
  const rows = overviewData.runs.filter(row => batchCalendarDay(row.created) === byId('workDate').value);
  workflowList(list, byId('workDate').value, rows, row => line(list, row.topic || row.name, nextAction[row.stage - 1], openRun(row.id)));
  if (!rows.length) list.append(element('p', '所选日期没有新建批次。'));
}
byId('workDate').value = localCalendarDay(new Date());
byId('workDate').onchange = () => { if (overviewData) drawDailyRuns(); };
byId('home').onclick = handle(home);
byId('workNav').onclick = handle(openWorkflow);
byId('channelNav').onclick = handle(()=>openConfigSection('collectionTasks'));
byId('brandHome').onclick = handle(home);
byId('startWithFile').onclick = () => openImportView();
byId('skipWelcome').onclick = handle(home);
byId('refreshHomeBtn').onclick = handle(async () => {
  await Promise.all([refreshOverview(), historyList()]);
});
byId('manageSources').onclick = handle(() => openConfigSection('collectionTasks'));
for (const id of ['homeWelcome','settingsWelcome']) byId(id).onclick = () => view('welcome');
function showSetupEntry(visible) {
  setupComplete = !visible;
  byId('homeWelcome').hidden = !visible;
  byId('settingsWelcome').hidden = false;
  byId('settingsWelcome').textContent = visible ? '首次初始化' : '重开项目向导';
}

async function prepareWelcome() {
  const version = navigationVersion;
  const welcome = byId('welcomeForm');
  let resume = byId('resumeWelcome');
  if (!resume) {
    resume = element('section', '', 'workCard');
    resume.id = 'resumeWelcome';
    welcome.before(resume);
  }
  welcome.hidden = true;
  resume.hidden = false;
  resume.textContent = '正在读取项目…';
  const runs = await request('?project_id=' + projectId);
  if (version !== navigationVersion) return;
  const existing = setupComplete || runs.length > 0;
  welcome.hidden = existing;
  resume.hidden = !existing;
  if (!existing) return;
  const project = currentProject();
  const button = element('button', runs.length ? '继续内容工作流' : '用示例开始导入', 'primary');
  button.onclick = runs.length ? handle(() => openWorkflow()) : () => { openImportView(); byId('sample').click(); };
  resume.replaceChildren(element('h2', project.name), element('p', [project.settings.audience, project.settings.style, runs.length + ' 个批次'].filter(Boolean).join(' · ')), button);
}

for (const button of document.querySelectorAll('[data-library]')) {
  button.onclick = handle(() => window.openLibrary(button.dataset.library));
}

for (const button of document.querySelectorAll('[data-config]')) {
  button.onclick = handle(() => openConfigSection(button.dataset.config));
}

setInterval(() => {
  if (!document.hidden && !byId('workflow').hidden && projectRows.length) {
    refreshOverview().catch(error => notify(error.message, 'error'));
  }
}, 30000);

function updateWelcomeCollection() {
  const fields = byId('welcomeForm').elements;
  const topic = fields.mode.value === 'topic';
  const examples = {xiaohongshu:'粘贴小红书账号主页链接', bilibili:'粘贴 B站 UP 主主页链接，或填写 UID',
    douyin:'粘贴抖音账号主页链接', instagram:'填写账号名，例如 @instagram', tiktok:'填写账号名，例如 @tiktok', x:'填写账号名，例如 @OpenAI'};
  byId('welcomeQueryLabel').textContent = topic ? '话题关键词' : '来源作者账号或主页链接';
  fields.query.placeholder = topic ? '例如 AI科普、旅行攻略、摄影教程' : examples[fields.platform.value];
  byId('welcomeQueryHelp').textContent = topic ? '填写你关心的方向；留空也可以先创建项目。' : '填写想持续跟踪的账号；留空也可以先创建项目。';
  fields.query.setCustomValidity('');
}
byId('welcomeForm').elements.platform.onchange = updateWelcomeCollection;
byId('welcomeForm').querySelectorAll('[name=mode]').forEach(input => input.onchange = updateWelcomeCollection);
updateWelcomeCollection();
function updateWelcomeLibrary() {
  const provider = byId('welcomeForm').elements.library_provider.value;
  byId('welcomeLibraryHelp').textContent = {local:'不需要连接账号。可上传本地文件，也可跟踪平台账号和话题。', notion:'创建项目后，填写 Notion 集成密钥和数据库链接，并选择标题、正文两列。请准备一个已分享给该集成的数据库。', feishu:'创建项目后，填写飞书应用 App ID、App Secret 和多维表格链接，并选择标题、正文两列。请准备有读取权限的自建应用。'}[provider];
  const collection = byId('welcomeForm').querySelector('[value=collection]');
  const localImport = byId('welcomeForm').querySelector('[value=import]');
  const startWithFile = provider === 'local' && !byId('welcomeForm').elements.query.value.trim();
  collection.textContent = provider === 'local' ? '创建项目，进入采集' : '创建项目，连接资料库';
  localImport.textContent = startWithFile ? '创建项目，导入素材' : '导入本地素材';
  const primary = startWithFile ? localImport : collection;
  const secondary = startWithFile ? collection : localImport;
  primary.className = 'primary';
  secondary.className = 'secondary';
  if (primary.parentElement.firstElementChild !== primary) primary.parentElement.prepend(primary);
}
byId('welcomeForm').elements.query.addEventListener('input', updateWelcomeLibrary);
byId('welcomeForm').querySelectorAll('[name=library_provider]').forEach(input => input.onchange = updateWelcomeLibrary);
updateWelcomeLibrary();

byId('welcomeForm').onsubmit = handle(async event => {
  const welcome = byId('welcomeForm');
  if (welcome.getAttribute('aria-busy') === 'true') return;
  welcome.setAttribute('aria-busy', 'true');
  const actions = welcome.querySelectorAll('[type="submit"]');
  actions.forEach(button => button.disabled = true);
  try {
    await finishWelcome(event.submitter?.value || 'collection');
  } finally {
    actions.forEach(button => button.disabled = false);
    welcome.removeAttribute('aria-busy');
  }
});

async function finishWelcome(choice) {
  const provider = byId('welcomeForm').elements.library_provider.value;
  const fields = byId('welcomeForm').elements;
  const values = {name: fields.name.value.trim(), audience: fields.audience.value.trim(), style: fields.style.value.trim()};
  const query = fields.query.value.trim();
  if (query) values.collection = {platform:fields.platform.value, mode:fields.mode.value, query,
    limit:fields.limit.valueAsNumber, minutes:Number(fields.minutes.value)};
  if (query && fields.collector_label.value.trim()) values.collection.collector_label = fields.collector_label.value.trim();
  const directory = byId('welcomeDirectory').value.trim();
  if (directory) values.directory = directory;
  const version = navigationVersion;
  const setup = await api('/api/setup', 'POST', values);
  if (version !== navigationVersion) { await loadProjects(); return; }
  projectId = setup.project_id;
  showSetupEntry(false);
  await loadProjects();
  if (version !== navigationVersion) return;
  byId('welcomeDirectory').value = '';
  if (!setup.created) {
    await home();
    notify('工作台已在其他页面完成初始化，已打开现有项目。本次填写的设置未应用。', 'info');
    return;
  }
  if (directory) {
    try { renderEnvMatrix(await api('/api/connections')); }
    catch (error) { byId('welcomeProcessingStatus').textContent = error.message; }
  }
  if (version !== navigationVersion) return;
  if (choice === 'collection' && provider !== 'local') {
    await openConfigSection('onlineLibraries');
    window.selectOnlineProvider?.(provider);
    notify('项目已创建。按页面步骤保存并检测资料库连接，再导入素材。','ok');
  } else if (choice === 'collection') {
    await openChannels();
    notify(query ? '项目和采集任务已创建。点击「立即采集」开始，结果会进入素材库。' : '项目已创建。选择平台，添加第一个账号或话题。', 'ok');
    byId('collectionTasks').scrollIntoView({block:'start'});
  } else if (choice === 'overview') {
    await home();
  } else {
    openImportView();
    if (choice === 'sample') byId('sample').click();
    await historyList();
  }
}

selectSourceKind('rss');
initializeAccounts();
initializeConnectors();

const initializeWorkspace = handle(async () => {
  const setup = await api('/api/setup');
  projectId = setup.project_id;
  await loadProjects(true);
  showSetupEntry(!setup.complete);
  const params = new URLSearchParams(location.search);
  const panel = params.get('view');
  const kind = Object.hasOwn(libraryNames, panel) ? panel : undefined;
  const saved = history.state?.workspace || {project:Number(params.get('project')), panel:kind ? 'library' : panel,
    kind, section:params.get('section') || undefined, run:Number(params.get('run')), filter:params.get('filter') || undefined, shelf:params.get('shelf') || undefined};
  if (projectRows.some(row => row.id === saved?.project)) {
    switchProject(saved.project);
    await Promise.all([restoreRoute(saved), historyList()]);
  } else if (!route) await home();
  try { renderEnvMatrix(await api('/api/connections')); }
  catch (error) { byId('welcomeProcessingStatus').textContent = `暂时无法读取服务状态。${error.message}`; }
});
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => initializeWorkspace(), {once: true});
else initializeWorkspace();
