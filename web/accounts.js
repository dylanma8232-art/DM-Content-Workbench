const accountFields = {followers: '期末粉丝', views: '阅读 / 播放', engagements: '互动次数', published: '发布数'};
const platformBrands = DMPlatforms.map(platform => ({...platform, name: platform.label}));

function brandFor(name) {
  const key = name.trim().toLowerCase();
  return platformBrands.find(brand => [brand.id, brand.label, brand.accountName, ...(brand.aliases || [])]
    .some(value => value.toLowerCase() === key))
    || {name, color: '#54617c', tint: '#f1f4fa'};
}

function brandIcon(brand) {
  if (!brand.icon) return element('span', Array.from(brand.name)[0], 'brandFallback');
  const icon = element('img');
  icon.src = '/icons/' + brand.icon + '.svg?v=20261127-ui6';
  icon.alt = '';
  icon.width = icon.height = 38;
  return icon;
}

function platformCard(brand, accounts, isRange) {
  const card = element('details', '', 'platformCard');
  const summary = element('summary', '', 'platformSummary');
  card.style.setProperty('--brand', brand.color);
  card.style.setProperty('--tint', brand.tint);
  const reported = accounts.filter(row => row.views !== null);
  const total = key => reported.length ? reported.reduce((sum, row) => sum + row[key], 0).toLocaleString() : '—';
  const identity = element('span', '', 'platformIdentity');
  const name = element('span');
  name.append(element('b', brand.name), element('small', accounts.length === 1 ? accounts[0].account : `${accounts.length} 个账号`));
  identity.append(brandIcon(brand), name);
  const state = element('small', reported.length ? `${reported.length} 个账号已报` : '未录入', 'platformStatus');
  state.dataset.state = reported.length ? 'reported' : 'pending';
  const stats = element('span', '', 'platformNumbers');
  for (const [key, label] of Object.entries(accountFields)) {
    const metric = element('span');
    metric.append(element('small', label), element('b', total(key)));
    stats.append(metric);
  }
  summary.append(identity, state, stats);
  const details = element('div', '', 'accountRoster');
  const views = reported.reduce((sum, row) => sum + row.views, 0);
  const engagements = reported.reduce((sum, row) => sum + row.engagements, 0);
  const rate = views ? (engagements / views).toLocaleString('zh-CN', {style:'percent', maximumFractionDigits:2}) : '—';
  details.append(element('p', '所选时段互动率 ' + rate + (reported.length ? ' · 互动 ' + engagements.toLocaleString() + ' 次 / 阅读或播放 ' + views.toLocaleString() + ' 次 · 有记录账号 ' + reported.length + ' 个' : ' · 暂无日报'), 'fieldHelp'));
  const dates = [byId('accountStartDate').value, byId('accountDate').value].filter(Boolean);
  details.append(element('small', [...new Set(dates)].join(' 至 ') + ' · 实报 ' + accounts.reduce((sum,row) => sum + (row.reports || 0), 0) + ' 个账号日'));
  for (const row of accounts) {
    const entry = element('div','','accountEntry');
    entry.append(element('b',row.account));
    if (isRange && row.latest) entry.append(element('small','粉丝记录日期 ' + row.latest));
    if (accounts.length > 1 && row.views !== null) entry.append(element('small',Object.entries(accountFields).map(([key,label]) => label + ' ' + (row[key]?.toLocaleString() ?? '—')).join(' · ')));
    const actions = element('div', '', 'accountRowActions');
    if (!isRange) {
      const report = element('button',row.views === null ? '补录日报' : '编辑日报','ghost');
      report.onclick = () => editReport(row);
      actions.append(report);
    }
    const edit = element('button','编辑账号','ghost');
    edit.dataset.editAccount = '';
    edit.onclick = () => openPlatformSetup('edit', row);
    const remove = element('button','删除账号','ghost');
    remove.onclick = handle(async () => {
      const project = projectId;
      const impact = await api('/api/projects/' + project + '/account-impact?' + new URLSearchParams({platform:row.platform, account:row.account}));
      if (project !== projectId || !remove.isConnected) return;
      if (!window.confirm(`从「${currentProject().name}」永久删除「${row.account}」及 ${impact.reports} 份历史日报？若只停止统计应报日期，请选择停报。`)) return;
      await api('/api/projects/' + project + '/platform-accounts/manage?expected_reports=' + impact.reports,'DELETE',{platform:row.platform,account:row.account});
      if (project !== projectId || byId('overview').hidden) return;
      await home();
      notify('账号及其历史日报已删除。','ok');
    });
    const period = element('details');
    period.append(element('summary','设置应报期间'));
    const dateLabel = element('label','生效日期');
    const effective = element('input'); effective.type = 'date'; effective.value = byId('accountDate').value;
    dateLabel.append(effective); period.append(dateLabel);
    for (const [active,label] of [[true,'从该日开始应报'],[false,'从该日起停报']]) {
      const change = element('button',label,'ghost'); change.type = 'button';
      change.onclick = handle(async () => {
        const project = projectId;
        await api('/api/projects/' + project + '/reporting-period','PUT',{platform:row.platform,account:row.account,active,day:effective.value});
        if (project === projectId && !byId('overview').hidden) await home();
      });
      period.append(change);
    }
    actions.append(edit,period,remove);
    entry.append(actions);
    details.append(entry);
  }
  card.append(summary, details);
  return card;
}

let reportRequest = 0;
let knownAccounts = [];
const syncingProjects = new Set();

let douyinViewProject;
const douyinConfirmUntil = new Map();
const douyinReading = new Set();
let douyinPoll;
const douyinOperations = new Map();

function showDouyinControls({message, installed, state, busy = false}) {
  byId('douyinStatus').textContent = message;
  const dayButton = byId('viewDouyinDay');
  dayButton.hidden = !state?.connected || !/^\d{4}-\d{2}-\d{2}$/.test(state.day);
  if (!dayButton.hidden) {
    dayButton.textContent = `查看 ${state.day} 日报`;
    dayButton.onclick = handle(async () => {
      selectReportDay(state.day);
      await home();
      byId('accountPlatforms').scrollIntoView({block: 'start'});
    });
  }
  byId('douyinInstall').hidden = installed;
  const boundProject = projectRows.find(row => row.id === state?.boundProject);
  const switchBinding = byId('viewBoundProject');
  switchBinding.hidden = !boundProject;
  if (boundProject) {
    switchBinding.textContent = `前往绑定项目「${boundProject.name}」`;
    switchBinding.onclick = () => {
      const picker = byId('project');
      picker.value = String(boundProject.id);
      picker.dispatchEvent(new Event('change', {bubbles:true}));
      byId('accountConnections').open = true;
    };
  }
  byId('douyinConnect').hidden = !installed || state?.connected !== false || !!boundProject;
  byId('douyinSync').hidden = !installed || !state?.connected;
  byId('douyinDaily').hidden = !installed || !(state?.enabled || state?.connected);
  byId('douyinDaily').textContent = state?.enabled ? '停止每日回填' : '开启每日回填';
  byId('douyinRefresh').hidden = !installed;
  byId('douyinDisconnect').hidden = !installed || !state?.connected;
  for (const id of ['douyinConnect', 'douyinSync', 'douyinDaily', 'douyinRefresh', 'douyinDisconnect']) byId(id).disabled = busy;
}

function douyinRequest(project, action) {
  const requestId = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    function finish(callback, value) {
      clearTimeout(timer);
      window.removeEventListener('message', receive);
      callback(value);
    }
    function receive(event) {
      const reply = event.data;
      if (event.source !== window || event.origin !== location.origin || reply?.type !== 'dm-douyin-daily-result' || reply.requestId !== requestId || reply.project !== project || reply.action !== action) return;
      if (reply.ok !== true) finish(reject, Error(reply.error || '助手未能完成操作。'));
      else finish(resolve, reply.status);
    }
    const timer = setTimeout(() => finish(reject, Error('等待助手回复超时，结果尚未确认。请刷新连接状态。')), action === 'status' ? 5000 : 60000);
    window.addEventListener('message', receive);
    window.postMessage({type: 'dm-douyin-daily', requestId, project, action}, location.origin);
  });
}

async function refreshDouyin(id, action = 'status') {
  clearTimeout(douyinPoll);
  douyinViewProject = id;
  const current = () => id === projectId && !byId('overview').hidden;
  const installed = document.documentElement.dataset.dmDouyinDailyExtension === '1';
  if (!installed) {
    showDouyinControls({installed, message: '未检测到支持抖音回填的助手。请安装或更新浏览器助手。'});
    return;
  }
  const messages = {status: '正在读取连接状态…', connect: '正在打开助手确认页…', retry: '正在读取抖音日报…', enable: '正在开启每日回填…', disable: '正在停止每日回填…', disconnect: '正在解除绑定…'};
  const pending = douyinOperations.get(id);
  showDouyinControls({installed, busy: true, message: messages[pending || action]});
  if (pending) return;
  douyinOperations.set(id, action);
  const previousConfirmation = douyinConfirmUntil.has(id);
  let refreshReports = false;
  try {
    const state = await douyinRequest(id, action);
    if (action === 'connect') douyinConfirmUntil.set(id, Date.now() + 120000);
    if (!current()) return;
    if (typeof state?.connected !== 'boolean' || typeof state.enabled !== 'boolean') throw Error('无法确认连接状态，请刷新连接状态。');
    const waiting = !state.connected && Date.now() < (douyinConfirmUntil.get(id) || 0);
    const reading = state.connected && (state.running === true || String(state.message || '').startsWith('正在读取'));
    const wasReading = douyinReading.has(id);
    if (reading) douyinReading.add(id);
    else douyinReading.delete(id);
    if (!waiting) douyinConfirmUntil.delete(id);
    const account = state.account || state.platform_uid;
    const connection = state.connected ? '已连接抖音账号' + (account ? '「' + account + '」' : '') + '。' : waiting ? '请在助手确认页登录抖音并确认账号，完成后回到此页。' : state.boundProject ? '到绑定项目管理此连接。' : '点击连接，在助手确认页登录抖音并确认账号。';
    const text = [connection, state.enabled ? '每日回填已开启，按北京时间中午回填前一天的日报。' : '每日回填已关闭。', state.day && '日报日期：' + state.day + '。', state.message].filter(Boolean).join(' ');
    showDouyinControls({installed, state, message: text});
    byId('douyinDaily').onclick = () => refreshDouyin(projectId, state.enabled ? 'disable' : 'enable');
    byId('douyinSync').disabled = reading;
    if (waiting || reading) douyinPoll = setTimeout(() => { if (current()) refreshDouyin(id); }, 3000);
    refreshReports = !reading && state.connected && (wasReading || previousConfirmation || action === 'retry');
  } catch (error) {
    if (current()) showDouyinControls({installed, message: error.message});
  } finally {
    douyinOperations.delete(id);
  }
  if (refreshReports && current()) await home();
}

function refreshActiveDouyin() {
  if (douyinViewProject === projectId && !byId('overview').hidden) refreshDouyin(projectId);
}
window.addEventListener('focus', refreshActiveDouyin);
new MutationObserver(refreshActiveDouyin).observe(document.documentElement, {attributes: true, attributeFilter: ['data-dm-douyin-daily-extension']});

async function refreshSyncControls(id, token) {
  refreshDouyin(id);
  const connect = byId('reportConnect');
  const sync = byId('reportSyncNow');
  const auto = byId('reportAuto');
  const label = byId('reportAutoLabel');
  const status = byId('reportSyncStatus');
  connect.hidden = sync.hidden = label.hidden = byId('reportSyncHelp').hidden = true;
  status.textContent = '正在读取日报连接状态…';
  try {
    const info = await api('/api/projects/' + id + '/report-sync');
    if (token !== reportRequest || id !== projectId || byId('overview').hidden) return;
    connect.hidden = !!info.configured;
    sync.hidden = label.hidden = byId('reportSyncHelp').hidden = !info.configured;
    sync.disabled = auto.disabled = syncingProjects.has(id);
    auto.checked = info.enabled;
    status.textContent = !info.configured ? '连接已有的统计接口，可一起回填账号名称和运营数据。也可以直接填写一天的日报。' :
      [info.enabled ? '自动回填已开启。' : '数据源已连接，可立即回填。', info.last_success && '上次成功：' + info.last_success, info.message].filter(Boolean).join(' ');
  } catch (error) {
    if (token === reportRequest && id === projectId) status.textContent = '无法读取回填状态。' + error.message;
  }
}

async function changeReportSync(toggle) {
  const id = projectId;
  if (syncingProjects.has(id)) return;
  syncingProjects.add(id);
  const auto = byId('reportAuto');
  const sync = byId('reportSyncNow');
  auto.disabled = sync.disabled = true;
  byId('reportSyncStatus').textContent = toggle ? '正在保存自动回填设置…' : '正在从统计接口读取日报…';
  try {
    const base = '/api/projects/' + id;
    const result = toggle ? await api(base + '/report-sync', 'PUT', {enabled:auto.checked}) : await api(base + '/connectors/reports/sync', 'POST');
    if (id !== projectId || byId('overview').hidden) return;
    syncingProjects.delete(id);
    // Keep the selected reporting period when new daily records arrive.
    await home();
    if (!toggle && id === projectId && !byId('overview').hidden) notify(`已回填 ${result.count} 条日报。${result.latest ? '最新日期：' + result.latest : ''}`, 'ok');
  } catch (error) {
    syncingProjects.delete(id);
    if (id === projectId && !byId('overview').hidden) {
      await refreshSyncControls(id, reportRequest);
      notify(error.message, 'error');
    }
  } finally { syncingProjects.delete(id); }
}

let editingAccount = null;
let accountFormRevision = 0;
let csvPreview;
let reportDateProject;

function describeReport(row) {
  if (row.error) return row.error;
  const changes = Object.entries(row.changes).map(([field, values]) => accountFields[field] + ' ' + values.join(' → '));
  return [row.platform, row.account, row.day, row.status, ...changes].join(' · ');
}

function clearAccountPreview() {
  accountFormRevision++;
  csvPreview = null;
  byId('accountCsvResult')?.replaceChildren();
  if (byId('platformSetup').elements.mode.value === 'csv') byId('platformSave').textContent = '预览日报';
}
let overviewProject;

function openPlatformSetup(mode = 'register', row = null) {
  clearAccountPreview();
  const form = byId('platformSetup');
  form.reset();
  editingAccount = row && {platform:row.platform,account:row.account};
  if (row) {
    form.elements.platform.value = row.platform;
    form.elements.account.value = row.account;
  }
  const csv = mode === 'csv';
  form.elements.mode.value = mode;
  byId('reportUpload').hidden = !csv;
  form.elements.file.required = csv;
  byId('accountSetupTitle').textContent = row ? '编辑运营账号' : csv ? '导入账号日报 CSV' : '添加运营账号';
  byId('accountSetupHint').textContent = row ? '修改后历史日报随账号迁移。同步旧名时按已知别名匹配，身份冲突时需要核对。' : csv ? '选择平台和账号，预览每行差异后确认导入。' : '选择你运营的平台，填写用于区分账号的名称。';
  byId('platformSave').textContent = csv ? '预览日报' : '保存账号';
  syncPlatformPicker(form);
  if (row) {
    form.elements.platform.value = row.platform;
    if (!brandFor(row.platform).id) {
      for (const item of form.querySelectorAll('[data-platform]')) item.setAttribute('aria-checked','false');
      byId('accountSetupHint').textContent += ' 原平台：' + row.platform + '。';
    }
  }
  form.elements.platform.dispatchEvent(new Event('input'));
  byId('reportForm').hidden = true;
  form.hidden = false;
  form.scrollIntoView({block:'start'});
  form.querySelector('[data-platform-picker] button[aria-checked="true"]')?.focus({preventScroll:true});
}


function selectReportDay(day) {
  reportDateProject = projectId;
  byId('accountStartDate').value = day;
  byId('accountDate').value = day;
  window.refreshDateRange?.(byId('accountDate'));
}

function initializeAccounts() {
  const form = byId('platformSetup');
  const previewOutput = element('div');
  previewOutput.id = 'accountCsvResult';
  previewOutput.setAttribute('aria-live', 'polite');
  byId('reportUpload').append(previewOutput);
  form.addEventListener('input', clearAccountPreview);
  byId('reportForm').addEventListener('input', () => accountFormRevision++);
  byId('connectorPreview').addEventListener('click', () => {
    byId('connectorImport').textContent = '确认导入至「' + currentProject().name + '」';
  });
  const tools=document.querySelector('.accountToolsMenu');
  tools.addEventListener('click',event=>{if(event.target.closest('button'))tools.open=false;});
  document.addEventListener('pointerdown',event=>{if(!tools.contains(event.target))tools.open=false;});
  tools.addEventListener('keydown',event=>{if(event.key==='Escape'){tools.open=false;tools.querySelector('summary').focus();}});
  byId('platformSetupBtn').onclick = byId('addFirstAccount').onclick = () => openPlatformSetup();
  byId('manageAccounts').onclick = () => {
    const rows = byId('accountRows');
    for (const detail of rows.querySelectorAll('details')) detail.open = true;
    rows.querySelector('.accountRowActions button[data-edit-account]')?.focus();
  };
  byId('importDailyCsv').onclick = () => openPlatformSetup('csv');
  byId('closePlatformSetup').onclick = () => { form.hidden = true; };
  byId('dailyTemplate').href = 'data:text/csv;charset=utf-8,' + encodeURIComponent('\uFEFF日期,粉丝,阅读或播放,互动,发布\r\n');
  form.elements.platform.oninput = () => {
    const selected = brandFor(form.elements.platform.value);
    const names = knownAccounts.filter(row => brandFor(row.platform).name === selected.name);
    byId('knownPlatformAccounts').replaceChildren(...names.map(row => new Option(row.account)));
  };
  form.onsubmit = handle(async () => {
    const id = projectId;
    const values = {platform:form.elements.platform.value.trim(), account:form.elements.account.value.trim()};
    const endpoint = '/api/projects/' + id;
    let message = '已登记账号，可在平台卡片中填写日报。';
    if (form.elements.mode.value === 'csv') {
      const revision = accountFormRevision;
      const current = () => id === projectId && revision === accountFormRevision && !form.hidden && !byId('overview').hidden;
      const file = form.elements.file.files[0];
      if (file.size > 250000) throw Error('请选择小于 250 KB 的日报 CSV');
      const content = await file.text();
      if (!current()) return;
      const payload = {...values, content};
      if (!csvPreview) {
        const result = await api(endpoint + '/account-import?preview=true','POST',payload);
        if (!current()) return;
        previewOutput.replaceChildren(element('p', '目标项目「' + currentProject().name + '」' + (result.valid ? ' · 尚未导入' : ' · 整批尚未导入，请修正文件')));
        for (const row of result.rows) previewOutput.append(element('div', '第 ' + row.line + ' 行 · ' + describeReport(row)));
        if (result.valid) {
          csvPreview = result.preview_id;
          byId('platformSave').textContent = '确认导入';
        }
        return;
      }
      const key = csvPreview;
      csvPreview = null;
      byId('platformSave').textContent = '重新预览';
      const result = await api(endpoint + '/account-import?preview_id=' + key,'POST',payload);
      if (!current()) return;
      if (id !== projectId || byId('overview').hidden) return;
      selectReportDay(result.latest);
      message = '已导入 ' + result.count + ' 天的日报。';
    } else if (form.elements.mode.value === 'edit') {
      await api(endpoint + '/platform-accounts/manage', 'PUT', {...editingAccount,new_platform:values.platform,new_account:values.account});
      message = '账号信息已更新。';
    } else await api(endpoint + '/platform-accounts','PUT',values);
    if (id !== projectId || byId('overview').hidden) return;
    form.hidden = true;
    await home();
    notify(message,'ok');
  });
  const picker = byId('accountDate');
  const now = new Date();
  selectReportDay(new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10));
  picker.onchange = handle(home);
  byId('addReport').onclick = () => editReport();
  byId('douyinConnect').onclick = () => refreshDouyin(projectId, 'connect');
  byId('douyinSync').onclick = () => refreshDouyin(projectId, 'retry');
  byId('douyinRefresh').onclick = () => refreshDouyin(projectId);
  byId('douyinDisconnect').onclick = () => {
    if (window.confirm('解除这个抖音账号的绑定并停止每日回填？已保存的日报会保留。')) refreshDouyin(projectId, 'disconnect');
  };
  byId('reportSyncNow').onclick = () => changeReportSync(false);
  byId('reportAuto').onchange = () => changeReportSync(true);
  byId('closeReport').onclick = () => { byId('reportForm').hidden = true; };
  byId('reportForm').onsubmit = handle(async () => {
    const id = projectId;
    const values = Object.fromEntries(new FormData(byId('reportForm')));
    const path = '/api/projects/' + id + '/account-daily';
    const revision = accountFormRevision;
    const preview = await api(path + '?preview=true', 'PUT', values);
    if (revision !== accountFormRevision || id !== projectId || byId('reportForm').hidden || byId('overview').hidden) return;
    if (preview.row.status === '覆盖' && !window.confirm('确认覆盖「' + currentProject().name + '」的日报？\n' + describeReport(preview.row))) return;
    await api(path + '?preview_id=' + preview.preview_id, 'PUT', values);
    if (id !== projectId || byId('overview').hidden) return;
    byId('reportForm').hidden = true;
    selectReportDay(values.day);
    await home();
    notify('账号日报已保存', 'ok');
  });
}

function editReport(report = {}) {
  clearAccountPreview();
  const form = byId('reportForm');
  byId('platformSetup').hidden = true;
  form.reset();
  form.elements.day.value = byId('accountDate').value;
  for (const key of ['account','platform', ...Object.keys(accountFields)]) {
    form.elements[key].value = report[key] ?? '';
  }
  syncPlatformPicker(form);
  // Reports are keyed by their stored platform string, including old aliases.
  if (report.platform) form.elements.platform.value = report.platform;
  if (report.platform && !brandFor(report.platform).id) {
    form.querySelectorAll('[data-platform-picker] button').forEach((button, index) => {
      button.setAttribute('aria-checked', 'false');
      button.tabIndex = index === 0 ? 0 : -1;
    });
  }
  form.hidden = false;
  (form.querySelector('[data-platform-picker] button[aria-checked="true"]') || form.elements.account).focus();
}

async function home() {
  const id = projectId;
  const token = ++reportRequest;
  view('overview');
  if (overviewProject !== id) {
    clearAccountPreview();
    if (overviewProject != null && reportDateProject !== id) {
      const now = new Date();
      selectReportDay(new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10));
    }
    editingAccount = null;
    byId('platformSetup').reset();
    byId('reportForm').reset();
    knownAccounts = [];
    byId('platformSetup').hidden = true;
    byId('reportForm').hidden = true;
    overviewProject = id;
    reportDateProject = id;
  }
  byId('manageAccounts').disabled = true;
  const unreported = byId('unreportedAccounts');
  unreported.hidden = true;
  unreported.open = false;
  const shortcuts = unreported.querySelector('.formActions');
  shortcuts.replaceChildren();
  const date = byId('accountDate').value;
  const start = byId('accountStartDate').value || date;
  const isRange = start !== date;
  const metrics = byId('accountMetrics');
  metrics.setAttribute('aria-busy', date ? 'true' : 'false');
  const list = byId('accountRows');
  const placeholders = Object.values(accountFields).map(label => {
    const card = element('section');
    card.append(element('h2', label), element('strong', '—'));
    return card;
  });
  metrics.replaceChildren(...placeholders);
  refreshSyncControls(id, token);
  byId('accountEmpty').hidden = true;
  byId('homeIntroText').textContent = date ? '正在读取所选日期的运营数据…' : '选择日期，查看各平台的运营数据。';
  list.replaceChildren();
  byId('accountScope').textContent = currentProject().name;
  byId('reachPanel').hidden = true;
  if (!date) return;
  let report, coverage;
  try {
    const suffix = '?day=' + date + '&start=' + start;
    [report, coverage] = await Promise.all([api('/api/projects/' + id + '/account-daily' + suffix), api('/api/projects/' + id + '/reporting-summary' + suffix), historyList()]);
  } catch (error) {
    if (token !== reportRequest || id !== projectId || byId('overview').hidden) return;
    byId('homeIntroText').textContent = '运营数据读取失败。' + error.message;
    const retry = element('button', '重新读取', 'ghost');
    retry.onclick = handle(home);
    list.append(retry);
    return;
  } finally {
    if (token === reportRequest && id === projectId) metrics.setAttribute('aria-busy', 'false');
  }
  if (token !== reportRequest || id !== projectId || byId('overview').hidden) return;
  const rows = report.accounts;
  const actual = new Map(coverage.actual.map(row => [JSON.stringify([row.platform,row.account]),row]));
  for (const row of rows) Object.assign(row, actual.get(JSON.stringify([row.platform,row.account])));
  knownAccounts = rows;
  byId('manageAccounts').disabled = !rows.length;
  const trend = byId('reachTrend');
  trend.replaceChildren();
  byId('reachPanel').hidden = !report.trend.length;
  byId('accountEmpty').hidden = !!rows.length;
  const welcome = byId('homeWelcome');
  (rows.length ? document.querySelector('.accountToolsPopover') : byId('accountEmpty').querySelector('.formActions')).append(welcome);
  byId('reachTitle').textContent = isRange ? '所选时段阅读 / 播放' : '近七天阅读 / 播放';
  const dayMs = 86400000;
  const endTime = Date.parse(date);
  const startTime = isRange ? Date.parse(start) : endTime - 6 * dayMs;
  const length = Math.round((endTime - startTime) / dayMs) + 1;
  const span = Math.ceil(length / 7);
  const buckets = Array.from({length: Math.ceil(length / span)}, () => ({views: 0, days: 0}));
  for (const point of report.trend) {
    const bucket = buckets[Math.floor((Date.parse(point.day) - startTime) / (span * dayMs))];
    if (bucket) { bucket.views += point.views; bucket.days++; }
  }
  byId('reachHint').textContent = span === 1 ? '每日已报账号合计 · — 表示当天无日报' : `每 ${span} 天一组 · 仅合计已报数据`;
  const peak = Math.max(1, ...buckets.map(bucket => bucket.views));
  trend.style.gridTemplateColumns = `repeat(${buckets.length}, minmax(0,1fr))`;
  buckets.forEach((bucket, index) => {
    const first = new Date(startTime + index * span * dayMs).toISOString().slice(0, 10);
    const last = new Date(Math.min(endTime, startTime + ((index + 1) * span - 1) * dayMs)).toISOString().slice(0, 10);
    const column = element('div');
    const bar = element('i');
    bar.style.height = (bucket.days ? Math.max(2, bucket.views / peak * 54) : 1) + 'px';
    column.title = `${first} 至 ${last} · ${bucket.days} 天有记录`;
    column.append(element('b', bucket.days ? bucket.views.toLocaleString() : '—'), bar, element('small', first.slice(5)));
    if (first !== last) column.append(element('small', '至 ' + last.slice(5)));
    trend.append(column);
  });
  const recorded = rows.filter(row => row.followers !== null);
  if (coverage.missing?.length) {
    const missing = coverage.missing;
    unreported.querySelector('summary').textContent = `应报未填（${missing.length} 份）· 选择补填`;
    for (const row of missing) {
      const fill = element('button', row.day + ' · ' + row.platform + ' · ' + row.account, 'ghost');
      fill.type = 'button';
      fill.onclick = () => {editReport(row); byId('reportForm').elements.day.value = row.day;};
      shortcuts.append(fill);
    }
    unreported.hidden = false;
  }
  metrics.replaceChildren();
  byId('homeIntroText').textContent = !rows.length
    ? ''
    : recorded.length && isRange ? `此时段有 ${recorded.length} 个账号上报。粉丝取各账号时段内最后一次记录，其余指标按天累加。`
    : recorded.length ? `${rows.length} 个运营账号 · ${recorded.length} 个已报`
    : `${rows.length} 个运营账号 · 所选日期暂无日报`;
  if (rows.length) {
    byId('homeIntroText').append(' · 实报 ' + coverage.actual.reduce((sum,row) => sum + row.reports, 0) + ' 个账号日（仅含本项目保留的账号与日报）');
    if (coverage.expected && !coverage.unknown) byId('homeIntroText').append(' · 日报覆盖 ' + coverage.reported + '/' + coverage.expected + ' 个账号日');
    if (coverage.unknown) byId('homeIntroText').append(' · ' + coverage.unknown + ' 个账号应报期间未知');
    if (coverage.limited) byId('homeIntroText').append(' · 此范围仅显示实报数，覆盖与同期比较支持最近选择的 366 天以内区间');
  }
  for (const [key, label] of Object.entries(accountFields)) {
    const card = element('section');
    const total = recorded.reduce((sum, row) => sum + row[key], 0);
    card.append(element('h2', label), element('strong', recorded.length ? total.toLocaleString() : '—'));
    if (key === 'followers' && recorded.length && recorded.every(row => row.growth !== null)) {
      const growth = recorded.reduce((sum, row) => sum + row.growth, 0);
      card.append(element('small', (isRange ? '较开始日前一天 ' : '较前日 ') + (growth > 0 ? '+' : '') + growth.toLocaleString()));
    }
    metrics.append(card);
    if (key !== 'followers' && coverage.samples) {
      const delta = coverage.differences[key];
      const prior = coverage.baseline[key];
      const percent = prior ? '（' + (delta / prior).toLocaleString('zh-CN', {style:'percent',maximumFractionDigits:1}) + '）' : '（基期为零）';
      card.append(element('small','较上个等长时段 ' + (delta > 0 ? '+' : '') + delta.toLocaleString() + percent + ' · ' + coverage.samples + ' 个可比账号'));
    } else if (key !== 'followers' && recorded.length) card.append(element('small',coverage.limited ? '区间超出比较范围' : '缺基期或应报不完整 · 0 个可比账号'));
  }
  const groups = new Map();
  for (const row of rows) {
    const brand = brandFor(row.platform);
    if (!groups.has(brand.name)) groups.set(brand.name, {brand, accounts: []});
    groups.get(brand.name).accounts.push(row);
  }
  for (const brand of platformBrands) {
    if (groups.has(brand.name)) list.append(platformCard(brand, groups.get(brand.name).accounts, isRange));
    groups.delete(brand.name);
  }
  if (groups.size) {
    const legacy = element('details', '', 'legacyPlatforms');
    legacy.append(element('summary', '其他已登记平台'));
    for (const {brand, accounts} of groups.values()) legacy.append(platformCard(brand, accounts, isRange));
    list.append(legacy);
  }
}
