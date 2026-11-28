/* Manual archival only. The host calls draftArchive.attach after rendering stage 5. */
(() => {
  'use strict';
  const settings = new Map();
  const mounts = new WeakMap();
  const node = (tag, text, className) => {
    const item = document.createElement(tag);
    if (text) item.textContent = text;
    if (className) item.className = className;
    return item;
  };
  async function request(path, options) {
    const response = await fetch(path, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : '归档信息读取失败，请稍后重试');
    return data;
  }
  function defaultLibrary(project) {
    if(typeof currentProject === 'function' && currentProject().id === project)
      return currentProject().settings?.archive_library_id;
    return settings.get(project)?.archive_library_id;
  }
  function safeLink(value) {
    try {
      const url = new URL(value);
      return url.protocol === 'https:' && !url.username && !url.password && !url.port
        && (/^(?:[a-z0-9-]+\.)*(?:notion\.so|notion\.site)$/.test(url.hostname)
          ? /^\/[A-Za-z0-9_/-]+$/.test(url.pathname) && !url.search && !url.hash
          : /^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.feishu\.cn$/.test(url.hostname)
            && /^\/base\/[A-Za-z0-9]+$/.test(url.pathname)
            && /^\?table=tbl[A-Za-z0-9]+&record=rec[A-Za-z0-9]+$/.test(url.search) && !url.hash);
    } catch (_) { return false; }
  }
  function attach(container, run) {
    mounts.get(container)?.remove();
    mounts.delete(container);
    if (run.stage !== 5) return;
    const project = run.project_id;
    const version = run.payload?.drafts?.length || 0;
    const review = run.payload?.reviews?.at(-1);
    const approved = version > 0 && review?.version === version && review?.decision === '通过';
    const fold = node('details', '', 'workFold');
    mounts.set(container, fold);
    fold.append(node('summary', '归档到线上资料库'));
    fold.append(node('p', '使用项目已保存的 Notion／飞书连接。读取和写入权限需要分别授予；保存连接或选择目标不会写入远端，只有点击归档按钮才会写入。'));
    fold.append(node('p', approved
      ? `将归档已保存且审核通过的版本 ${version}；编辑后请先保存并重新审核。`
      : '当前版本尚未审核通过。请返回“审核稿件”，审核最新保存版本并选择“通过”，再打开此面板。'));
    const label = node('label', '目标资料库');
    const select = node('select');
    select.append(new Option('展开面板后读取项目资料库', ''));
    select.disabled = true;
    label.append(select);
    const actions = node('div', '', 'formActions');
    const button = node('button', '归档当前审核版本', 'secondary');
    button.type = 'button';
    button.disabled = true;
    const retry = node('button', '重新读取资料库', 'secondary');
    retry.type = 'button';
    retry.hidden = true;
    const output = node('div', '', 'resultBox');
    output.setAttribute('role', 'status');
    output.setAttribute('aria-live', 'polite');
    actions.append(button, retry);
    const history = node('div');
    const recover = node('button', '从 Notion 备份快照恢复为新批次', 'ghost');
    recover.type = 'button';
    recover.onclick = handle(() => openConfigSection('restorePage'));
    fold.append(node('p', '采集管理中的“导入”将远端资料读入本地。本页将已审核稿件写入选定资料库。'), label, actions, output, history, recover);
    container.append(fold);
    let loaded = false, loading = false, sending = false, receipts = [];
    const active = () => mounts.get(container) === fold && fold.isConnected;
    const selectedReceipt = () => receipts.find(item => item.run_id === run.id
      && item.version === version && item.library_id === Number(select.value));
    function render(receipt = selectedReceipt()) {
      output.replaceChildren();
      history.replaceChildren(...receipts.filter(item => item.run_id === run.id).map(item => {
        const target = item.target_name || item.provider;
        return node('p', item.updated_at + ' UTC · ' + target + ' #' + item.library_id + ' · 版本 ' + item.version + ' · ' + (item.message || '发送中'));
      }));
      if (receipt) {
        const messages = {
          sent: '已归档当前审核版本。',
          sending: '归档请求已登记，发送结果尚未确认。请先到远端核实；已阻止重复发送。',
          uncertain: '发送结果不确定，请先到远端核实是否已创建。已阻止重复发送，请勿盲目重试。',
          failed: '归档失败。请检查目标字段及独立的写入权限后重试，也可导出 Markdown 手动归档。'
        };
        output.append(node('p', messages[receipt.state] || '请检查归档回执。'));
        if (receipt.state === 'sent' && safeLink(receipt.remote_url)) {
          const link = node('a', '打开已归档稿件', 'download');
          link.href = receipt.remote_url;
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
          output.append(link);
        }
      }
      button.disabled = !approved || !loaded || !select.value || sending
        || !!(receipt && receipt.state !== 'failed');
    }
    async function load() {
      if (loading) return;
      loading = true;
      retry.hidden = true;
      output.textContent = '正在读取项目资料库与归档回执…';
      try {
        const base = '/api/projects/' + encodeURIComponent(project);
        const [libraries, history] = await Promise.all([
          request(base + '/online-libraries'), request(base + '/draft-archives')
        ]);
        if (!active()) return;
        receipts = history;
        const previous = select.value || String(defaultLibrary(project) || '');
        select.replaceChildren(new Option(libraries.length ? '请选择归档目标' : '尚未保存线上资料库，请到「采集管理」的「资料库」添加连接', ''));
        for (const library of libraries) {
          select.append(new Option(`${library.name} · ${library.provider === 'notion' ? 'Notion' : '飞书'}`, String(library.id)));
        }
        if (libraries.some(item => String(item.id) === previous)) select.value = previous;
        select.disabled = !libraries.length;
        loaded = true;
        render();
        if (!libraries.length) {
          const manage = node('button', '添加资料库连接', 'secondary');
          manage.type = 'button';
          manage.onclick = handle(() => openConfigSection('onlineLibraries'));
          output.append(manage);
        }
      } catch (error) {
        if (!active()) return;
        loaded = false;
        button.disabled = true;
        output.textContent = error.message;
        retry.hidden = false;
      } finally { loading = false; }
    }
    fold.addEventListener('toggle', () => { if (fold.open && !sending) load(); });
    retry.onclick = load;
    select.onchange = () => render();
    button.onclick = async () => {
      if (button.disabled || sending) return;
      sending = true;
      button.disabled = true;
      select.disabled = true;
      output.textContent = '正在归档当前审核版本，请等待回执…';
      try {
        const receipt = await request('/api/runs/' + encodeURIComponent(run.id) + '/archive', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({version, library_id: Number(select.value)})
        });
        if (!active()) return;
        receipts = [receipt, ...receipts.filter(item => item.id !== receipt.id)];
        sending = false;
        render(receipt);
      } catch (error) {
        if (!active()) return;
        // A lost browser response may follow a committed write. Read durable
        // receipts before allowing the operator to initiate another request.
        loaded = false;
        output.textContent = error.message + '。请重新读取回执；发送结果未确认前，请先核实远端。';
        retry.hidden = false;
      } finally {
        sending = false;
        if (active()) select.disabled = false;
      }
    };
  }
  window.draftArchive = {
    attach,
    refreshSettings(project, values) {
      if (project && typeof project === 'object') {
        values = project.settings;
        project = project.id;
      }
      if (values) settings.set(Number(project), values);
      else settings.delete(Number(project));
    }
  };
})();
