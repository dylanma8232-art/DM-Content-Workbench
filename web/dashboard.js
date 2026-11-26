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
    guide: '填写返回 JSON 对象列表的 HTTPS 接口；支持嵌套数组路径（如 data.items）、游标分页（连抓最多 10 页）与环境变量 Bearer 令牌鉴权。'
  },
  notion: {
    label: '已授权的 Notion 数据库 ID 或链接',
    placeholder: '粘贴 Notion Database 链接或 32 位 ID',
    guide: '需先在服务环境变量配置 DM_NOTION_TOKEN，并在 Notion 中将目标数据库 Share 授权给该集成；下方可指定标题与正文属性名。'
  }
};

const ALL_FILE_ACCEPT = '.csv,.json,.txt,.md,.pdf,.mp3,.wav,.m4a,.ogg,.flac,.mp4,.mov,.mkv,.webm';

let active = null;
let filename = '';
let extractedFrom = null;
let projectId = 1;
let projectRows = [];
let overviewData = null;
let overviewRequest = 0;
let connectionCache = {
  model: null,
  notion: false,
  transcription: false,
  ffmpeg: false,
  ocr_enabled: false,
  ocr_tools: false,
  data_dir: ''
};

const nextAction = ['执行清洗', '分析素材', '确认选题', '生产内容', '查看稿件'];
const panels = ['welcome', 'overview', 'workflow', 'channels', 'setup', 'settings', 'detail', 'library'];

function element(tag, text = '', className = '') {
  const node = document.createElement(tag);
  if (text !== '') node.textContent = text;
  if (className) node.className = className;
  return node;
}

function svgIcon(markup) {
  const wrapper = document.createElement('span');
  wrapper.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${markup}</svg>`;
  return wrapper.firstElementChild;
}

function notify(message, tone = 'error') {
  const box = byId('notice');
  box.dataset.tone = tone;
  box.textContent = message || '';
}

async function request(path, method = 'GET', body) {
  const response = await fetch('/api/runs' + path, {
    method,
    headers: {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  const result = await response.json();
  if (!response.ok) {
    throw Error(Array.isArray(result.detail) ? '配置无效，请检查字段、长度和数量限制。' : result.detail);
  }
  return result;
}

async function api(path, method = 'GET', body) {
  const response = await fetch(path, {
    method,
    headers: {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  const result = await response.json();
  if (!response.ok) {
    throw Error(typeof result.detail === 'string' ? result.detail : '输入无效，请检查配置');
  }
  return result;
}

function handle(action) {
  return async event => {
    event?.preventDefault();
    const control = event?.submitter || event?.currentTarget;
    if (control) control.disabled = true;
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

function view(id) {
  for (const panel of panels) byId(panel).hidden = panel !== id;
  if (id !== 'detail') {
    active = null;
    for (const row of byId('history').querySelectorAll('[aria-current="true"]')) {
      row.setAttribute('aria-current', 'false');
    }
  }
  for (const button of byId('home').closest('nav').querySelectorAll('[aria-current]')) {
    button.removeAttribute('aria-current');
  }
  for (const [button, panel] of [['home', 'overview'], ['workNav', 'workflow'], ['channelNav', 'channels'], ['newRun', 'setup'], ['projectSettings', 'settings']]) {
    if (id === panel) byId(button).setAttribute('aria-current', 'page');
    else byId(button).removeAttribute('aria-current');
  }
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

function openImportView(options = {}) {
  form.reset();
  filename = '';
  extractedFrom = null;
  applyDefaults();
  byId('previewResult').textContent = '';
  setMediaMode(options.accept || '.csv,.json,.txt,.md', options.format || 'csv');
  if (!options.accept) byId('file').accept = ALL_FILE_ACCEPT;
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
  const runs = await request('?project_id=' + requestedProject);
  if (requestedProject !== projectId) return;
  history.replaceChildren();
  if (countNode) countNode.textContent = runs.length ? runs.length + ' 项' : '0 项';
  for (const row of runs) {
    const link = element('button', '');
    link.type = 'button';
    link.append(element('span', row.name), element('span', row.stage + '/5', 'histStage'));
    link.setAttribute('aria-current', String(active?.id === row.id));
    link.onclick = handle(async () => {
      history.closest('details').open = false;
      await show(await request('/' + row.id));
    });
    history.append(link);
  }
}

function makeReviewBadge(statusText) {
  const tone = statusText.includes('通过') ? 'ok' : statusText.includes('退回') ? 'err' : 'warn';
  return element('span', statusText, 'badge ' + tone);
}

async function show(run, selectedCandidate) {
  if (run.project_id !== projectId) return;
  view('detail');
  active = run;
  const data = run.payload;
  const path = '/' + run.id;

  byId('runName').textContent = run.name;
  byId('runInfo').textContent = '已完成 ' + run.stage + ' / 5 个阶段 · ' + data.materials.length + ' 条源记录 · 来源：' + (data.config.filename || run.name);
  byId('raw').textContent = JSON.stringify({配置: data.config, 素材: data.materials}, null, 2);

  const quickLinks = byId('detailQuickLinks');
  quickLinks.replaceChildren();
  const backHome = element('button', '返回总览', 'ghost');
  backHome.type = 'button';
  backHome.onclick = handle(home);
  const viewBatch = element('button', '查看本批素材', 'secondary');
  viewBatch.type = 'button';
  viewBatch.onclick = handle(() => window.openLibrary('materials', {run_id: run.id}));
  quickLinks.append(backHome, viewBatch);
  if (run.stage >= 3) {
    const viewTopics = element('button', '在选题库核对', 'ghost');
    viewTopics.type = 'button';
    viewTopics.onclick = handle(() => window.openLibrary('topics', {run_id: run.id}));
    quickLinks.append(viewTopics);
  }
  if (run.stage === 5) {
    const viewDraft = element('button', '在稿件库查看', 'ghost');
    viewDraft.type = 'button';
    viewDraft.onclick = handle(() => window.openLibrary('drafts', {run_id: run.id, key: 'draft'}));
    quickLinks.append(viewDraft);
  }

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
        '本批共载入 ' + data.materials.length + ' 条原始记录。点击下方按钮将按「最短保留 ' + (data.config.min_length || 1) + ' 字 + 全文精确去重」执行清洗。',
        'cardHint'
      ));
    } else if (run.stage === 2 && data.cleaned) {
      const keptCount = data.cleaned.filter(row => row.reason === '保留').length;
      primaryCard.append(element(
        'p',
        '清洗已完成：保留 ' + keptCount + ' 条有效素材，剔除 ' + (data.cleaned.length - keptCount) + ' 条过短或重复记录。下一步提取核心证据句' + (data.config.focus ? '（优先匹配关键词：' + data.config.focus + '）' : '') + '。',
        'cardHint'
      ));
    }

    let candidate, title;
    if (run.stage === 3) {
      if (!data.insights.length) {
        primaryCard.append(element('p', '当前批次没有通过清洗的有效素材，无法提取证据。你可以调低最短字数规则后重算，或导入新材料。', 'cardHint'));
        const actions = element('div', '', 'formActions');
        const again = element('button', '按当前项目规则重算', 'primary');
        again.type = 'button';
        again.onclick = handle(async () => show(await request(path + '/replay', 'POST', currentProject().settings)));
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
      const explain = () => {
        const choice = choices.find(row => row.key === candidate.value) || choices[0];
        if (choice) rationale.textContent = '推荐依据：' + choice.reason + ' · 关联源素材 ' + choice.ids.map(id => '#' + id).join('、');
      };
      candidate.onchange = explain;
      explain();
      primaryCard.append(rationale);
    }

    const advanceBtn = element('button', ['', '执行清洗', '提取证据摘录', '确认选题', '生成草稿'][run.stage], 'primary');
    advanceBtn.type = 'button';
    let production = {};
    if (run.stage === 4) {
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
      production = {engine, audience, style};
    }

    advanceBtn.onclick = handle(async () => show(await request(path + '/advance', 'POST', {
      ...Object.fromEntries(Object.entries(production).map(([key, input]) => [key, input.value])),
      stage: run.stage,
      candidate_key: candidate?.value || null,
      title: title?.value || ''
    })));
    primaryCard.append(advanceBtn);
  } else {
    const latestDecision = (data.reviews || []).filter(item => item.version === data.drafts.length).at(-1);
    const statusLabel = latestDecision ? latestDecision.decision : '待审核';
    const head = element('div', '', 'stageCardHead');
    head.append(element('h2', data.topic ? data.topic.title : run.name), makeReviewBadge('版本 ' + data.drafts.length + ' · ' + statusLabel));
    primaryCard.append(head);

    const label = element('label', '编辑 Markdown · 版本 ' + data.drafts.length);
    const editor = element('textarea', '');
    editor.rows = 12;
    editor.value = data.drafts.at(-1);
    label.append(editor);
    primaryCard.append(label);

    const editorActions = element('div', '', 'formActions');
    const save = element('button', '保存版本', 'primary');
    save.type = 'button';
    save.onclick = handle(async () => show(await request(path + '/draft', 'PUT', {version: data.drafts.length, content: editor.value})));

    const download = element('a', '导出已保存版本', 'download');
    download.href = '/api/runs' + path + '/export?version=' + data.drafts.length;

    const variant = element('button', '用同一选题制作另一份稿件', 'secondary');
    variant.type = 'button';
    variant.onclick = handle(async () => show(await request(path + '/variant', 'POST', {})));

    editorActions.append(save, download, variant);
    primaryCard.append(editorActions);
    if (latestDecision?.decision === '通过') attachDraftWebhook(primaryCard, run.id, data.drafts.length);

    const review = element('details', '', 'workFold');
    review.append(element('summary', '审核稿件'));
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
    for (const verdict of ['通过', '退回']) {
      const btn = element('button', verdict, verdict === '通过' ? 'primary' : '');
      btn.type = 'button';
      btn.onclick = handle(async () => show(await request(path + '/review', 'POST', {
        version: data.drafts.length,
        decision: verdict,
        note: note.value
      })));
      verdictBar.append(btn);
    }
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
  }
  task.append(primaryCard);

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
  replay.onclick = handle(async () => show(await request(path + '/replay', 'POST', currentProject().settings)));
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

function mapping() {
  byId('mapping').hidden = !['csv', 'json'].includes(fields.format.value);
}
fields.format.onchange = mapping;

byId('newRun').onclick = () => openImportView();
byId('sourceLightBtn').onclick = handle(() => openConfigSection('sourcesCard'));
byId('heroImportBtn').onclick = () => openImportView();

byId('file').onchange = handle(async () => {
  const file = byId('file').files[0];
  if (!file) return;
  let format = file.name.split('.').pop().toLowerCase();
  let content;
  let extracted = null;
  if (['md', 'csv', 'json', 'txt'].includes(format)) {
    if (file.size > 4000000) throw Error('请选择小于 4 MB 的文本文件');
    content = await file.text();
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
  form.reset();
  extractedFrom = null;
  applyDefaults();
  fields.name.value = '社区阅读访谈（合成示例）';
  fields.format.value = 'csv';
  fields.focus.value = '开放时间';
  fields.content.value = 'title,body\n周末读书会,读者希望延长周末开放时间。\n重复访谈,读者希望延长周末开放时间。\n借阅体验,读者建议在入口展示新书清单。';
  filename = '合成示例.csv';
  mapping();
  notify('已填充合成示例数据，可点击「预览清洗结果」或「导入并创建运行」。', 'ok');
};

byId('quickSampleBtn').onclick = () => {
  openImportView();
  byId('sample').click();
};

function importValues() {
  const values = Object.fromEntries(new FormData(form));
  values.project_id = projectId;
  values.extracted_from = extractedFrom;
  values.min_length = Number(values.min_length);
  values.filename = filename || '手工录入';
  return values;
}

form.onsubmit = handle(async () => {
  await show(await request('', 'POST', importValues()));
});

byId('previewImport').onclick = handle(async () => {
  if (!form.reportValidity()) return;
  const preview = await api('/api/import-preview', 'POST', importValues());
  byId('previewResult').textContent = [
    '共 ' + preview.总数 + ' 条 · 保留 ' + preview.保留 + ' 条 · 排除 ' + (preview.总数 - preview.保留) + ' 条',
    ...preview.前五条.map(row => '#' + row.id + '　[' + row.reason + ']　' + row.body.slice(0, 120))
  ].join('\n');
});

function applyDefaults() {
  const settings = currentProject().settings;
  if (!settings) return;
  fields.focus.value = settings.focus;
  fields.min_length.value = settings.min_length;
  for (const [key, value] of Object.entries(settings)) {
    if (byId('rules').elements[key]) byId('rules').elements[key].value = value;
  }
}

async function loadProjects() {
  projectRows = await api('/api/projects');
  byId('project').replaceChildren(...projectRows.map(row => new Option(row.name, row.id)));
  byId('project').value = String(projectId);
  applyDefaults();
}

byId('project').onchange = handle(async () => {
  projectId = Number(byId('project').value);
  applyDefaults();
  await home();
});

/* Preset Chips in #rulesCard */
document.querySelectorAll('#rulesCard .presetChip').forEach(chip => {
  const applyPreset = () => {
    const targetName = chip.dataset.fillTarget;
    const input = byId('rules').elements[targetName];
    if (input) {
      input.value = chip.dataset.fillValue;
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

function renderEnvMatrix(connections) {
  connectionCache = connections;
  const headerBtn = byId('headerStorageBtn');
  const readyCount = [
    Boolean(connections.model),
    Boolean(connections.notion),
    Boolean(connections.transcription && connections.ffmpeg),
    Boolean(connections.ocr_enabled && connections.ocr_tools)
  ].filter(Boolean).length;

  if (headerBtn) {
    headerBtn.textContent = readyCount
      ? '已配置 ' + readyCount + ' 项服务'
      : '本地存储';
  }

  const ocrReady = Boolean(connections.ocr_enabled && connections.ocr_tools);
  const asrReady = Boolean(connections.transcription && connections.ffmpeg);
  const setupOcr = byId('setupOcrBadge');
  const setupAsr = byId('setupAsrBadge');
  if (setupOcr) {
    setupOcr.textContent = ocrReady ? '文字 + OCR 已启用' : '文字直提就绪 · OCR 未开';
    setupOcr.className = 'badge ' + (ocrReady ? 'ok' : '');
  }
  if (setupAsr) {
    setupAsr.textContent = asrReady ? 'ASR + FFmpeg 已就绪' : '需配 ASR 与 FFmpeg';
    setupAsr.className = 'badge ' + (asrReady ? 'ok' : 'warn');
  }

  byId('connectionStatus').textContent = '写作引擎：' + (connections.model || '本地证据模式') + ' · Notion：' + (connections.notion ? '已配置' : '未配置');
  const directory = byId('storageSettings').elements.directory;
  if (byId('settings').hidden || document.activeElement !== directory) directory.value = connections.data_dir;
  if (byId('welcome').hidden || !byId('welcomeDirectory').value) byId('welcomeDirectory').value = connections.data_dir;
  byId('storageLocation').textContent = '当前 SQLite 数据目录：' + connections.data_dir;

  const matrix = byId('envMatrix');
  if (!matrix) return;
  matrix.replaceChildren();
  const items = [
    {
      key: 'model',
      title: 'AI 写作大模型（OpenAI 兼容接口）',
      stage: '阶段 5 · 成稿',
      ready: Boolean(connections.model),
      desc: connections.model
        ? '已配置模型：' + connections.model + '，生成草稿时可调用 AI 深度撰写。'
        : '当前未配置，默认使用本地证据摘录模式；配置后解锁 AI 深度成稿。',
      vars: ['DM_MODEL_BASE_URL', 'DM_MODEL_NAME', 'DM_MODEL_KEY']
    },
    {
      key: 'notion',
      title: 'Notion 数据库双向连接',
      stage: '采集 & 归档',
      ready: Boolean(connections.notion),
      desc: connections.notion
        ? '集成令牌已就绪，可添加 Notion 采集源或开启成稿自动快照备份。'
        : '配置集成令牌并授权数据库后，可将 Notion 作为素材源或远端快照仓库。',
      vars: ['DM_NOTION_TOKEN']
    },
    {
      key: 'transcription',
      title: '播客与音视频语音转写 (ASR)',
      stage: '阶段 1 · 多媒体导入',
      ready: asrReady,
      desc: asrReady
        ? 'ASR 转写接口与本机 FFmpeg 均已就绪，可直接上传 MP3/WAV/MP4/MOV。'
        : '本机 FFmpeg：' + (connections.ffmpeg ? '已安装' : '未安装') + ' · ASR 接口：' + (connections.transcription ? '已配置' : '未配置') + '。',
      vars: ['DM_ASR_BASE_URL', 'DM_ASR_MODEL', 'DM_ASR_KEY', 'ffmpeg']
    },
    {
      key: 'ocr',
      title: '扫描版 PDF 光学字符识别 (OCR)',
      stage: '阶段 1 · 文档导入',
      ready: ocrReady,
      desc: ocrReady
        ? 'Poppler 与 Tesseract 已启用，上传图片型扫描 PDF 会自动逐页识别文字。'
        : '工具状态：' + (connections.ocr_tools ? '已装 Poppler/Tesseract' : '未装 Poppler/Tesseract') + ' · 开关：' + (connections.ocr_enabled ? '已开' : '未设 DM_OCR_ENABLED=1') + '。',
      vars: ['DM_OCR_ENABLED=1', 'DM_OCR_LANG', 'pdftoppm', 'tesseract']
    }
  ];

  for (const item of items) {
    const card = element('div', '', 'envCard');
    const top = element('div', '', 'sourceItemHead');
    const titleGroup = element('div', '', 'sourceItemTitle');
    titleGroup.append(element('strong', item.title), element('span', item.stage, 'badge accent'));
    top.append(titleGroup, element('span', item.ready ? '已配置' : '按需接入', 'badge ' + (item.ready ? 'ok' : '')));

    const varsRow = element('div', '', 'envVars');
    for (const v of item.vars) varsRow.append(element('code', v));

    const foot = element('div', '', 'envCardFoot');
    foot.append(element('span', '配置方式：启动服务前设置上述环境变量'));
    const checkBtn = element('button', '测试此能力', 'ghost');
    checkBtn.type = 'button';
    checkBtn.onclick = () => {
      const select = byId('connectionCheck').elements.target;
      select.value = item.key;
      updateConnectionCheck();
      if (item.key === 'notion') {
        byId('connectionCheck').elements.database.focus();
      } else {
        byId('connectionCheck').requestSubmit();
      }
    };
    foot.append(checkBtn);

    card.append(top, element('p', item.desc), varsRow, foot);
    matrix.append(card);
  }
}

function selectSourceKind(kind) {
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
  card.onclick = () => selectSourceKind(card.dataset.kind);
});
byId('source').elements.kind.onchange = event => selectSourceKind(event.target.value);

async function renderSettings() {
  active = null;
  view('settings');
  applyDefaults();
  const [connections] = await Promise.all([api('/api/connections'), loadConnectorSettings()]);
  renderEnvMatrix(connections);
  byId('connectionResult').textContent = '';
  updateConnectionCheck();
}

async function openChannels() {
  view('channels');
  byId('sources').replaceChildren();
  await showSources();
}
async function openConfigSection(sectionId, options = {}) {
  if (sectionId === 'sourcesCard') await openChannels();
  else await renderSettings();
  if (options.sourceKind) {
    selectSourceKind(options.sourceKind);
  }
  if (!sectionId) return;
  const target = byId(sectionId);
  if (!target) return;
  let ancestor = target;
  while (ancestor) {
    if (ancestor.tagName === 'DETAILS') ancestor.open = true;
    ancestor = ancestor.parentElement;
  }
  target.scrollIntoView({block: 'start', behavior: 'smooth'});
  const focusTarget = options.focusField
    ? target.querySelector(`[name="${options.focusField}"]`)
    : (target.matches('input, select, textarea, button') ? target : target.querySelector('input, select, textarea'));
  focusTarget?.focus({preventScroll: true});
}
window.openConfigSection = openConfigSection;

byId('projectSettings').onclick = handle(() => renderSettings());
byId('headerStorageBtn').onclick = handle(() => openConfigSection('storageSettings'));
byId('storageSettings').onsubmit = handle(async () => {
  const value = byId('storageSettings').elements.directory.value;
  await api('/api/storage','PUT',{directory:value});
  renderEnvMatrix(await api('/api/connections'));
  notify('数据目录已保存，后续读写使用新位置。','ok');
});
byId('quickNewProject').onclick = handle(() => openConfigSection('newProject', {focusField: 'name'}));

const hints = {
  model: '在启动服务前设置环境变量 DM_MODEL_BASE_URL、DM_MODEL_NAME 与 DM_MODEL_KEY，重启后点击检测（将发送一条短请求验证连通性）。',
  notion: '在启动服务前设置环境变量 DM_NOTION_TOKEN，并在 Notion 将目标数据库授权给该集成，输入上方数据库 ID 验证读取权限。',
  transcription: '本机安装 FFmpeg，并设置环境变量 DM_ASR_BASE_URL、DM_ASR_MODEL 与 DM_ASR_KEY，重启后点击检测。',
  ocr: '本机安装 Poppler (pdftoppm) 与 Tesseract，设置 DM_OCR_ENABLED=1 与 DM_OCR_LANG（如 chi_sim+eng），重启后点击检测。'
};

function updateConnectionCheck() {
  const target = byId('connectionCheck').elements.target.value;
  byId('checkDatabase').hidden = target !== 'notion';
  byId('connectionHint').textContent = hints[target];
  byId('connectionResult').textContent = '';
}

byId('connectionCheck').elements.target.onchange = updateConnectionCheck;
byId('connectionCheck').onsubmit = handle(async () => {
  const config = Object.fromEntries(new FormData(byId('connectionCheck')));
  byId('connectionResult').textContent = '正在检测连接…';
  try {
    byId('connectionResult').textContent = '✓ ' + (await api('/api/connections/check', 'POST', config)).message;
  } catch (error) {
    byId('connectionResult').textContent = '✕ ' + error.message;
  }
});

byId('newProject').onsubmit = handle(async () => {
  projectId = (await api('/api/projects', 'POST', {name: byId('newProject').elements.name.value})).id;
  await loadProjects();

  byId('newProject').reset();
  await home();
});

byId('restorePage').onsubmit = handle(async () => {
  const restored = await api('/api/notion/restore', 'POST', {
    project_id: projectId,
    page_id: byId('restorePage').elements.page_id.value
  });
  await show(restored);
});

byId('rules').onsubmit = handle(async () => {
  const rules = Object.fromEntries(new FormData(byId('rules')));
  rules.min_length = Number(rules.min_length);
  await api('/api/projects/' + projectId, 'PUT', rules);
  await loadProjects();
  notify('项目默认规则已保存，后续导入与采集将自动生效', 'ok');
});

function sourceValues() {
  const values = Object.fromEntries(new FormData(byId('source')));
  return {...values, minutes: Number(values.minutes), project_id: projectId};
}

byId('previewSource').onclick = handle(async () => {
  if (!byId('source').reportValidity()) return;
  const preview = await api('/api/sources/preview', 'POST', sourceValues());
  byId('sourcePreview').textContent = [
    '试采成功 · 共读取 ' + preview.count + ' 条记录（预览前 ' + preview.sample.length + ' 条）：',
    ...preview.sample.map(row => '• ' + row.title + ' — ' + row.body.slice(0, 120))
  ].join('\n');
});

byId('source').onsubmit = handle(async () => {
  await api('/api/sources', 'POST', sourceValues());
  const currentKind = byId('source').elements.kind.value;
  byId('source').reset();
  selectSourceKind(currentKind);
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
    const refreshBtn = element('button', '立即刷新抓取', 'secondary');
    refreshBtn.type = 'button';
    refreshBtn.onclick = handle(async () => {
      try {
        const result = await api('/api/sources/' + row.id + '/refresh', 'POST', {});
        if (result.run) await show(result.run);
        else notify('刷新完成：' + result.status, 'ok');
      } finally {
        await showSources();
        await historyList();
      }
    });

    const interval = element('input', '');
    interval.type = 'number';
    interval.min = '0';
    interval.max = '43200';
    interval.value = row.minutes;
    const label = element('label', '轮询间隔（分钟，0 为暂停）');
    label.append(interval);

    const platform = element('input');
    platform.value = row.platform || '';
    platform.maxLength = 80;
    platform.setAttribute('list', 'collectionPlatforms');
    const platformLabel = element('label','采集平台或站点');
    platformLabel.append(platform);
    const update = element('button', '保存采集设置');
    update.type = 'button';
    update.onclick = handle(async () => {
      await api('/api/sources/' + row.id + '/interval', 'PUT', {minutes: Number(interval.value), platform: platform.value.trim()});
      await showSources();
      notify('平台与采集间隔已保存', 'ok');
    });

    controls.append(platformLabel, label, update, refreshBtn);
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
}

function openRun(id) {
  return async () => show(await request('/' + id));
}

function drawRuns() {
  const target = byId('runRows');
  const filter = Number(byId('runFilter').value);
  target.replaceChildren();
  const rows = overviewData.runs.filter(row => !filter || row.stage === filter);
  for (const row of rows) {
    line(target, row.name, row.created.slice(0, 10) + ' · ' + nextAction[row.stage - 1], openRun(row.id));
  }
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
  button.onclick = () => selectShelf(button.dataset.shelf);
}
selectShelf('recentMaterials');

for (const entry of document.querySelectorAll('[data-media]')) {
  const mode = {text: 0, pdf: 1, audio: 2}[entry.dataset.media];
  entry.onclick = () => openImportView({accept: document.querySelectorAll('.mediaModeCard')[mode].dataset.accept});
}

function drawProduction(data) {
  const complete = data.by_stage[4];
  byId('progressCaption').textContent = (data.runs.length - complete) + ' 个进行中 · ' + complete + ' 个已成稿';
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
    days.set(new Date(Date.now() - offset * 86400000).toISOString().slice(0, 10), 0);
  }
  for (const row of data.runs) {
    const day = row.created.slice(0, 10);
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

function drawOverview(data, shelves) {
  overviewData = data;
  byId('overviewTitle').textContent = '每日工作流';
  byId('overviewNote').textContent = currentProject().name;
  drawDailyRuns();

  byId('totalRunsVal').textContent = data.runs.length + ' 个批次';
  drawProduction(data);
  const stageNames = ['待清洗', '待分析', '待选题', '待写稿', '已成稿'];
  const pipeline = byId('pipeline');
  pipeline.replaceChildren();
  for (const [index, count] of data.by_stage.entries()) {
    const button = element('button');
    const latest = data.runs.find(row => row.stage === index + 1);
    button.style.setProperty('--stage', 'var(--stage' + index + ')');
    button.append(element('small', stageNames[index]), element('strong', String(count)), element('span', latest ? latest.topic || latest.name : '暂无批次'));
    button.onclick = () => {
      byId('runFilter').value = String(index + 1);
      drawRuns();
      const drawer = byId('runFilter').closest('details');
      drawer.open = true;
      drawer.scrollIntoView({block: 'nearest'});
    };
    pipeline.append(button);
  }
  const focus = data.runs.find(row => row.stage === 3 || (row.stage === 5 && row.review !== '通过'))
    || data.runs.find(row => row.stage < 5) || data.runs[0];
  const spot = byId('focusSpotlight');
  const title = focus ? focus.topic || focus.name : '导入第一份素材';
  byId('coverTitle').textContent = focus ? title : currentProject().name;
  byId('coverDate').textContent = focus ? focus.created.slice(0, 10).replaceAll('-', '.') : '项目素材册';
  const action = focus ? (focus.stage === 5 && focus.review !== '通过' ? '审阅稿件' : nextAction[focus.stage - 1]) : '选择素材';
  const button = element('button', action + ' →');
  button.onclick = focus ? handle(openRun(focus.id)) : () => openImportView();
  spot.replaceChildren(element('h3', title), element('p', focus ? stageNames[focus.stage - 1] + ' · ' + focus.materials + ' 条素材' : '从采访记录、PDF 或一段录音开始。'), button);
  byId('recentPanel').hidden = !shelves.some(shelf => shelf.items.length);
  const todo = byId('todo');
  todo.replaceChildren();
  for (const row of data.conflicts) line(todo, row.name, '处理 Notion 同步异常', openRun(row.id));
  for (const failure of data.failures) line(todo, failure.name, failure.error, () => openConfigSection('sourcesCard'));
  const exceptions = data.conflicts.length + data.failures.length;
  byId('batchHistory').closest('details').querySelector('summary').textContent = exceptions ? '采集与同步 · ' + exceptions + ' 项异常' : '采集与同步记录';
  const shelfDefs = [
    ['materials', 'recentMaterials', '暂无素材，可在采集导入页添加。'],
    ['topics', 'candidateShelf', '完成素材清洗与证据分析后，将在此展示推荐候选选题。'],
    ['drafts', 'outputs', '确认选题并生成初稿后，将在此展示最近修订的稿件。']
  ];
  for (const [index, [kind, targetId, emptyText]] of shelfDefs.entries()) {
    const shelf = byId(targetId);
    shelf.replaceChildren();
    for (const item of shelves[index].items) {
      const note = kind === 'drafts'
        ? '版本 ' + item.version + ' · ' + item.status
        : kind === 'topics'
          ? item.reason
          : item.source + ' · ' + item.status;
      line(shelf, item.title, note, () => window.openLibrary(kind, {run_id: item.run_id, key: item.key}));
    }
    if (!shelf.children.length) shelf.append(element('p', emptyText));
  }

  const batches = byId('batchHistory');
  batches.replaceChildren();
  for (const batch of data.batches) {
    const summary = batch.error
      ? '失败 · ' + batch.error
      : '读取 ' + batch.received + ' · 新增 ' + batch.added + ' · 更新 ' + batch.updated + ' · 重复 ' + batch.duplicates;
    line(
      batches,
      batch.name,
      batch.created + ' UTC · ' + summary,
      batch.run_id ? () => window.openLibrary('materials', {run_id: batch.run_id}) : null
    );
  }
  if (!batches.children.length) batches.append(element('p', '下次导入文件或刷新在线来源后，将在此记录完整批次明细。'));

  drawRuns();
}

async function refreshOverview() {
  const token = ++overviewRequest;
  const id = projectId;
  const base = '/api/projects/' + id;
  const [data, connections, ...shelves] = await Promise.all([
    api(base + '/overview'),
    api('/api/connections'),
    ...['materials', 'topics', 'drafts'].map(kind =>
      api(base + '/library/' + kind + '?limit=3&compact=true&preview=true' + (kind === 'topics' ? '&status=' + encodeURIComponent('候选') : ''))
    )
  ]);
  renderEnvMatrix(connections);
  if (token === overviewRequest && id === projectId && !byId('workflow').hidden) {
    drawOverview(data, shelves);
  }
}

async function openWorkflow() {
  active = null;
  view('workflow');
  byId('overviewNote').textContent = '正在读取项目状态与配置…';
  for (const id of ['pipeline', 'todo', 'focusSpotlight', 'outputs', 'runRows', 'batchHistory', 'recentMaterials', 'candidateShelf']) {
    byId(id).replaceChildren();
  }
  byId('recentPanel').hidden = true;
  selectShelf('recentMaterials');
  overviewData = null;
  byId('runFilter').value = '0';
  await Promise.all([refreshOverview(), historyList()]);
}

for (const [index, title] of nextAction.entries()) {
  byId('runFilter').add(new Option(title, index + 1));
}
byId('runFilter').onchange = () => {
  if (overviewData) drawRuns();
};

function drawDailyRuns() {
  const list = byId('dailyRuns');
  list.replaceChildren();
  const rows = overviewData.runs.filter(row => row.created.slice(0, 10) === byId('workDate').value);
  for (const row of rows) line(list, row.topic || row.name, nextAction[row.stage - 1], openRun(row.id));
  if (!rows.length) list.append(element('p', '所选日期没有新建批次。'));
}
byId('workDate').value = new Date().toISOString().slice(0, 10);
byId('workDate').onchange = () => { if (overviewData) drawDailyRuns(); };
byId('home').onclick = handle(home);
byId('workNav').onclick = handle(openWorkflow);
byId('channelNav').onclick = handle(openChannels);
byId('brandHome').onclick = handle(home);
byId('skipWelcome').onclick = handle(home);
byId('refreshHomeBtn').onclick = handle(async () => {
  await Promise.all([refreshOverview(), historyList()]);
});
byId('manageSources').onclick = handle(() => openConfigSection('sourcesCard'));
for (const id of ['openWelcome','homeWelcome','settingsWelcome']) byId(id).onclick = () => view('welcome');
function showSetupEntry(visible) {
  for (const id of ['firstProject','homeWelcome','settingsWelcome']) byId(id).hidden = !visible;
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

byId('welcomeForm').onsubmit = handle(async event => {
  const choice = event.submitter?.value || 'overview';
  const values = Object.fromEntries(new FormData(byId('welcomeForm')));
  if (Boolean(values.platform.trim()) !== Boolean(values.source_address.trim())) throw Error('请同时填写采集平台和来源地址，或将两项留空。');
  const directory = byId('welcomeDirectory').value.trim();
  if (directory !== connectionCache.data_dir) {
    await api('/api/storage','PUT',{directory});
    renderEnvMatrix(await api('/api/connections'));
  }
  const setup = await api('/api/setup', 'POST', values);
  projectId = setup.project_id;
  showSetupEntry(false);
  await loadProjects();
  if (choice === 'overview') {
    await home();
  } else {
    openImportView();
    if (choice === 'sample') byId('sample').click();
    await historyList();
  }
});

selectSourceKind('rss');
initializeAccounts();
initializeConnectors();

handle(async () => {
  const [setup, connections] = await Promise.all([api('/api/setup'), api('/api/connections')]);
  projectId = setup.project_id;
  renderEnvMatrix(connections);
  await loadProjects();
  showSetupEntry(!setup.complete);
  await home();
})();
