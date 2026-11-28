const reportLabels = {platform:'平台', account:'账号', day:'统计日期', followers:'粉丝数', views:'阅读 / 播放', engagements:'互动数', published:'发布数'};
let savedConnectors = {};
let connectorRevision = 0;
let previewId = '';

function resetConnectorPreview() {
  connectorRevision++;
  previewId = '';
  byId('connectorResult').textContent = '';
  byId('connectorImport').disabled = true;
}

function showConnectorForm() {
  resetConnectorPreview();
  const kind = byId('connectorKind').value;
  byId('connectorTitle').textContent=kind==='reports'?'日报导入接口':'审核稿发送接口';
  byId('connectorPurpose').textContent=kind==='reports'?'从已有统计系统读取粉丝、播放、互动与发布数据，导入后在「账号总览」查看。':'将审核通过的稿件发送给你指定的接收系统。保存配置不会发送，发送前需在稿件中预览并确认。';
  const config = savedConnectors[kind];
  const fields = byId('connectorForm').elements;
  for (const key of ['url','token_alias','records_path','next_path']) fields[key].value = config?.[key] || '';
  for (const key of Object.keys(reportLabels)) fields['map_' + key].value = config?.mapping?.[key] || key;
  byId('connectorMapping').hidden = kind !== 'reports';
  byId('webhookHelp').hidden = kind !== 'drafts';
  byId('reportConnectActions').hidden = kind !== 'reports';
  byId('connectorPreview').disabled = !config;
  byId('connectorState').textContent = config ? (config.status || '连接已保存，尚无执行记录') : '尚未配置';
}

async function loadConnectorSettings() {
  const id = projectId;
  savedConnectors = {};
  showConnectorForm();
  const revision = connectorRevision;
  const data = await api('/api/projects/' + id + '/connectors');
  if (projectId === id && !byId('settings').hidden) {
    savedConnectors = data;
    if (revision === connectorRevision) showConnectorForm();
  }
}

function initializeConnectors() {
  for (const [key,title] of Object.entries(reportLabels)) {
    const label = element('label',title);
    const input = element('input');
    input.name = 'map_' + key;
    input.maxLength = 200;
    label.append(input);
    byId('reportMappingFields').append(label);
  }
  byId('connectorKind').onchange = showConnectorForm;
  const form = byId('connectorForm');
  form.oninput = () => {
    resetConnectorPreview();
    byId('connectorPreview').disabled = true;
    byId('connectorState').textContent = '有未保存的修改';
  };
  form.onsubmit = handle(async () => {
    const id = projectId;
    const kind = byId('connectorKind').value;
    const config = {};
    for (const key of ['url','token_alias','records_path','next_path']) config[key] = form.elements[key].value.trim();
    config.mapping = Object.fromEntries(Object.keys(reportLabels).map(key => [key,form.elements['map_' + key].value.trim()]));
    await api('/api/projects/' + id + '/connectors/' + kind,'PUT',config);
    if (id === projectId && !byId('settings').hidden) await loadConnectorSettings();
  });
  const path = () => '/api/projects/' + projectId + '/connectors/reports/';
  byId('connectorPreview').onclick = handle(async () => {
    resetConnectorPreview();
    const revision = connectorRevision;
    const id = projectId;
    const result = await api(path() + 'preview','POST');
    if (revision !== connectorRevision || id !== projectId || byId('settings').hidden) return;
    previewId = result.preview_id;
    byId('connectorResult').textContent = '共 ' + result.count + ' 条。导入时覆盖同平台、账号和日期的记录。\n' + JSON.stringify(result.sample,null,2);
    byId('connectorImport').disabled = !result.count;
  });
  byId('connectorImport').onclick = handle(async () => {
    if (!previewId) return;
    const id = projectId;
    const result = await api(path() + 'import','POST',{preview_id:previewId});
    if (id !== projectId || byId('settings').hidden) return;
    await loadConnectorSettings();
    notify('已导入 ' + result.count + ' 条日报。','ok');
    const open=element('button','查看本次日报','secondary');open.type='button';
    open.onclick=handle(async()=>{if(id!==projectId)return;if(result.latest)selectReportDay(result.latest);await home();});
    byId('connectorState').append(document.createElement('br'),open);
  });
}

function attachDraftWebhook(card, runId, version, approved) {
  const fold = element('details','','workFold');
  fold.append(element('summary','发送到第三方接口'));
  const setup = element('button','配置稿件接收接口','ghost');
  setup.onclick = handle(async () => {
    await openConfigSection('draftConnector');
  });
  const preview = element('button','预览发送内容');
  const output = element('pre','','resultBox');
  const send = element('button','确认发送此版本','primary');
  send.hidden = true;
  const base = '/api/runs/' + runId + '/webhook/' + version + '/';
  const history = element('div');
  const reload = element('button', '刷新投递历史', 'ghost');
  let historyRevision = 0;
  async function loadHistory() {
    const revision = ++historyRevision;
    const rows = await api('/api/runs/' + runId + '/webhook-receipts');
    if (revision !== historyRevision || !fold.isConnected) return;
    history.replaceChildren(...rows.map(row => {
      const item = element('details');
      item.append(element('summary', row.started + ' UTC · 版本 ' + row.version + ' · ' + row.state),
        element('p', row.target + ' · ' + row.detail), element('p', '接收端核对键 ' + row.receipt),
        element('pre', JSON.stringify(row.approval, null, 2)));
      return item;
    }));
    if (!rows.length) history.textContent = '暂无投递记录';
  }
  reload.onclick = handle(loadHistory);
  fold.ontoggle = handle(async () => { if (fold.open) await loadHistory(); });
  preview.hidden = !approved;
  preview.onclick = handle(async () => {
    send.hidden = true;
    const result = await api(base + 'preview','POST');
    output.textContent = result.target + '\n' + result.state + '\n' + JSON.stringify(result.payload,null,2);
    if (result.checked_attempt) output.textContent += '\n请先在接收端按此键核对：' + result.preview_id;
    send.textContent = result.checked_attempt ? '已核对接收端，确认需要重试' : '确认发送此版本';
    send.hidden = ['发送中','已送达'].includes(result.state);
    send.onclick = handle(async () => {
      send.hidden = true;
      preview.disabled = true;
      try {
        const receipt = await api(base + 'send','POST',{preview_id:result.preview_id, checked_attempt:result.checked_attempt});
        output.textContent = receipt.message;
      } catch (error) {
        output.textContent = error.message;
      } finally {
        preview.disabled = false;
        await loadHistory();
      }
    });
  });
  fold.append(setup,preview,output,send,reload,history);
  card.append(fold);
}
