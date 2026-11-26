const accountFields = {followers: '期末粉丝', views: '阅读 / 播放', engagements: '互动次数', published: '发布数'};
const platformBrands = [
  {name: '微信公众号', aliases: ['公众号','微信公众平台','wechat'], icon: 'wechat', color: '#087746', tint: '#eff9f3'},
  {name: '小红书', aliases: ['xhs','rednote'], icon: 'xhs', color: '#b22846', tint: '#fff1f2'},
  {name: '抖音', aliases: ['douyin'], icon: 'douyin', color: '#333640', tint: '#f1f2f5'},
  {name: '哔哩哔哩', aliases: ['b站','bilibili'], icon: 'bilibili', color: '#12779a', tint: '#edf8fc'},
  {name: 'YouTube', aliases: ['油管'], icon: 'youtube', color: '#b52826', tint: '#fff3ef'}
];

function brandFor(name) {
  const key = name.trim().toLowerCase();
  return platformBrands.find(brand => brand.name.toLowerCase() === key || brand.aliases.includes(key))
    || {name, color: '#54617c', tint: '#f1f4fa'};
}

function brandIcon(brand) {
  if (!brand.icon) return element('span', Array.from(brand.name)[0], 'brandFallback');
  const icon = element('img');
  icon.src = '/icons/' + brand.icon + '.svg';
  icon.alt = '';
  icon.width = icon.height = 38;
  return icon;
}

function platformCard(brand, accounts) {
  const card = element('section', '', 'platformCard');
  card.style.setProperty('--brand', brand.color);
  card.style.setProperty('--tint', brand.tint);
  const reported = accounts.filter(row => row.views !== null);
  const total = key => reported.length ? reported.reduce((sum, row) => sum + row[key], 0).toLocaleString() : '—';
  const head = element('header');
  head.append(brandIcon(brand), element('h3',brand.name), element('small', reported.length + '/' + accounts.length + ' 已报'));
  const reach = element('div','','platformReach');
  const reachLabel = brand.icon === 'wechat' ? '阅读量' : ['douyin','youtube','bilibili'].includes(brand.icon) ? '播放量' : '阅读 / 播放';
  reach.append(element('small',reachLabel),element('strong',total('views')));
  const stats = element('div','','platformStats');
  for (const [key,label] of [['followers','粉丝'],['engagements','互动'],['published','发布']]) {
    const metric = element('div');
    metric.append(element('small',label),element('b',total(key)));
    stats.append(metric);
  }
  const details = element('details');
  details.append(element('summary',accounts.length === 1 ? accounts[0].account : '查看 ' + accounts.length + ' 个账号'));
  for (const row of accounts) {
    const entry = element('div','','accountEntry');
    const edit = element('button',row.views === null ? '补录' : '编辑','ghost');
    edit.onclick = () => editReport(row);
    entry.append(element('b',row.account),edit,element('small',Object.entries(accountFields).map(([key,label]) => label + ' ' + (row[key]?.toLocaleString() ?? '—')).join(' · ')));
    details.append(entry);
  }
  const setup = element('button','接入设置','goLink');
  setup.type = 'button';
  setup.onclick = () => openPlatformSetup(accounts[0].platform, accounts.length === 1 ? accounts[0].account : '');
  const actions = element('div','','platformActions');
  actions.append(details, setup);
  card.append(head,reach,stats,actions);
  return card;
}

let reportRequest = 0;
let knownAccounts = [];

function openPlatformSetup(platform = '', account = '') {
  const form = byId('platformSetup');
  form.reset();
  form.elements.platform.value = platform;
  form.elements.account.value = account;
  form.elements.platform.dispatchEvent(new Event('input'));
  form.elements.mode.dispatchEvent(new Event('change'));
  byId('reportForm').hidden = true;
  form.hidden = false;
  form.elements[platform ? 'account' : 'platform'].focus();
}


function initializeAccounts() {
  const form = byId('platformSetup');
  byId('platformSetupBtn').onclick = () => openPlatformSetup();
  byId('closePlatformSetup').onclick = () => { form.hidden = true; };
  byId('dailyTemplate').href = 'data:text/csv;charset=utf-8,' + encodeURIComponent('\uFEFF日期,粉丝,阅读或播放,互动,发布\r\n');
  form.elements.platform.oninput = () => {
    const names = knownAccounts.filter(row => row.platform === form.elements.platform.value);
    byId('knownPlatformAccounts').replaceChildren(...names.map(row => new Option(row.account)));
  };
  form.elements.mode.onchange = () => {
    const csv = form.elements.mode.value === 'csv';
    byId('reportUpload').hidden = !csv;
    form.elements.file.required = csv;
    byId('platformSave').textContent = csv ? '导入日报' : '保存账号';
  };
  form.onsubmit = handle(async () => {
    const id = projectId;
    const values = {platform:form.elements.platform.value.trim(), account:form.elements.account.value.trim()};
    const endpoint = '/api/projects/' + id;
    let message = '已登记账号，可在平台卡片中填写日报。';
    if (form.elements.mode.value === 'csv') {
      const file = form.elements.file.files[0];
      if (file.size > 250000) throw Error('请选择小于 250 KB 的日报 CSV');
      const content = await file.text();
      const result = await api(endpoint + '/account-import','POST',{...values, content});
      if (id !== projectId || byId('overview').hidden) return;
      byId('accountDate').value = result.latest;
      message = '已导入 ' + result.count + ' 天的日报。';
    } else await api(endpoint + '/platform-accounts','PUT',values);
    if (id !== projectId || byId('overview').hidden) return;
    await home();
    notify(message,'ok');
  });
  const picker = byId('accountDate');
  const now = new Date();
  picker.value = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  picker.onchange = handle(home);
  byId('addReport').onclick = () => editReport();
  byId('closeReport').onclick = () => { byId('reportForm').hidden = true; };
  byId('reportForm').onsubmit = handle(async () => {
    const id = projectId;
    const values = Object.fromEntries(new FormData(byId('reportForm')));
    await api('/api/projects/' + id + '/account-daily', 'PUT', values);
    if (id !== projectId || byId('overview').hidden) return;
    picker.value = values.day;
    await home();
    notify('账号日报已保存', 'ok');
  });
}

function editReport(report = {}) {
  const form = byId('reportForm');
  byId('platformSetup').hidden = true;
  form.reset();
  form.elements.day.value = byId('accountDate').value;
  for (const key of ['account','platform', ...Object.keys(accountFields)]) {
    form.elements[key].value = report[key] ?? '';
  }
  form.hidden = false;
  form.elements.platform.focus();
}

async function home() {
  const id = projectId;
  const token = ++reportRequest;
  view('overview');
  knownAccounts = [];
  byId('platformSetup').hidden = true;
  byId('reportForm').hidden = true;
  const date = byId('accountDate').value;
  const metrics = byId('accountMetrics');
  const list = byId('accountRows');
  metrics.replaceChildren();
  list.textContent = date ? '正在读取账号日报…' : '请选择统计日期。';
  byId('accountScope').textContent = currentProject().name;
  if (!date) return;
  byId('reachPanel').hidden = true;
  const [report] = await Promise.all([api('/api/projects/' + id + '/account-daily?day=' + date), historyList()]);
  if (token !== reportRequest || id !== projectId || byId('overview').hidden) return;
  list.replaceChildren();
  const rows = report.accounts;
  knownAccounts = rows;
  const trend = byId('reachTrend');
  trend.replaceChildren();
  byId('reachPanel').hidden = !rows.length;
  const peak = Math.max(1, ...report.trend.map(point => point.views));
  for (let offset = 6; offset >= 0; offset--) {
    const day = new Date(Date.parse(date) - offset * 86400000).toISOString().slice(0, 10);
    const point = report.trend.find(item => item.day === day);
    const column = element('div');
    const bar = element('i');
    bar.style.height = (point ? Math.max(2, point.views / peak * 54) : 1) + 'px';
    column.title = day + (point ? ' · ' + point.reported + ' 个账号已报' : ' · 未记录');
    column.append(element('b', point ? point.views.toLocaleString() : '—'), bar, element('small', day.slice(5)));
    trend.append(column);
  }
  const recorded = rows.filter(row => row.followers !== null);
  byId('accountScope').textContent = currentProject().name + ' · 当日已记录 ' + recorded.length + ' / ' + rows.length + ' 个账号';
  for (const [key, label] of Object.entries(accountFields)) {
    const card = element('section');
    const total = recorded.reduce((sum, row) => sum + row[key], 0);
    card.append(element('h2', label), element('strong', recorded.length ? total.toLocaleString() : '—'));
    if (key === 'followers' && recorded.length && recorded.every(row => row.growth !== null)) {
      const growth = recorded.reduce((sum, row) => sum + row.growth, 0);
      card.append(element('small', '较前日 ' + (growth > 0 ? '+' : '') + growth.toLocaleString()));
    } else card.append(element('small', recorded.length ? '已报账号合计' : '等待日报'));
    metrics.append(card);
  }
  if (!rows.length) {
    const empty = element('section', '', 'platformEmpty');
    empty.append(element('h3', '还没有账号日报'), element('p', '选择平台，录入第一份账号日报。'));
    const choices = element('div', '', 'platformChoices');
    for (const brand of platformBrands) {
      const button = element('button');
      button.append(brandIcon(brand), element('span',brand.name));
      button.onclick = () => editReport({platform:brand.name});
      choices.append(button);
    }
    empty.append(choices);
    list.append(empty);
  }
  const groups = new Map();
  for (const row of rows) {
    const brand = brandFor(row.platform);
    if (!groups.has(brand.name)) groups.set(brand.name, {brand, accounts: []});
    groups.get(brand.name).accounts.push(row);
  }
  for (const {brand, accounts} of groups.values()) list.append(platformCard(brand, accounts));
}
