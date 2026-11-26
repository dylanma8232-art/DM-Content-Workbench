(() => {
  const names = {materials: '素材库', topics: '选题库', drafts: '稿件库'};
  const descriptions = {
    materials: '按来源、状态或批次检索原始与清洗后正文，支持跨来源勾选合并为新分析批次。',
    topics: '对照推荐理由与跨素材原句证据，核实独立来源后一键进入内容生产。',
    drafts: '回查最新修改的多版本成稿，直接导出 Markdown 或反向追溯原始素材。'
  };
  const limit = 20;
  let session;
  let basket;

  function node(tag, text = '', className = '') {
    const result = element(tag, text);
    if (className) result.className = className;
    return result;
  }

  function button(text, click, className = '') {
    const result = node('button', text, className);
    result.type = 'button';
    result.onclick = click;
    return result;
  }

  function field(title, input) {
    const label = node('label', title);
    label.append(input);
    return label;
  }

  function itemMeta(kind, item) {
    const batch = kind === 'materials' && item.batch_name && item.batch_name !== item.source
      ? '批次：' + item.batch_name
      : '';
    return [item.source, batch, item.status].filter(Boolean).join(' · ');
  }

  function activePage(page) {
    return page === session && projectId === page.project && !page.root.hidden;
  }

  function navigation(kind) {
    for (const name of Object.keys(names)) {
      const link = byId(name + 'Nav');
      if (name === kind) link?.setAttribute('aria-current', 'page');
      else link?.removeAttribute('aria-current');
    }
  }

  function resetProject() {
    clearTimeout(session?.timer);
    session = null;
    basket = null;
    navigation(null);
    byId('library')?.replaceChildren();
  }

  function content(parent, heading, value) {
    if (!value) return;
    parent.append(node('h3', heading), node('p', value, 'lib-prose'));
  }

  function original(parent, value) {
    if (!value) return;
    const fold = node('details');
    fold.append(node('summary', '查看原文'), node('p', value, 'lib-prose'));
    parent.append(fold);
  }

  function references(page, item) {
    const fold = node('details');
    const results = node('div', '', 'lib-references');
    const message = node('p');
    message.setAttribute('role', 'status');
    fold.append(node('summary', '展开关联选题与稿件'), message, results);
    let started = false;
    const current = () => activePage(page) && page.selected === item && page.detail.contains(fold);

    async function read() {
      if (!current() || started) return;
      started = true;
      results.replaceChildren();
      message.textContent = '正在读取关联内容…';
      try {
        const material = encodeURIComponent(item.material_id ?? item.key);
        const data = await api('/api/projects/' + page.project + '/references/' + encodeURIComponent(item.run_id) + '/' + material);
        if (!current()) return;
        message.textContent = data.topics.length || data.drafts.length ? '' : '暂无关联选题或稿件。';
        for (const kind of ['topics', 'drafts']) {
          if (!data[kind].length) continue;
          results.append(node('h3', (kind === 'topics' ? '关联选题' : '关联稿件') + '（' + data[kind].length + '）'));
          for (const entry of data[kind]) {
            results.append(button(entry.title || '未命名', () => window.openLibrary(kind, {run_id: entry.run_id, key: entry.key})));
          }
        }
      } catch (error) {
        if (!current()) return;
        started = false;
        message.textContent = '读取失败。' + error.message;
        results.append(button('重试', read));
      }
    }

    fold.ontoggle = () => {
      if (fold.open) read();
    };
    return fold;
  }

  function renderEmptyDetail(page) {
    const box = node('div', '', 'lib-empty-detail');
    const emptyGuide = {
      materials: '当前筛选下没有素材。你可以直接导入本地文档、PDF、音视频，或配置外部 RSS / 网页 / API 采集源。',
      topics: '暂无匹配的候选或已选选题。请先在素材库导入或合并素材批次，并完成清洗与证据提取。',
      drafts: '暂无匹配的成稿记录。确认选题并生成草稿后，可在此统一检索、导出与追溯依据。'
    };
    box.append(node('p', emptyGuide[page.kind]));
    page.detail.replaceChildren(box);
  }

  function select(page, item, reveal) {
    page.selected = item;
    const detail = page.detail;
    detail.replaceChildren();
    for (const [index, row] of page.rows.entries()) {
      if (page.items[index] === item) row.setAttribute('aria-current', 'true');
      else row.removeAttribute('aria-current');
    }
    if (!item) {
      renderEmptyDetail(page);
      return;
    }

    const back = button('返回结果列表', () => {
      page.root.classList.remove('lib-reading');
      page.rows[page.items.indexOf(item)]?.focus();
    }, 'lib-back');

    const heading = node('h2', item.title || '未命名');
    heading.tabIndex = -1;
    detail.append(back, node('small', itemMeta(page.kind, item)), heading);

    const actionLabel = page.kind === 'drafts'
      ? '编辑与审核稿件'
      : page.kind === 'topics' && item.status === '候选'
        ? '使用此选题'
        : '打开所属批次';

    const actionBar = node('div', '', 'lib-detail-actions');
    const openRunBtn = button(actionLabel, handle(async () => {
      const revision = page.revision;
      const run = await request('/' + encodeURIComponent(item.run_id));
      if (activePage(page) && page.selected === item && page.revision === revision) {
        await show(run, page.kind === 'topics' ? item.key : undefined);
      }
    }), 'lib-open');
    actionBar.append(openRunBtn);

    if (page.kind === 'drafts') {
      const exportLink = node('a', '导出当前版本 (.md)', 'download');
      exportLink.href = '/api/runs/' + encodeURIComponent(item.run_id) + '/export?version=' + encodeURIComponent(item.version);
      actionBar.append(exportLink);
    }
    detail.append(actionBar);

    if (page.kind === 'materials') {
      if (item.created) detail.append(node('small', '批次时间 · ' + item.created + ' UTC'));
      if (item.status === '待清洗') content(detail, '原文（待清洗）', item.original || item.excerpt);
      else {
        content(detail, '清洗文本', item.cleaned);
        original(detail, item.original || item.excerpt);
      }
      detail.append(references(page, item));
    } else {
      if (page.kind === 'topics') {
        content(detail, '推荐理由', item.reason || item.excerpt);
      } else {
        const meta = [
          '版本 ' + item.version,
          item.updated ? '最近修改 · ' + new Date(item.updated).toLocaleString() : '历史版本未记录修改时间'
        ];
        if (meta.length) detail.append(node('small', meta.join(' · ')));
        content(detail, '稿件摘要', item.excerpt);
      }
      const evidence = item.evidence || [];
      detail.append(node('h3', '引用依据（' + evidence.length + '）'));
      if (item.source_count != null) detail.append(node('small', '涉及 ' + item.source_count + ' 个来源'));
      for (const entry of evidence) {
        const block = node('section', '', 'lib-evidence');
        block.append(node('strong', entry.title || '素材 ' + entry.id));
        if (entry.source) block.append(node('small', entry.source, 'lib-origin'));
        if (entry.quote) block.append(node('blockquote', entry.quote));
        original(block, entry.original);
        if (entry.run_id != null && entry.material_id != null) {
          block.append(button('打开原始素材', () => window.openLibrary('materials', {
            run_id: entry.run_id,
            key: String(entry.material_id)
          })));
        }
        detail.append(block);
      }
    }

    if (reveal) {
      page.root.classList.add('lib-reading');
      heading.focus();
    }
  }

  function materialKey(item) {
    return JSON.stringify([item.run_id, String(item.material_id ?? item.key)]);
  }

  function updateBasket(page) {
    const count = basket.items.size;
    page.count.textContent = '已选 ' + count + ' / 100';
    page.create.disabled = !count || basket.busy;
    page.clear.disabled = !count || basket.busy;
    page.name.disabled = basket.busy;
    for (const [key, input] of page.checks) {
      input.checked = basket.items.has(key);
      input.disabled = basket.busy || (!input.checked && count >= 100);
    }
  }

  function collection(page) {
    const form = node('form', '', 'lib-collection');
    page.count = node('strong');
    page.count.setAttribute('role', 'status');
    page.name = node('input');
    page.name.required = true;
    page.name.value = basket.name;
    page.name.oninput = () => {
      basket.name = page.name.value;
    };
    page.clear = button('清空选择', () => {
      basket.items.clear();
      updateBasket(page);
    });
    page.create = node('button', '合并为新批次', 'lib-submit');
    page.create.type = 'submit';
    form.append(page.count, page.clear, field('新批次名称', page.name), page.create);

    form.onsubmit = async event => {
      event.preventDefault();
      if (!activePage(page) || !basket.items.size || basket.busy) return;
      const current = basket;
      const name = page.name.value.trim();
      if (!name) {
        page.name.focus();
        return;
      }
      const items = [...current.items.values()];
      const payload = JSON.stringify({name, items});
      current.busy = true;
      updateBasket(page);
      try {
        if (current.payload !== payload) {
          current.requestKey = crypto.randomUUID();
          current.payload = payload;
        }
        const run = await api('/api/projects/' + page.project + '/collections', 'POST', {
          name,
          request_key: current.requestKey,
          items
        });
        current.items.clear();
        current.payload = null;
        if (activePage(page)) await show(run);
      } catch (error) {
        if (activePage(page)) page.message.textContent = error.message;
      } finally {
        current.busy = false;
        if (current === basket && session?.kind === 'materials') updateBasket(session);
      }
    };
    updateBasket(page);
    return form;
  }

  function renderStatusChips(page, statuses) {
    if (!page.chips) return;
    page.chips.replaceChildren();
    if (!statuses.length) return;
    const current = page.status.value.trim();
    for (const [label, val] of [['全部状态', ''], ...statuses.map(s => [s, s])]) {
      const chip = button(label, () => {
        page.status.value = val;
        page.offset = 0;
        load(page, null, 0);
      }, 'lib-chip');
      chip.setAttribute('aria-pressed', String(current === val));
      page.chips.append(chip);
    }
  }

  function draw(page, result, wanted) {
    page.items = result.items;
    page.rows = [];
    page.checks.clear();
    page.list.replaceChildren();
    const sources = new Set(result.sources || []);
    if (page.source.value) sources.add(page.source.value);
    const source = page.source.value;
    page.source.replaceChildren(new Option('全部来源', ''));
    for (const value of sources) {
      if (value) page.source.add(new Option(value, value));
    }
    page.source.value = source;
    const statusList = result.statuses || [];
    page.statusOptions.replaceChildren(...statusList.map(value => new Option(value, value)));
    renderStatusChips(page, statusList);

    for (const item of page.items) {
      const row = node('div', '', 'lib-row');
      const open = button('', () => select(page, item, true), 'lib-item');
      open.append(node('strong', item.title || '未命名'), node('small', itemMeta(page.kind, item)));
      if (page.kind === 'materials') {
        const check = node('input');
        check.type = 'checkbox';
        check.setAttribute('aria-label', '选择素材 ' + item.title);
        const key = materialKey(item);
        check.onchange = () => {
          if (check.checked && basket.items.size < 100) {
            basket.items.set(key, {run_id: item.run_id, material_id: Number(item.material_id ?? item.key)});
          } else {
            basket.items.delete(key);
          }
          updateBasket(page);
        };
        page.checks.set(key, check);
        row.append(check);
      }
      row.append(open);
      page.list.append(row);
      page.rows.push(open);
    }
    if (page.kind === 'materials') updateBasket(page);
    page.message.textContent = wanted != null && !page.items.some(item => item.key === wanted)
      ? '该条目已不可用。'
      : result.total
        ? '共 ' + result.total + ' 条'
        : '没有找到匹配的条目。';
    page.prev.disabled = page.offset === 0;
    page.next.disabled = page.offset + limit >= result.total;
    page.position.textContent = result.total
      ? (page.offset + 1) + '–' + (page.offset + page.items.length)
      : '0';
    const match = page.items.find(item => item.key === wanted);
    select(page, match || page.items[0], Boolean(match));
  }

  async function load(page, wanted, delay = 0) {
    clearTimeout(page.timer);
    const revision = ++page.revision;
    page.root.classList.remove('lib-reading');
    page.selected = null;
    page.detail.replaceChildren();
    page.list.replaceChildren();
    page.position.textContent = '';
    page.prev.disabled = page.next.disabled = true;
    page.message.textContent = '正在读取…';
    const fresh = () => activePage(page) && page.revision === revision;
    const fetchPage = async () => {
      try {
        while (fresh()) {
          const params = new URLSearchParams({offset: page.offset, limit});
          for (const [key, value] of Object.entries({
            q: page.query.value.trim(),
            status: page.status.value.trim(),
            source: page.source.value,
            run_id: page.run,
            since: page.since?.value,
            until: page.until?.value
          })) {
            if (value != null && value !== '') params.set(key, value);
          }
          const result = await api('/api/projects/' + page.project + '/library/' + page.kind + '?' + params);
          if (!fresh()) return;
          if (wanted != null && result.items.length && page.offset + limit < result.total && !result.items.some(item => item.key === wanted)) {
            page.offset += limit;
            continue;
          }
          if (page.offset && !result.items.length) {
            page.offset = 0;
            wanted = null;
            continue;
          }
          draw(page, result, wanted);
          return;
        }
      } catch (error) {
        if (fresh()) {
          page.message.replaceChildren(
            node('span', '读取失败。' + error.message),
            button('重试', () => load(page, wanted))
          );
        }
      }
    };
    if (delay) page.timer = setTimeout(fetchPage, delay);
    else await fetchPage();
  }

  function buildHeaderActions(kind) {
    const actions = node('div', '', 'lib-actions');
    if (kind === 'materials') {
      actions.append(
        button('管理采集来源', handle(() => window.openConfigSection('sourcesCard')), 'ghost'),
        button('导入新素材', () => window.openImportView(), 'primary')
      );
    } else if (kind === 'topics') {
      actions.append(
        button('配置主题分析词', handle(() => window.openConfigSection('rulesCard')), 'ghost'),
        button('前往素材库合并批次', handle(() => window.openLibrary('materials')), 'secondary')
      );
    } else {
      actions.append(
        button('浏览候选选题', handle(() => window.openLibrary('topics')), 'ghost'),
        button('导入新素材', () => window.openImportView(), 'secondary')
      );
    }
    return actions;
  }

  window.openLibrary = async function openLibrary(kind, options = {}) {
    if (!Object.hasOwn(names, kind)) throw Error('未知内容库');
    const root = byId('library');
    clearTimeout(session?.timer);
    if (basket?.project !== projectId) basket = {project: projectId, items: new Map(), name: '素材合辑'};
    const page = {kind, project: projectId, root, run: options.run_id, offset: 0, revision: 0, checks: new Map()};
    session = page;
    view('library');
    root.hidden = false;
    navigation(kind);

    const head = node('div', '', 'lib-head');
    const titleWrap = node('div');
    titleWrap.append(node('h1', names[kind]), node('p', descriptions[kind]));
    head.append(titleWrap, buildHeaderActions(kind));
    root.replaceChildren(head);

    const filters = node('div', '', 'lib-filters');
    page.query = node('input');
    page.query.type = 'search';
    page.query.placeholder = '搜索标题、正文或来源';
    page.status = node('input');
    page.status.placeholder = '全部状态';
    page.statusOptions = node('datalist');
    page.statusOptions.id = 'libraryStatusOptions';
    page.status.setAttribute('list', page.statusOptions.id);
    page.source = node('select');
    page.source.add(new Option('全部来源', ''));
    const refresh = delay => {
      page.offset = 0;
      return load(page, null, delay);
    };
    page.query.oninput = page.status.oninput = () => refresh(220);
    page.source.onchange = () => refresh(0);
    filters.append(
      field('搜索', page.query),
      field('状态', page.status),
      field('来源', page.source),
      page.statusOptions
    );
    root.append(filters);

    page.chips = node('div', '', 'lib-chips');
    root.append(page.chips);

    if (kind === 'materials') {
      const dates = node('details', '', 'lib-dates');
      dates.append(node('summary', '按批次日期筛选（UTC）'));
      const range = node('div', '', 'pair');
      page.since = node('input');
      page.until = node('input');
      page.since.type = page.until.type = 'date';
      page.since.onchange = page.until.onchange = () => {
        page.since.max = page.until.value;
        page.until.min = page.since.value;
        if (page.since.reportValidity() && page.until.reportValidity()) refresh(0);
      };
      range.append(field('起始日期', page.since), field('结束日期', page.until));
      dates.append(range);
      root.append(dates);
    }

    if (page.run != null) {
      root.append(button('批次 ' + page.run + ' · 清除限定', event => {
        page.run = null;
        event.currentTarget.remove();
        page.query.focus();
        refresh(0);
      }, 'lib-run-chip'));
    }

    if (kind === 'materials') root.append(collection(page));

    const layout = node('div', '', 'lib-layout');
    const master = node('section', '', 'lib-master');
    master.setAttribute('aria-label', '结果列表');
    page.message = node('div', '', 'lib-message');
    page.message.setAttribute('role', 'status');
    page.list = node('div', '', 'lib-list');
    const pager = node('div', '', 'lib-pager');
    page.position = node('small');
    page.prev = button('上一页', () => {
      page.offset -= limit;
      load(page);
    });
    page.next = button('下一页', () => {
      page.offset += limit;
      load(page);
    });
    pager.append(page.prev, page.position, page.next);
    master.append(page.message, page.list, pager);
    page.detail = node('article', '', 'lib-detail');
    layout.append(master, page.detail);
    root.append(layout);

    await load(page, options.key == null ? null : String(options.key));
  };

  function bind() {
    for (const kind of Object.keys(names)) {
      const link = byId(kind + 'Nav');
      if (link) link.onclick = handle(() => window.openLibrary(kind));
    }
    byId('project')?.addEventListener('change', resetProject);
    const root = byId('library');
    if (root) {
      new MutationObserver(() => {
        if (root.hidden) {
          clearTimeout(session?.timer);
          session = null;
          navigation(null);
        }
      }).observe(root, {attributes: true, attributeFilter: ['hidden']});
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind, {once: true});
  else bind();
})();
