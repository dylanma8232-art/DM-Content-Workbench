(() => {
  const template = byId('publishForm').cloneNode(true);
  const pages = new Map();
  const posts = byId('publishPosts');
  let session;

  // LAN HTTP clients can use getRandomValues even without crypto.randomUUID.
  window.createWorkbenchRequestId = () => {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
    return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-');
  };

  function current(page) {
    return session === page && projectId === page.project && !byId('publishing').hidden;
  }

  let fileDatabase;
  function openFileDatabase() {
    if (!fileDatabase) fileDatabase = new Promise((resolve, reject) => {
      const open = indexedDB.open('dm-publishing-drafts', 1);
      open.onupgradeneeded = () => open.result.createObjectStore('files');
      open.onsuccess = () => resolve(open.result);
      open.onerror = () => reject(open.error);
    }).catch(error => { fileDatabase = null; throw error; });
    return fileDatabase;
  }

  async function draftFiles(page, operation, value) {
    const db = await openFileDatabase();
    return new Promise((resolve, reject) => {
      const tx = db.transaction('files', operation === 'get' ? 'readonly' : 'readwrite');
      const store = tx.objectStore('files');
      const request = operation === 'put' ? store.put(value, page.key) : store[operation](page.key);
      tx.oncomplete = () => resolve(request.result);
      tx.onabort = () => reject(tx.error);
    });
  }

  function targetsOf(page) {
    return Array.from(page.accounts.children, row => ({
      platform: row.querySelector('input[type=radio]:checked')?.value || '',
      account: row.querySelector('input[list]').value
    }));
  }

  function saveDraft(page, filesChanged = false) {
    if (pages.get(page.project) !== page) return;
    page.dirty = true;
    const {title, body, files} = page.form.elements;
    if (filesChanged) {
      page.fileId = files.files.length ? window.createWorkbenchRequestId() : null;
      const fileId = page.fileId;
      const operation = fileId ? draftFiles(page, 'put', {fileId, files: Array.from(files.files)}) : draftFiles(page, 'delete');
      page.fileWarning.textContent = fileId ? '正在保存草稿附件…' : '';
      operation.then(() => {
        if (page.fileId === fileId) page.fileWarning.textContent = '';
      }, () => {
        if (fileId && page.fileId === fileId) page.fileWarning.textContent = '附件未能保存到此浏览器。请在刷新前提交，或刷新后重新选择附件。';
      });
    }
    try {
      localStorage.setItem(page.key, JSON.stringify({
        title: title.value, body: page.draft?.body ?? body.value,
        targets: page.accounts.children.length ? targetsOf(page) : page.savedTargets || [],
        draft: page.draft && {run_id: page.draft.run_id, version: page.draft.version},
        requestId: page.requestId, fileId: page.fileId
      }));
      page.textWarning.textContent = '';
    } catch {
      page.textWarning.textContent = '浏览器未能保存本次草稿修改。请在刷新前提交，或先复制标题和正文。';
    }
  }

  function edited(page, filesChanged) {
    page.requestId = null;
    saveDraft(page, filesChanged);
    preparationSummary(page);
  }

  function preparationSummary(page) {
    const names = Array.from(page.form.elements.files.files, file => file.name);
    const targets = targetsOf(page).filter(target => target.platform).map(target => {
      const platform = page.platforms.find(item => item.id === target.platform);
      return (platform?.name || target.platform) + ' · ' + (target.account || '待填账号');
    });
    page.summary.textContent = '附件：' + (names.join('、') || '无') + '。目标：' + (targets.join('、') || '待选择');
  }

  function button(text, action, style = '') {
    const control = element('button', text, style);
    control.type = 'button';
    control.onclick = action;
    return control;
  }

  function labelled(text, input) {
    const label = element('label', text);
    label.append(input);
    return label;
  }

  function httpsUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === 'https:' && !url.username && !url.password ? url.href : '';
    } catch { return ''; }
  }

  function external(text, url) {
    const link = element('a', text, 'download');
    link.href = url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    return link;
  }

  function addAccount(page, saved) {
    const accounts = page.accounts;
    const row = element('div', '', 'publishAccount');
    const picker = element('fieldset');
    picker.append(element('legend', '发布平台'));
    const options = element('div', '', 'segmentedChoice publishPlatforms');
    const groupName = 'publish-platform-' + (++page.accountIndex);
    for (const item of page.platforms) {
      const radio = element('input');
      radio.type = 'radio';
      radio.name = groupName;
      radio.value = item.id;
      radio.required = true;
      const label = element('label');
      label.append(radio, element('span', item.name));
      options.append(label);
    }
    picker.append(options);
    const account = element('input');
    account.required = true;
    account.maxLength = 100;
    account.placeholder = '选择或填写账号';
    const choices = element('datalist');
    choices.id = 'publishAccounts-' + page.accountIndex;
    account.setAttribute('list', choices.id);
    const accountLabel = labelled('发布账号', account);
    accountLabel.hidden = true;
    account.disabled = true;
    picker.onchange = () => {
      accountLabel.hidden = false;
      account.disabled = false;
      account.value = '';
      populateChoices(page, row);
      if (choices.options.length === 1) account.value = choices.options[0].value;
    };
    row.append(picker, accountLabel, choices);
    if (accounts.children.length) row.append(button('移除', () => {
      row.remove();
      edited(page);
    }));
    accounts.append(row);
    const selected = Array.from(options.querySelectorAll('input')).find(input => input.value === saved?.platform);
    if (selected) {
      selected.checked = true;
      picker.onchange();
      account.value = saved.account;
    }
    return options.querySelector('input');
  }

  function populateChoices(page, row) {
    const platform = page.platforms.find(item => item.id === row.querySelector('input[type=radio]:checked')?.value);
    if (!platform) return;
    const names = new Set(page.accountRows.filter(item => item.platform === platform.id ||
      brandFor(item.platform).name === brandFor(platform.name).name).map(item => item.account));
    row.querySelector('datalist').replaceChildren(...Array.from(names, name => new Option(name)));
  }

  function copyButton(page, label, value) {
    return button(label, async () => {
      try {
        await navigator.clipboard.writeText(value);
        if (current(page)) notify('已' + label, 'info');
      } catch {
        if (current(page)) notify('复制失败，请展开稿件并手动复制。');
      }
    });
  }

  function canAssist(target) {
    return target.status === 'pending' && ['bilibili', 'xiaohongshu'].includes(target.platform);
  }

  function openAssisted(page, post, target) {
    if (!current(page) || !canAssist(target) || document.documentElement.dataset.dmPublishExtension !== '1') return;
    const ids = {project: Number(page.project), post: Number(post.id), target: Number(target.id)};
    if (!Object.values(ids).every(id => Number.isSafeInteger(id) && id > 0)) return;
    rememberTarget(page, target);
    window.postMessage({type: 'dm-publish-open', ...ids}, location.origin);
  }

  function batchControls(page, post, targets) {
    const group = element('fieldset', '', 'publishBatch');
    group.append(element('legend', '批量辅助填写'));
    const selections = targets.map(target => {
      const check = element('input');
      check.type = 'checkbox';
      check.dataset.targetId = target.id;
      check.checked = true;
      const platform = page.platforms.find(item => item.id === target.platform);
      const label = element('label');
      label.append(check, (platform?.name || target.platform) + ' · ' + target.account);
      group.append(label);
      return {check, target};
    });
    const open = button('打开所选发布页', () => {
      for (const {check, target} of selections) {
        if (check.checked) openAssisted(page, post, target);
      }
    }, 'secondary');
    group.onchange = () => { open.disabled = selections.every(({check}) => !check.checked); };
    group.append(open);
    return group;
  }

  function assistantControls(page, post, target) {
    const group = element('div', '', 'publishAssistant');
    group.append(element('p', '准备好正文与附件后使用辅助填写。在平台核对账号、素材并手动发布，再回到此账号填写作品链接。', 'fieldHelp'));
    if (location.protocol !== 'http:' || !['localhost', '127.0.0.1'].includes(location.hostname)) {
      group.append(element('p', '辅助填写需在运行工作台的电脑上使用。请通过 localhost 或 127.0.0.1 的 HTTP 地址打开工作台并安装扩展，或在当前设备手动发布。', 'fieldHelp'));
      return group;
    }
    const assist = button('工作台辅助填写', () => openAssisted(page, post, target), 'secondary');
    assist.classList.add('publishWithExtension');
    const hint = element('details', '', 'publishWithoutExtension');
    hint.append(element('summary', '安装扩展以辅助填写'));
    const download = element('a', '下载扩展 ZIP', 'download');
    download.href = '/api/publishing/extension.zip';
    download.download = '';
    hint.append(download, element('p', '解压后，在浏览器扩展管理页开启开发者模式，加载已解压的目录，再刷新工作台。', 'fieldHelp'));
    group.append(assist, hint);
    return group;
  }

  function rememberTarget(page, target) {
    try { sessionStorage.setItem('dm-publish-target:' + page.project, String(target.id)); } catch {}
  }

  function targetRow(page, post, target) {
    const row = element('div', '', 'publishTarget');
    row.id = 'publish-target-' + target.id;
    row.targetSnapshot = JSON.stringify(target);
    row.addEventListener('focusin', () => rememberTarget(page, target));
    const platform = page.platforms.find(item => item.id === target.platform) || (target.platform === 'weixin'
      ? {name:'微信视频号', url:'https://channels.weixin.qq.com/platform/post/create'} : null);
    const status = {pending: '待发布', published: '已发布', failed: '发布失败'}[target.status] || target.status;
    const heading = element('div', '', 'ph');
    heading.append(element('strong', (platform?.name || target.platform) + ' · ' + target.account), element('small', status));
    row.append(heading);
    if (target.status === 'published') {
      if (httpsUrl(target.url)) row.append(external('查看作品', httpsUrl(target.url)));
      const panel = element('details');
      panel.append(element('summary','作品复盘'));
      const records = target.metrics || [];
      if (!records.length) panel.append(element('p','暂无作品数据，可按平台后台的作品明细回填。'));
      for (const record of records.slice().reverse()) {
        panel.append(element('p',new Date(record.sampled).toLocaleString() + ' · 阅读／播放 ' + record.views + ' · 互动 ' + record.engagements + ' · ' + record.source));
      }
      const form = element('form');
      for (const [name,caption,type] of [['sampled','采样时间','datetime-local'],['views','阅读／播放','number'],['engagements','互动次数','number'],['source','数据来源','text']]) {
        const label = element('label',caption), input = element('input');
        input.name=name; input.type=type; input.required=true;
        if (type==='number') {input.min='0';input.max='1000000000000';}
        if (name==='source') input.maxLength=200;
        if (name==='sampled') {const now=new Date();input.value=new Date(now-now.getTimezoneOffset()*60000).toISOString().slice(0,16);}
        label.append(input);form.append(label);
      }
      const save = element('button','保存作品数据','secondary');save.type='submit';form.append(save);
      form.onsubmit=async event=>{
        event.preventDefault();if (!current(page) || save.disabled) return;
        const values=Object.fromEntries(new FormData(form));values.sampled=new Date(values.sampled).toISOString();
        save.disabled=true;
        try {await api(page.base+'/targets/'+target.id+'/metrics','PUT',values);if(current(page))await refresh(page);}
        catch(error){if(current(page))notify(error.message);} finally {save.disabled=false;}
      };
      panel.append(form);row.append(panel);
      return row;
    }
    const controls = element('fieldset');
    row.append(controls);
    async function update(status, url) {
      if (!current(page) || controls.disabled) return;
      controls.disabled = true;
      try {
        await api(page.base + '/targets/' + encodeURIComponent(target.id), 'POST', {status, url});
        if (current(page)) await refresh(page);
      } catch (error) {
        if (current(page)) notify(error.message);
      } finally { controls.disabled = false; }
    }
    if (target.status === 'failed') {
      controls.append(button('重新尝试', () => update('pending')));
      return row;
    }
    if (target.status !== 'pending') return row;
    const portal = httpsUrl(platform?.url);
    if (portal) {
      const link = external(target.platform === 'instagram' ? '打开 Instagram 首页' : '打开官方发布后台', portal);
      link.onclick = () => rememberTarget(page, target);
      controls.append(link);
    }
    if (canAssist(target)) controls.append(assistantControls(page, post, target));
    const confirmation = element('form');
    const input = element('input');
    input.type = 'url';
    input.required = true;
    input.placeholder = 'https://…';
    input.oninput = () => input.setCustomValidity('');
    const actions = element('div', '', 'formActions');
    actions.append(element('button', '确认已发布', 'primary'), button('标记失败', () => update('failed')));
    confirmation.append(labelled('作品链接', input), actions);
    confirmation.onsubmit = event => {
      event.preventDefault();
      const url = httpsUrl(input.value.trim());
      if (url) return update('published', url);
      input.setCustomValidity('请输入有效的 HTTPS 作品链接。');
      input.reportValidity();
    };
    controls.append(confirmation);
    return row;
  }

  function postEntry(page, post) {
    const entry = element('article', '', 'publishPost');
    entry.dataset.postId = post.id;
    const published = post.targets.filter(target => target.status === 'published').length;
    entry.append(element('h3', post.title), element('p', '已确认发布 ' + published + '/' + post.targets.length + ' 个目标账号', 'fieldHelp'));
    if (!post.run_id) entry.append(element('p', '独立录入稿', 'fieldHelp'));
    if (post.run_id && post.version) {
      const source = element('div', '', 'formActions');
      const version = element('a', '下载审核原稿 · 版本 ' + post.version, 'download');
      version.href = '/api/runs/' + encodeURIComponent(post.run_id) + '/export?version=' + encodeURIComponent(post.version);
      source.append(button('返回来源稿件', handle(() => openRun(post.run_id)()), 'ghost'), version);
      entry.append(source);
    }
    const text = element('details');
    text.append(element('summary', post.assets.length ? '正文与附件（' + post.assets.length + '）' : '稿件正文'), element('p', post.body, 'publishBody'));
    const actions = element('div', '', 'formActions');
    actions.append(copyButton(page, '复制标题', post.title), copyButton(page, '复制正文', post.body));
    for (const file of post.assets) {
      const link = element('a', '下载 ' + file.name, 'download');
      link.href = page.base + '/assets/' + encodeURIComponent(file.id);
      link.download = file.name;
      actions.append(link);
    }
    text.append(actions);
    entry.append(text);
    const eligible = post.targets.filter(canAssist);
    if (eligible.length >= 2) entry.append(batchControls(page, post, eligible));
    entry.append(...Array.from(post.targets).sort((a, b) => Number(a.status === 'published') - Number(b.status === 'published')).map(target => targetRow(page, post, target)));
    return entry;
  }

  function renderPosts(page, result) {
    const focused = document.activeElement;
    const focusedTarget = focused.closest('.publishTarget')?.id;
    const fieldName = focused.name;
    const visible = new Map([...posts.querySelectorAll('.publishPost')].map(entry => [entry.dataset.postId,entry]));
    const entries = result.posts.map(post => {
      const old = visible.get(String(post.id));
      const snapshot = JSON.stringify(post);
      if (old?.postSnapshot === snapshot) return old;
      const entry = postEntry(page, post);
      entry.postSnapshot = snapshot;
      if (old) {
        for (const target of post.targets) {
          const selector = '#publish-target-' + target.id;
          const previous = old.querySelector(selector);
          const replacement = entry.querySelector(selector);
          if (previous?.targetSnapshot === JSON.stringify(target)) replacement.replaceWith(previous);
          else if (previous && target.status === 'published' && previous.querySelector('form input[name=views]')) {
            replacement.querySelector('details').open = previous.querySelector('details').open;
            for (const input of previous.querySelectorAll('input[name]')) replacement.querySelector('[name="'+input.name+'"]').value = input.value;
          }
        }
        for (const input of old.querySelectorAll('[data-target-id]')) {
          const check = entry.querySelector('[data-target-id="'+input.dataset.targetId+'"]');
          if (check) check.checked = input.checked;
        }
        entry.querySelector('.publishBatch')?.onchange();
        entry.querySelector('details').open = old.querySelector('details').open;
      }
      return entry;
    });
    posts.replaceChildren(...entries);
    const replacement = focused.dataset.targetId ? posts.querySelector('[data-target-id="'+focused.dataset.targetId+'"]')
      : focusedTarget && fieldName ? document.getElementById(focusedTarget)?.querySelector('[name="'+CSS.escape(fieldName)+'"]') : null;
    if (focused !== document.body) (focused.isConnected ? focused : replacement)?.focus({preventScroll:true});
  }

  async function refresh(page, offset = page.offset || 0, resumeTarget = null) {
    const {accounts, fields} = page;
    const revision = ++page.revision;
    if (!resumeTarget && offset === page.offset && current(page)) {
      const focus = document.activeElement;
      resumeTarget = focus.closest('.publishTarget')?.id.slice('publish-target-'.length) || focus.dataset.targetId;
    }
    try {
      const [result, report] = await Promise.all([
        api(page.base + '?' + new URLSearchParams({limit:listPageSize, offset, ...(resumeTarget && {target_id:resumeTarget})})),
        api('/api/projects/' + page.project + '/account-daily?day=' + byId('accountDate').value).catch(() => ({accounts: []}))
      ]);
      if (!current(page) || revision !== page.revision) return;
      const portals = new Map(result.platforms.map(platform => [platform.id, platform.url]));
      page.platforms = DMPlatforms.filter(platform => portals.has(platform.id)).map(platform => ({
        id: platform.id, name: platform.label, url: portals.get(platform.id)
      }));
      page.accountRows = [...report.accounts, ...(result.accounts || [])];
      if (!accounts.children.length) {
        for (const target of page.savedTargets?.length ? page.savedTargets : [null]) addAccount(page, target);
        page.savedTargets = null;
      }
      else for (const row of accounts.children) populateChoices(page, row);
      await page.restoringFiles;
      if (!current(page) || revision !== page.revision) return;
      fields.disabled = page.loadingDraft || page.saving || !page.platforms.length;
      page.offset = result.offset;
      renderPosts(page,result);
      listPager(posts,page.offset,result.total,next => refresh(page,next));
      let lastTarget;
      try { lastTarget = sessionStorage.getItem('dm-publish-target:' + page.project); } catch {}
      const resume = document.getElementById('publish-target-' + lastTarget);
      if (lastTarget && result.total) posts.prepend(button('返回上次目标' + (resume ? ' · ' + resume.querySelector('strong').textContent : ''), async () => {
        if (!resume) await refresh(page,page.offset,lastTarget);
        if (!current(page)) return;
        const target = document.getElementById('publish-target-' + lastTarget);
        target?.scrollIntoView({block: 'center'});
        (target?.querySelector('input[type=url]') || target?.querySelector('a, button, summary'))?.focus({preventScroll:true});
      }, 'secondary'));
      if (page.editing == null) page.editing = !result.posts.length;
      showPanel(page);
      preparationSummary(page);
      if (!result.posts.length) posts.textContent = '当前项目还没有发布任务。';
      if (!page.platforms.length) notify('暂无可用发布平台。');
    } catch (error) {
      if (current(page) && revision === page.revision) {
        if (!posts.querySelector('article')) posts.textContent = '读取发布任务失败，请刷新重试。';
        notify(error.message);
      }
    }
  }

  function sourceState(page) {
    const {form} = page;
    const linked = !!page.draft;
    form.elements.title.readOnly = linked;
    form.elements.body.readOnly = linked;
    const source = form.querySelector('#publishSource');
    source.hidden = false;
    source.replaceChildren();
    if (!linked) source.textContent = '独立录入稿';
    if (linked) {
      source.append('已审核成稿 #' + page.draft.run_id + ' · 版本 ' + page.draft.version + '。标题和正文固定为审核原稿；修改请返回成稿库保存新版本并重新审核。', button('取消关联，按独立内容编辑', () => {
        if (!current(page) || page.loadingDraft || page.saving) return;
        page.draft = null;
        edited(page);
        sourceState(page);
        form.elements.title.focus();
      }));
      source.append(button('返回来源稿件',handle(async()=>{
        if(!current(page)||!page.draft)return;
        await openRun(page.draft.run_id)();
      }),'ghost'));
    }
  }

  function fillDraft(page, body) {
    page.draft = {...page.draft, body};
    const {form} = page;
    form.elements.title.value = body.trim().split('\n')[0].replace(/^#+/, '').trim();
    form.elements.body.value = body;
    saveDraft(page);
  }

  function showPanel(page) {
    page.form.hidden = !page.editing;
    byId('publishQueue').hidden = !!page.editing;
    const compose = byId('publishCompose');
    compose.hidden = !!page.editing;
    compose.textContent = page.draft || page.dirty ? '继续编辑发布任务' : '新建发布任务';
  }

  function createPage(draft) {
    const form = template.cloneNode(true);
    const fields = form.querySelector('#publishFields');
    const accounts = form.querySelector('#publishAccounts');
    const page = {project: projectId, base: '/api/projects/' + projectId + '/publishing', form, fields, accounts, draft, revision: 0, accountIndex: 0, platforms: [], accountRows: [], editing: draft ? true : null};
    fields.disabled = true;
    page.key = 'dm-publishing-draft:' + projectId;
    page.summary = element('p', '', 'fieldHelp');
    accounts.after(page.summary);
    page.textWarning = element('p', '', 'fieldHelp');
    page.fileWarning = element('p', '', 'fieldHelp');
    for (const warning of [page.textWarning, page.fileWarning]) {
      warning.setAttribute('role', 'status');
      fields.before(warning);
    }
    try {
      const saved = JSON.parse(localStorage.getItem(page.key));
      if (saved && (!draft || (draft.run_id === saved.draft?.run_id && draft.version === saved.draft?.version))) {
        form.elements.title.value = saved.title;
        form.elements.body.value = saved.body;
        Object.assign(page, {
          draft: saved.draft && {...saved.draft, body: saved.body}, savedTargets: saved.targets,
          fileId: saved.fileId, requestId: saved.requestId, dirty: true, editing: true
        });
        if (saved.fileId) page.restoringFiles = draftFiles(page, 'get').then(record => {
          if (record?.fileId !== saved.fileId) throw Error();
          const transfer = new DataTransfer();
          for (const file of record.files) transfer.items.add(file);
          form.elements.files.files = transfer.files;
        }).catch(() => { page.fileWarning.textContent = '草稿附件未能恢复，请重新选择附件。'; });
      }
    } catch { page.textWarning.textContent = '未能读取此浏览器保存的发布草稿。'; }
    form.oninput = event => {
      if (!['file', 'radio'].includes(event.target.type)) edited(page);
    };
    form.onchange = event => {
      if (['file', 'radio'].includes(event.target.type)) edited(page, event.target.type === 'file');
    };
    form.onsubmit = event => submit(page, event);
    form.querySelector('#publishAdd').onclick = () => {
      addAccount(page)?.focus();
      edited(page);
    };
    form.querySelector('#publishShowTasks').onclick = () => {
      page.editing = false;
      showPanel(page);
      byId('publishQueue').querySelector('h2').focus();
    };
    pages.set(projectId, page);
    sourceState(page);
    return page;
  }

  async function openPublishing(draft) {
    let page = pages.get(projectId);
    const newDraft = draft && (!page?.draft || draft.run_id !== page.draft.run_id || draft.version !== page.draft.version);
    if (!page || newDraft) page = createPage(draft);
    const {form, fields} = page;
    if (draft) page.editing = true;
    if (session !== page) byId('publishForm').replaceWith(form);
    session = page;
    view('publishing');
    notify('', 'info');
    byId('publishScope').textContent = currentProject().name;
    showPanel(page);
    if (newDraft && !page.dirty && draft.body !== undefined) fillDraft(page, draft.body);
    posts.textContent = '正在读取发布任务…';
    page.loadingDraft ||= newDraft && !page.dirty && draft.body === undefined;
    const loading = refresh(page);
    if (newDraft && page.loadingDraft) {
      try {
        const run = await request('/' + encodeURIComponent(draft.run_id));
        const decision = run.payload.reviews?.filter(item => item.version === draft.version).at(-1);
        if (run.project_id !== page.project || run.payload.drafts.length !== draft.version || decision?.decision !== '通过') throw Error('稿件已更新，请在成稿库重新选择已审核版本。');
        fillDraft(page, run.payload.drafts[draft.version - 1]);
        page.loadingDraft = false;
        fields.disabled = !page.platforms.length;
      } catch (error) {
        page.loadingDraft = false;
        page.draft = null;
        sourceState(page);
        fields.disabled = !page.platforms.length;
        if (current(page)) notify(error.message + '。可返回成稿库重新选择，或填写独立发布内容。');
      }
    }
    await loading;
  }

  async function createPost(page, data) {
    if (!page.draft) return fetch(page.base, {method: 'POST', body: data});
    // FormData converts text newlines to CRLF. Keep the reviewed Markdown byte-for-byte
    // while letting the browser encode file names and all other multipart fields.
    data.delete('body');
    const parts = new Response(data);
    const type = parts.headers.get('Content-Type');
    const boundary = type.split('boundary=')[1];
    const body = new Blob([
      '--' + boundary + '\r\nContent-Disposition: form-data; name="body"\r\n\r\n',
      page.draft.body, '\r\n', await parts.blob()
    ], {type});
    return fetch(page.base, {method: 'POST', headers: {'Content-Type': type}, body});
  }

  async function submit(page, event) {
    event.preventDefault();
    const {form, fields, accounts} = page;
    if (!current(page) || fields.disabled) return;
    const totalSize = Array.from(form.elements.files.files).reduce((sum, file) => sum + file.size, 0);
    if (totalSize > 64 * 1024 * 1024) return notify('素材总大小超过 64 MiB，请减少文件后重试。');
    const data = new FormData(form);
    const targets = targetsOf(page).map(target => ({...target, account: target.account.trim()}));
    if (!data.get('title').trim() || !data.get('body').trim() || targets.some(target => !target.account)) return notify('请填写标题、正文和账号名称。');
    if (new Set(targets.map(target => JSON.stringify(target))).size !== targets.length) return notify('目标账号重复，请移除重复项。');
    data.set('targets', JSON.stringify(targets));
    if (!form.elements.files.files.length) data.delete('files');
    if (page.draft) {
      data.set('run_id', page.draft.run_id);
      data.set('version', page.draft.version);
    }
    if (!page.requestId) page.requestId = window.createWorkbenchRequestId();
    saveDraft(page);
    data.set('request_id', page.requestId);
    page.saving = true;
    fields.disabled = true;
    try {
      const response = await createPost(page, data);
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw Error(typeof result.detail === 'string' ? result.detail : '创建任务失败，请重试。');
      }
      if (pages.get(page.project) !== page) return;
      try {
        localStorage.removeItem(page.key);
        page.textWarning.textContent = '';
      } catch { page.textWarning.textContent = '任务已创建，但浏览器未能清除本地草稿。'; }
      draftFiles(page, 'delete').catch(() => {});
      page.fileId = null;
      page.fileWarning.textContent = '';
      form.reset();
      page.dirty = false;
      page.editing = false;
      accounts.replaceChildren();
      page.requestId = null;
      page.draft = null;
      sourceState(page);
      addAccount(page);
      preparationSummary(page);
      if (!current(page)) return;
      showPanel(page);
      byId('publishQueue').querySelector('h2').focus();
      notify('已创建任务。请打开目标账号的官方后台发布，再回来填写作品链接。', 'info');
      await refresh(page);
    } catch (error) {
      if (current(page)) notify(error.message);
    } finally {
      page.saving = false;
      fields.disabled = !page.platforms.length;
    }
  }
  byId('publishCompose').onclick = () => {
    session.editing = true;
    showPanel(session);
    session.form.elements.title.focus();
  };
  byId('publishRefresh').onclick = () => refresh(session);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && session && current(session)) refresh(session);
  });
  byId('publishingNav').onclick = () => openPublishing();
  window.openPublishing = openPublishing;
})();
