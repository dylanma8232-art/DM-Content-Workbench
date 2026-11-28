(() => {
  const form = byId('collectionTaskForm');
  const fields = form.elements;
  const list = byId('taskList');
  const notice = byId('taskNotice');
  const platformNames = [['xiaohongshu', '小红书'], ['bilibili', 'B站'], ['douyin', '抖音'], ['instagram', 'Instagram'], ['tiktok', 'TikTok'], ['x', 'X']];
  let session;

  function current(ctx) {
    return session === ctx && ctx.project === projectId && !byId('channels').hidden;
  }

  function updateQueryLabel() {
    const topic = fields.mode.value === 'topic';
    byId('taskQueryLabel').textContent = topic ? '话题关键词' : '来源作者账号或主页链接';
    const examples = {instagram: '账号名，例如 @instagram', tiktok: '账号名，例如 @tiktok', x: '账号名，例如 @OpenAI', bilibili: 'UP 主 UID 或 space.bilibili.com 主页', douyin: '粘贴 www.douyin.com/user/… 主页', xiaohongshu: '粘贴小红书 user/profile/… 主页'};
    fields.query.placeholder = topic ? '例如 AI科普、旅行攻略、摄影教程' : examples[fields.platform.value] || '填写账号标识或官方主页';
    fields.query.setCustomValidity('');
  }

  function assistantReady() {
    const local = location.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(location.hostname);
    if (local && document.documentElement.dataset.dmCollectExtension === '1') return true;
    const guide = byId('collectionGuide');
      guide.open = true;
    notice.textContent = local ? '未检测到采集助手。请按安装步骤加载扩展，刷新工作台后再继续。' : '请在运行工作台的电脑上，通过 HTTP localhost 或 127.0.0.1 打开同一工作台，再继续此任务。';
    guide.querySelector('summary').focus();
    return false;
  }

  function button(text, action, className = 'secondary') {
    const control = element('button', text, className);
    control.type = 'button';
    control.onclick = action;
    return control;
  }

  function settingsForm(ctx, task) {
    const draft = ctx.edit;
    const editor = element('form');
    editor.setAttribute('aria-label', `修改「${task.query}」采集设置`);
    const collector = element('label', '采集账号备注（选填）');
    const labelInput = element('input');
    Object.assign(labelInput, {name: 'collector_label', value: draft.collector_label, maxLength: 256, disabled: ctx.busy.has(task.id)});
    labelInput.oninput = () => { draft.collector_label = labelInput.value; };
    collector.append(labelInput);
    editor.append(collector);
    for (const [key, label, min, max] of [['limit', '每次最多采集（条）', 1, 50], ['minutes', '更新周期（分钟，0 为手动）', 0, 43200]]) {
      const field = element('label', label);
      const input = element('input');
      Object.assign(input, {name: key, type: 'number', min, max, required: true, value: draft[key]});
      input.disabled = ctx.busy.has(task.id);
      input.oninput = () => { draft[key] = input.value; };
      field.append(input); editor.append(field);
    }
    editor.append(element('p', '修改关键词、账号或平台，请在上方添加新任务；旧任务可改为手动采集，历史素材可继续查看。更改条数或周期后，已打开的浏览器采集需重新发起。', 'fieldHelp'));
    const save = element('button', '保存采集设置', 'primary');
    save.type = 'submit';
    const cancel = button('取消修改', () => { ctx.edit = null; render(ctx); }, 'ghost');
    save.disabled = cancel.disabled = ctx.busy.has(task.id);
    editor.append(save, cancel);
    editor.onsubmit = async event => {
      event.preventDefault();
      if (!current(ctx) || ctx.busy.has(task.id)) return;
      ctx.busy.add(task.id); render(ctx);
      try {
        const updated = await api(`${ctx.path}/${task.id}`, 'PUT', {limit: Number(draft.limit), minutes: Number(draft.minutes), collector_label: draft.collector_label.trim()});
        if (!current(ctx)) return;
        ctx.tasks = ctx.tasks.map(row => row.id === updated.id ? updated : row);
        ctx.edit = null;
        notice.textContent = '采集设置已保存，历史批次可继续查看。';
        await refresh(ctx);
      } catch (error) {
        if (current(ctx)) notice.textContent = error.message;
      } finally {
        ctx.busy.delete(task.id);
        if (current(ctx)) render(ctx);
      }
    };
    return editor;
  }

  function render(ctx) {
    const focused = document.activeElement;
    const taskId = focused.closest('#taskList article')?.dataset.taskId;
    const pagerFocus = focused.closest('#taskList nav') && focused.textContent;
    list.replaceChildren();
    if (!ctx.tasks.length) list.append(element('p', '当前项目还没有平台采集任务。', 'cardHint'));
    for (const task of ctx.tasks) {
      const row = element('article', '', 'sourceItem');
      row.dataset.taskId = task.id;
      const platform = platformNames.find(([id]) => id === task.platform)?.[1] || task.platform;
      const running = task.status === 'reading' && task.lease_until * 1000 > Date.now();
      const statuses = {ready: '等待采集', reading: running ? '正在提取与解析' : '上次执行已超时，可重新采集', browser: '需要平台登录或浏览器继续', completed: '本轮已完成', failed: '采集失败'};
      row.append(element('h3', `${platform} · ${task.mode === 'topic' ? '话题' : '来源作者'}：${task.query}`),
        element('p', task.collector_label ? '采集账号备注：' + task.collector_label : '采集账号：浏览器当前登录的账号', 'cardHintInline'),
        element('p', `${task.mode === 'topic' ? '话题' : '账号'} · 每次最多 ${task.limit} 条 · ${task.minutes ? (task.minutes % 60 ? `每 ${task.minutes} 分钟` : `每 ${task.minutes / 60} 小时`) : '手动采集'}`, 'cardHintInline'),
        element('p', `最近采集：${statuses[task.status] || '等待采集'}${task.message ? ' · ' + task.message : ''}`, 'sourceStatus'));
      const actions = element('div', '', 'formActions');
      const receipt = task.result;
      if (receipt?.received != null) row.append(element('p',
        `读取 ${receipt.received} · 本批收录 ${receipt.imported} · 来源新增 ${receipt.source_added} / 更新 ${receipt.source_updated} / 重复 ${receipt.source_duplicates ?? receipt.duplicates} · 跳过低质量 ${receipt.skipped_quality ?? 0} · 当前缺项 ${task.batch_partial}`, 'cardHintInline'));
      const needsBrowser = task.status === 'browser';
      if (task.minutes && task.due) {
        const schedule = needsBrowser ? '本轮等待你在扩展中确认。' : '下次计划采集：' + new Date(task.due * 1000).toLocaleString('zh-CN', {hour12:false}) + '（到期后请用扩展继续）';
        row.append(element('p', schedule, 'cardHintInline'));
      }
      const run = button(needsBrowser ? '去登录并继续' : '立即采集', () => {
        if (!current(ctx)) return;
        if (needsBrowser) {
          if (!assistantReady()) return;
          window.postMessage({type: 'dm-collect-open', project: ctx.project, source: task.id}, location.origin);
          notice.textContent = '已请求打开平台页面。请核对当前登录账号，在目标页打开 DM 发布助手并确认采集。';
        } else mutate(ctx, task, false);
      });
      const remove = button('删除任务', () => {
        if (current(ctx) && window.confirm(`删除「${task.query}」采集任务？`)) mutate(ctx, task, true);
      }, 'ghost');
      remove.disabled = run.disabled = ctx.busy.has(task.id) || running;
      run.dataset.operation = 'run';
      actions.append(run);
      if (task.last_run_id) {
        const retry = button('重新解析上一批素材', () => mutate(ctx, task, false, 'reprocess'), 'ghost');
        retry.disabled = ctx.busy.has(task.id) || running;
        retry.dataset.operation = 'reprocess';
        actions.append(retry);
      }
      const batch = task.run_id;
      if (batch) actions.append(button('查看采集结果', () => {
        if (current(ctx)) window.openLibrary('materials', {run_id: batch});
      }, 'ghost'));
      if (batch && task.batch_partial > 0) actions.append(button('查看缺项素材（' + task.batch_partial + '）', () => {
        if (current(ctx)) window.openLibrary('materials', {run_id: batch, partial_only: true});
      }, 'ghost'));
      const edit = button('修改采集设置', () => {
        ctx.edit = {id: task.id, limit: task.limit, minutes: task.minutes, collector_label: task.collector_label || ''};
        render(ctx);
        list.querySelector('form input')?.focus();
      }, 'ghost');
      edit.disabled = ctx.busy.has(task.id) || !!ctx.edit || task.lease_until * 1000 > Date.now();
      actions.append(edit, remove);
      row.append(actions);
      if (ctx.edit?.id === task.id) row.append(settingsForm(ctx, task));
      list.append(row);
      if (row.dataset.taskId === taskId) {
        const next = [...row.querySelectorAll('input,button')].find(control => control.tagName === focused.tagName &&
          (focused.name ? control.name === focused.name : control.textContent === focused.textContent));
        next?.focus({preventScroll:true});
      }
    }
    listPager(list, ctx.offset, ctx.total, offset => refresh(ctx, offset, null));
    if (pagerFocus) [...list.querySelectorAll('nav button:not(:disabled)')].find(control => control.textContent === pagerFocus)?.focus({preventScroll:true});
  }

  async function refresh(ctx, offset = ctx.offset || 0, taskId = ctx.edit?.id || document.activeElement.closest('#taskList article')?.dataset.taskId) {
    const version = ++ctx.version;
    try {
      const params = new URLSearchParams({limit:listPageSize, offset});
      if (taskId) params.set('task_id',taskId);
      const data = await api(ctx.path + '?' + params);
      if (!current(ctx) || ctx.version !== version) return;
      ctx.tasks = data.tasks;
      ctx.offset = data.offset;
      ctx.total = data.total;
      byId('refreshTasks').hidden = !ctx.tasks.length;
      if (ctx.edit && !ctx.tasks.some(task => task.id === ctx.edit.id)) ctx.edit = null;
      ctx.platforms = data.platforms;
      syncPlatformPicker(form);
      for (const control of form.querySelectorAll('[data-platform]')) {
        control.disabled = !ctx.platforms.some(item => item.id === control.dataset.platform);
      }
      updateQueryLabel();
      form.querySelector('button[type=submit]').disabled = ctx.creating || !ctx.platforms.length;
      render(ctx);
      if (notice.textContent === '正在加载任务…') notice.textContent = '';
    } catch (error) {
      if (current(ctx) && ctx.version === version) {
        byId('refreshTasks').hidden = false;
        notice.textContent = `任务加载失败：${error.message}。请刷新任务重试。`;
      }
    }
  }

  async function mutate(ctx, task, deleting, operation = 'run') {
    if (!current(ctx) || ctx.busy.has(task.id)) return;
    ctx.busy.add(task.id);
    notice.textContent = deleting ? '正在删除任务…' : operation === 'reprocess' ? '正在补齐已采素材的解析，原素材会保留。' : '正在准备本轮扩展采集…';
    render(ctx);
    const path = `${ctx.path}/${encodeURIComponent(task.id)}`;
    try {
      const result = await api(deleting ? path : `${path}/${operation}`, deleting ? 'DELETE' : 'POST');
      if (!current(ctx)) return;
      if (deleting) notice.textContent = '任务已删除。';
      else if (result.in_progress) notice.textContent = result.message;
      else if (result.browser) {
        notice.textContent = '请点击本任务的「去登录并继续」，然后在目标页用扩展确认采集。';
      } else {
        task.run_id = result.run_id;
        task.batch_partial = result.batch_partial;
        render(ctx);
        const quality = result.batch_partial == null ? '' : `本批解析有缺项 ${result.batch_partial} 条。`;
        const imported = result.imported ?? (result.added ?? 0) + (result.updated ?? 0);
        const duplicates = result.source_duplicates ?? result.duplicates ?? 0;
        notice.textContent = `本批收录 ${imported} 条，跳过重复 ${duplicates} 条${result.skipped_quality != null ? '，跳过低质量 '+result.skipped_quality+' 条' : ''}。${quality}${result.run_id ? ' 点击「查看采集结果」核对内容。' : ' 本次未生成新批次。'}`;
      }
      await refresh(ctx);
    } catch (error) {
      if (current(ctx)) notice.textContent = error.message;
    } finally {
      ctx.busy.delete(task.id);
      if (current(ctx)) render(ctx);
    }
  }

  form.querySelectorAll('[name=mode]').forEach(input => input.onchange = updateQueryLabel);
  fields.platform.onchange = updateQueryLabel;
  fields.query.oninput = () => fields.query.setCustomValidity('');
  form.onsubmit = async event => {
    event.preventDefault();
    const ctx = session;
    if (!ctx || !current(ctx) || ctx.creating) return;
    const query = fields.query.value.trim();
    if (!query) {
      fields.query.setCustomValidity('请输入账号名称或话题关键词。');
      form.reportValidity();
      return;
    }
    ctx.creating = true;
    form.querySelector('button[type=submit]').disabled = true;
    try {
      const collector = fields.collector_label.value.trim();
      await api(ctx.path, 'POST', {platform: fields.platform.value, mode: fields.mode.value, query, limit: fields.limit.valueAsNumber, minutes: Number(fields.minutes.value), ...(collector && {collector_label: collector})});
      if (!current(ctx)) return;
      if (fields.query.value.trim() === query) fields.query.value = '';
      notice.textContent = '采集任务已添加。点击「立即采集」开始，结果会进入素材库。';
      await refresh(ctx);
    } catch (error) {
      if (current(ctx)) notice.textContent = error.message;
    } finally {
      ctx.creating = false;
      if (current(ctx)) form.querySelector('button[type=submit]').disabled = !ctx.platforms.length;
    }
  };

  byId('refreshTasks').onclick = () => {
    if (!session || !current(session)) return;
    notice.textContent = '正在加载任务…';
    refresh(session);
  };
  // The extension commits to the local API from its own tab.
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && session && current(session)) {
      refresh(session);
    }
  });
  window.addEventListener('message', event => {
    const ctx = session;
    const data = event.data;
    if (event.source !== window || event.origin !== location.origin || data?.type !== 'dm-collect-finished' || !ctx || !current(ctx) || String(data.project) !== String(ctx.project)) return;
    const task = ctx.tasks.find(item => String(item.id) === String(data.source));
    if (!task) return;
    notice.textContent = '浏览器采集已结束。';
    refresh(ctx);
  });
  const drafts=new Map();
  function remember(){
    if(!session)return;
    drafts.set(String(session.project),Object.fromEntries(['platform','mode','query','collector_label','limit','minutes'].map(name=>[name,form.elements[name].value])));
  }
  window.collectionTasks = {
    async focusTask(id) {
      const ctx = session;
      if (!ctx || !current(ctx)) return;
      await refresh(ctx, ctx.offset, id);
      if (current(ctx)) list.querySelector('[data-task-id="' + Number(id) + '"] [data-operation="run"]')?.focus();
    },
    close() {remember();session = null;},
    open(project) {
      remember();
      session = {project, path: `/api/projects/${encodeURIComponent(project)}/collection-tasks`, tasks: [], platforms: [], busy: new Set(), version: 0};
      list.replaceChildren();
      form.reset();
      byId('refreshTasks').hidden = true;
      form.querySelector('.taskOptions').open = !matchMedia('(max-width: 700px)').matches;
      const draft=drafts.get(String(project));
      if(draft)for(const [name,value] of Object.entries(draft))form.elements[name].value=value;
      syncPlatformPicker(form);
      updateQueryLabel();
      form.querySelector('button[type=submit]').disabled = true;
      notice.textContent = '正在加载任务…';
      const ctx = session;
      byId('taskReadiness').textContent = '正在检查内容处理能力…';
      api('/api/connections').then(status => {
        if (!current(ctx)) return;
        const ready = [status.image_ocr ? '图片文字识别：本机工具可用' : '图片文字识别：需安装 Tesseract 或 macOS Vision',
          '讲话转文字：' + ([!status.transcription && '需配置 ASR 服务和模型', !status.ffmpeg && '需安装 FFmpeg'].filter(Boolean).join('、') || '参数与工具齐全，待试用'),
          '内容理解：' + (status.model || '需配置模型服务')];
        const link = element('button', '检查内容处理能力', 'ghost'); link.type = 'button'; link.onclick = () => openConfigSection('connectionSettings');
        byId('taskReadiness').replaceChildren(ready.join(' · ') + ' ', link);
      }).catch(() => { if (current(ctx)) byId('taskReadiness').textContent = '暂时未取得处理能力状态，可到「配置」检查连接。'; });
      return refresh(session);
    }
  };
})();
