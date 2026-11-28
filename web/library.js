(() => {
  const names = {materials: '素材库', topics: '选题库', drafts: '成稿库'};
  const descriptions = {
    materials: '按来源、状态或批次检索原始与清洗后正文，支持跨来源勾选合并为新分析批次。',
    topics: '对照推荐理由与跨素材原句证据，核实独立来源后一键进入内容生产。',
    drafts: '回查最新修改的多版本成稿，直接导出 Markdown 或反向追溯原始素材。'
  };
  const limit = 20;
  let session;
  let basket;
  const baskets = new Map();
  const defaultName = '素材合辑';

  function projectBasket(project) {
    if (baskets.has(project)) return baskets.get(project);
    const current = {project, items: new Map(), name: defaultName};
    try {
      const saved = JSON.parse(localStorage.getItem('dm.library.collection.' + project));
      if (typeof saved?.name === 'string') current.name = saved.name;
      for (const item of (Array.isArray(saved?.items) ? saved.items.slice(0, 100) : [])) {
        if (Number.isSafeInteger(item?.run_id) && item.run_id > 0 && Number.isSafeInteger(item.material_id) && item.material_id > 0) {
          current.items.set(materialKey(item), {run_id: item.run_id, material_id: item.material_id, title: item.title, source: item.source});
        }
      }
      if (typeof saved?.requestKey === 'string') {
        current.requestKey = saved.requestKey;
        current.payload = saved.payload;
      }
    } catch {}
    baskets.set(project, current);
    return current;
  }

  function saveBasket(current) {
    const key = 'dm.library.collection.' + current.project;
    try {
      if (!current.items.size && current.name === defaultName) localStorage.removeItem(key);
      else localStorage.setItem(key, JSON.stringify({name: current.name, items: [...current.items.values()],
        requestKey: current.requestKey, payload: current.payload}));
    } catch {}
  }

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
    const batch = item.batch_name && (kind !== 'materials' || item.batch_name !== item.source)
      ? '批次：' + item.batch_name
      : '';
    return [item.source, batch, kind === 'topics' ? '确认选题' : kind === 'drafts' ? '生产成稿 · v' + item.version : '', item.status].filter(Boolean).join(' · ');
  }

  function activePage(page) {
    return page === session && projectId === page.project && !page.root.hidden;
  }

  window.captureLibraryView = function () {
    const page = session;
    if (!page || !activePage(page)) return null;
    const children = [...page.root.children];
    const reading = page.root.classList.contains('lib-reading');
    const scroll = window.scrollY;
    const listScroll = page.list.scrollTop;
    const focus = page.root.contains(document.activeElement) ? document.activeElement : null;
    return {name: names[page.kind], async restore() {
      if (page.project !== projectId) return;
      session = page;
      basket = projectBasket(page.project);
      view('library', {kind:page.kind, options:page.options});
      if (page.root.firstElementChild !== children[0]) page.root.replaceChildren(...children);
      page.root.classList.toggle('lib-reading', reading);
      navigation(page.kind);
      if (page.pending) await load(page, page.pending.wanted);
      if (activePage(page)) {
        if (page.kind === 'materials') updateBasket(page);
        if (focus?.isConnected) focus.focus({preventScroll: true});
        page.list.scrollTop = listScroll;
        window.scrollTo({top: scroll, behavior: 'instant'});
      }
    }};
  };

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

  // Only construct known elements. Raw HTML and unsupported Markdown stay readable as text.
  function inlineMarkdown(parent, text) {
    const tokens = /`([^`\n]+)`|\*\*([^*\n]+)\*\*|\*([^*\n]+)\*|\[([^\[\]\n]+)\]\((https?:\/\/[^\s)]+)\)/gi;
    let end = 0;
    for (const match of text.matchAll(tokens)) {
      parent.append(text.slice(end, match.index));
      const [, code, strong, em, label, address] = match;
      if (address) {
        let link;
        try {
          const url = new URL(address);
          if (!url.username && !url.password) {
            link = node('a', label);
            link.href = url.href;
            link.rel = 'noopener noreferrer';
            link.target = '_blank';
          }
        } catch {}
        parent.append(link || match[0]);
      } else parent.append(node(code ? 'code' : strong ? 'strong' : 'em', code || strong || em));
      end = match.index + match[0].length;
    }
    parent.append(text.slice(end));
  }

  function markdown(value) {
    const body = node('div', '', 'lib-markdown');
    let block, fence, codeLines;
    for (const line of value.split(/\r\n?|\n/)) {
      if (fence) {
        if (line.trim().startsWith(fence) && /^`+$/.test(line.trim())) {
          block.firstChild.textContent = codeLines.join('\n');
          block = fence = null;
        } else codeLines.push(line);
        continue;
      }
      const marker = line.match(/^\s*(`{3,})[^`]*$/);
      if (marker) {
        fence = marker[1];
        block = node('pre');
        block.append(node('code'));
        body.append(block);
        codeLines = [];
        continue;
      }
      if (!line.trim()) { block = null; continue; }
      const heading = line.match(/^\s*(#{1,6})\s+(.+)$/);
      if (heading) {
        const title = node('h' + Math.min(6, heading[1].length + 2));
        inlineMarkdown(title, heading[2]);
        body.append(title);
        block = null;
        continue;
      }
      const list = line.match(/^\s*([-*+]|\d+[.)])\s+(.*)$/);
      const quote = line.match(/^\s*>\s?(.*)$/);
      const tag = list ? (/\d/.test(list[1]) ? 'ol' : 'ul') : quote ? 'blockquote' : 'p';
      if (block?.localName !== tag) {
        block = node(tag);
        if (tag === 'ol') block.start = parseInt(list[1], 10);
        body.append(block);
      } else if (!list) block.append('\n');
      if (list) {
        const item = node('li');
        inlineMarkdown(item, list[2]);
        block.append(item);
      } else inlineMarkdown(block, quote ? quote[1] : line);
    }
    if (fence) block.firstChild.textContent = codeLines.join('\n');
    return body;
  }

  function content(parent, heading, value) {
    if (!value) return;
    parent.append(node('h3', heading), markdown(value));
  }

  function original(parent, value) {
    if (!value) return;
    const fold = node('details');
    fold.append(node('summary', '查看原文'), markdown(value));
    parent.append(fold);
  }

  function references(page, item) {
    const fold = node('details');
    const results = node('div', '', 'lib-references');
    const message = node('p');
    message.setAttribute('role', 'status');
    fold.append(node('summary', '展开关联选题与稿件'), message, results);
    let started = false;
    const current = () => page.selected === item && page.detail.contains(fold);

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
            results.append(button(entry.title || '未命名', () => window.openLibrary(kind, {run_id: entry.run_id, key: entry.key,
              origin: window.captureLibraryView(), originLabel: '返回来源素材'})));
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
      materials: '项目里还没有素材。导入第一份文件，或添加采集来源。',
      topics: '项目里还没有选题。先从素材库打开一个批次，完成清洗与主题分析。',
      drafts: '项目里还没有成稿。前往选题库确认选题后，就可以生成第一份草稿。'
    };
    const filtered = page.run != null || page.options.partial_only || [page.query, page.status, page.source, page.since, page.until].some(input => input?.value.trim());
    if (filtered) {
      box.append(node('p', '当前筛选条件下没有结果。'), button('清除全部筛选', () => {
        page.query.value = page.status.value = page.source.value = '';
        if (page.since) page.since.value = page.until.value = page.since.max = page.until.min = '';
        page.run = null;
        delete page.options.partial_only;
        delete page.options.run_id;
        page.root.querySelectorAll('.lib-run-chip').forEach(chip => chip.remove());
        page.offset = 0;
        page.query.focus();
        load(page);
      }));
    } else {
      box.append(node('p', emptyGuide[page.kind]), buildHeaderActions(page.kind));
    }
    page.list.append(box);
  }

  function draftBody(parent, item) {
    const body = node('div', '正在读取正文…', 'lib-prose');
    parent.append(node('h3', '稿件正文'), body);
    async function read() {
      try {
        if (item.body == null) {
          const response = await fetch('/api/runs/' + encodeURIComponent(item.run_id) + '/export?version=' + encodeURIComponent(item.version));
          if (!response.ok) throw Error();
          item.body = await response.text();
        }
        body.replaceChildren(item.body ? markdown(item.body) : '此版本没有正文。');
      } catch {
        body.replaceChildren('正文读取失败。', button('重试', read));
        if (item.excerpt) body.append(node('span', '\n稿件节选\n' + item.excerpt));
      }
    }
    read();
  }

  function select(page, item, reveal, version = item?.version) {
    page.selected = item;
    const detail = page.detail;
    detail.replaceChildren();
    for (const [index, row] of page.rows.entries()) {
      if (page.items[index] === item) row.setAttribute('aria-current', 'true');
      else row.removeAttribute('aria-current');
    }
    if (!item) {
      if (!page.items.length) renderEmptyDetail(page);
      return;
    }

    const back = button('返回结果列表', () => {
      page.root.classList.remove('lib-reading');
      page.rows[page.items.indexOf(item)]?.focus();
    }, 'lib-back');

    const heading = node('h2', item.title || '未命名');
    heading.tabIndex = -1;
    detail.append(back, node('small', (page.kind === 'drafts' && version !== item.version ? '最新稿 · ' : '') + itemMeta(page.kind, item)), heading);

    const actionLabel = page.kind === 'drafts'
      ? (version === item.version ? '编辑与审核稿件' : '打开最新版编辑与审核')
      : page.kind === 'topics' && item.status === '候选'
        ? '使用此选题'
        : '打开所属批次';

    const actionBar = node('div', '', 'lib-detail-actions');
    const openRunBtn = button(actionLabel, handle(async () => {
      const revision = page.revision;
      const run = await request('/' + encodeURIComponent(item.run_id));
      if (activePage(page) && page.selected === item && page.revision === revision) {
        const options = {...page.options, list: {project: page.project, offset: page.offset, key: item.key, run: item.run_id, version,
          query: page.query.value, source: page.source.value, status: page.status.value, since: page.since?.value, until: page.until?.value}};
        recordRoute('library', {kind: page.kind, options}, true);
        await show(run, page.kind === 'topics' ? item.key : undefined);
      }
    }), 'lib-open');
    actionBar.append(openRunBtn);

    if (page.kind === 'drafts') {
      const picker = node('select');
      for (let number = item.version; number >= 1; number--) picker.add(new Option('v' + number, number));
      picker.value = version;
      picker.onchange = () => {
        select(page, item, false, Number(picker.value));
        window.DMSelect?.refresh(page.detail);
        (page.detail.querySelector('[role="combobox"]') || page.detail.querySelector('select')).focus();
      };
      actionBar.append(field('稿件版本', picker));
      const exportLink = node('a', '导出 v' + version + ' (.md)', 'download');
      exportLink.href = '/api/runs/' + encodeURIComponent(item.run_id) + '/export?version=' + version;
      actionBar.append(exportLink);
      if (version === item.version && item.status === '通过') {
        actionBar.append(button('准备发布', () => window.openPublishing({
          title: item.title, run_id: item.run_id, version: item.version
        }), 'secondary'));
      }
    }
    detail.append(actionBar);

    if (page.kind === 'materials') {
      window.renderCollectionMedia?.(detail, item, page.project, async (report, difference) => {
        if (!activePage(page) || page.selected !== item) return;
        const keep = !page.options.partial_only || report.status === 'partial';
        if (keep) page.restoreList = {run:item.run_id};
        await load(page, keep ? item.key : null);
        if (activePage(page)) notify(item.title + ' · ' + difference, 'ok');
      });
      if (item.created) detail.append(node('small', '批次日期 · ' + batchCalendarDay(item.created)));
      if (item.source_url) {
        try {
          const url = new URL(item.source_url);
          if (['http:', 'https:'].includes(url.protocol) && !url.username && !url.password) {
            const link = node('a', '打开平台原文', 'download');
            link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
            actionBar.append(link);
          }
        } catch {}
      }
      if (item.content_level) {
        const levels = {title_only:'仅标题', summary:'列表简介', visible_text:'列表文字', detail_text:'作品详情文字', subtitle:'视频字幕', transcript:'音频转写'};
        detail.append(node('p', '采集深度 · ' + (levels[item.content_level] || item.content_level)));
        if (item.media_parts > 1) detail.append(node('small', `多分集视频 · 共 ${item.media_parts} 集，字幕来自第 ${item.media_part || 1} 集`));
        if (item.detail_error) detail.append(node('p', item.detail_error, 'fieldHelp'));
        if (item.description_text && item.description_text !== item.original) content(detail, '作品简介', item.description_text);
        if (item.segments?.length) {
          const subtitles = node('details');
          subtitles.append(node('summary', `带时间字幕（${item.segments.length} 段）`));
          const stamp = value => `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2,'0')}`;
          for (const segment of item.segments) subtitles.append(node('p', `${stamp(segment.start)}–${stamp(segment.end)}　${segment.text}`));
          detail.append(subtitles);
        }
      }
      if (item.processing_status) {
        detail.append(node('p', item.processing_status === 'complete' ? `内容解析 · 已完成${item.visual_frames?.length ? '（视频画面为关键帧采样）' : ''}` : '内容解析 · 部分完成'));
        for (const missing of item.processing_missing || []) detail.append(node('p', missing, 'fieldHelp'));
        if (item.analysis_status === 'ready' && item.analysis) {
          const analysis = item.analysis;
          content(detail, '内容摘要（AI 解析）', analysis.summary || '');
          if (analysis.relevance) detail.append(node('p', `方向匹配 · ${analysis.relevance.matches ? '符合' : '待复核'} · ${analysis.relevance.reason || ''}`));
          for (const claim of analysis.claims || []) detail.append(claimEvidence(page, item, claim));
          if (analysis.steps?.length) content(detail, '内容步骤（AI 解析）', analysis.steps.map((step, i) => `${i + 1}. ${typeof step === 'string' ? step : step.text || ''}`).join('\n'));
          const evidence = node('details');
          evidence.append(node('summary', '查看解析依据'));
          for (const row of item.evidence || []) evidence.append(node('p', `${row.id} · ${row.text || ''}`));
          detail.append(evidence);
        }
        for (const asset of [...(item.image_assets || []), ...(item.frame_assets || [])]) {
          if (!/^[a-f0-9]{64}$/.test(asset.id)) continue;
          const figure = node('figure', '', 'collectionImage');
          const image = node('img');
          image.src = `/api/projects/${encodeURIComponent(page.project)}/collection-images/${asset.id}`;
          const caption = asset.time != null ? `视频采样关键帧 · ${Math.floor(asset.time / 60)}:${String(Math.floor(asset.time % 60)).padStart(2, '0')}` : `原图 ${asset.index} · 按作品中的顺序保存`;
          image.alt = caption; image.loading = 'lazy';
          figure.append(image, node('figcaption', caption));
          detail.append(figure);
        }
        for (const image of item.image_texts || []) content(detail, `图片 ${image.index} · 文字识别`, image.text || image.error || '未完成');
        for (const image of item.image_observations || []) content(detail, `图片 ${image.index} · 画面观察（AI 解析）`, image.text || '未完成');
        if (item.visual_frames?.length) {
          const frames = node('details');
          frames.append(node('summary', `视频画面记录（${item.visual_frames.length} 个采样时间点）`));
          for (const frame of item.visual_frames) frames.append(node('p', `${Math.floor(frame.time / 60)}:${String(Math.floor(frame.time % 60)).padStart(2, '0')} · ${frame.text || '画面尚未解析'}`));
          detail.append(frames);
        }
      }
      if (item.status === '待清洗') content(detail, '原文（待清洗）', item.original);
      else {
        content(detail, '清洗文本', item.cleaned);
        original(detail, item.original);
      }
      if (!item.original && !item.cleaned) content(detail, '素材节选', item.excerpt);
      detail.append(references(page, item));
    } else {
      if (page.kind === 'topics') {
        content(detail, '推荐理由', item.reason || item.excerpt);
      } else {
        const meta = [
          '当前查看 v' + version,
          item.updated && version === item.version ? '最近修改 · ' + new Date(item.updated).toLocaleString() : '此版本未记录修改时间'
        ];
        if (meta.length) detail.append(node('small', meta.join(' · ')));
        draftBody(detail, version === item.version ? item : {run_id:item.run_id, version});
      }
      const evidence = item.evidence || [];
      detail.append(node('h3', '引用依据（' + evidence.length + '）'));
      if (!evidence.length) detail.append(node('p', '没有记录引用依据，待复核。'));
      if (item.source_count != null) detail.append(node('small', `${evidence.length} 份素材 · ${item.source_count} 个来源标签（文件或批次名称），来源独立性待核实。`));
      for (const entry of evidence) {
        const block = node('section', '', 'lib-evidence');
        block.append(node('strong', entry.title || '素材 ' + entry.id));
        if (entry.source) block.append(node('small', entry.source, 'lib-origin'));
        block.append(entry.quote ? node('blockquote', entry.quote) : node('p', '缺少引用原句，待复核。'));
        original(block, entry.original);
        if (entry.run_id != null && entry.material_id != null) {
          block.append(button('打开原始素材', () => window.openLibrary('materials', {
            run_id: entry.run_id,
            key: String(entry.material_id), origin: window.captureLibraryView(), originLabel: page.kind === 'topics' ? '返回来源选题' : '返回来源稿件'
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

  function claimEvidence(page, item, claim) {
    const fold = node('details', '', 'lib-evidence');
    const ids = claim.evidence_ids || [];
    const entries = ids.map(id => (item.evidence || []).find(row => row.id === id));
    const missing = !entries.length || entries.some(row => !row?.text);
    fold.append(node('summary', claim.text + (missing ? ' · 依据缺失，待复核' : ' · 查看依据')));
    entries.forEach((row, index) => {
      const id = ids[index];
      if (!row?.text) {
        fold.append(node('p', id + ' · 引用已失效'));
        return;
      }
      const [kind, ordinal] = id.split(':');
      const field = {image:'image_texts', image_view:'image_observations', frame:'visual_frames', subtitle:'segments'}[kind];
      const record = item[field]?.[Number(ordinal) - 1];
      const time = record?.time ?? record?.start;
      fold.append(node('small', id + (Number.isFinite(time) ? ' · ' + Math.floor(time / 60) + ':' + String(Math.floor(time % 60)).padStart(2, '0') : '')), node('blockquote', row.text));
      const asset = kind === 'frame' ? item.frame_assets?.find(asset => time != null && asset.time === time)
        : ['image','image_view'].includes(kind) && item.image_assets?.find(asset => record?.index != null && asset.index === record.index);
      if (asset && /^[a-f0-9]{64}$/.test(asset.id)) {
        const image = node('img');
        image.loading = 'lazy';
        image.alt = id + ' 对应画面';
        image.onerror = () => image.replaceWith(node('p', '画面不可用，待复核'));
        image.src = '/api/projects/' + page.project + '/collection-images/' + asset.id;
        fold.append(image);
      }
    });
    return fold;
  }

  function materialKey(item) {
    return JSON.stringify([item.run_id, String(item.material_id ?? item.key)]);
  }

  function updateBasket(page) {
    const count = basket.items.size;
    if (page.name.value !== basket.name) page.name.value = basket.name;
    page.count.textContent = '已选 ' + count + ' / 100';
    page.review.hidden = !count;
    const existing = new Map([...page.reviewList.children].map(line => [line.dataset.key,line]));
    const sources = new Set();
    for (const [key, item] of basket.items) {
      const source = item.source || '批次 #' + item.run_id;
      sources.add(source);
      const previous = existing.get(key);
      if (previous) {
        previous.querySelector('button').disabled = basket.busy;
        existing.delete(key);
        continue;
      }
      const line = node('li');
      line.dataset.key = key;
      const remove = button('移除', () => {
        basket.items.delete(key);
        saveBasket(basket);
        updateBasket(page);
        (page.review.hidden ? page.name : page.review.firstChild).focus();
      });
      remove.disabled = basket.busy;
      line.append(node('span', source + ' · ' + (item.title || '素材 #' + item.material_id)), remove);
      page.reviewList.append(line);
    }
    for (const removed of existing.values()) removed.remove();
    page.review.firstChild.textContent = '核对已选素材 · ' + [...sources].join('、');
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
    page.review = node('details', '', 'lib-review');
    page.reviewList = node('ul');
    page.review.append(node('summary'), page.reviewList);
    page.count.setAttribute('role', 'status');
    page.name = node('input');
    page.name.required = true;
    page.name.value = basket.name;
    page.name.oninput = () => {
      basket.name = page.name.value;
      saveBasket(basket);
    };
    page.clear = button('清空选择', () => {
      basket.items.clear();
      saveBasket(basket);
      updateBasket(page);
    });
    page.create = node('button', '合并为新批次', 'lib-submit');
    page.create.type = 'submit';
    form.append(page.count, page.clear, field('新批次名称', page.name), page.create);
    form.append(page.review);

    form.onsubmit = async event => {
      event.preventDefault();
      if (!activePage(page) || !basket.items.size || basket.busy) return;
      const current = basket;
      const name = page.name.value.trim();
      if (!name) {
        page.name.focus();
        return;
      }
      const items = [...current.items.values()].map(({run_id, material_id}) => ({run_id, material_id}));
      const payload = JSON.stringify({name, items});
      current.busy = true;
      updateBasket(page);
      try {
        if (current.payload !== payload) {
          current.requestKey = window.createWorkbenchRequestId();
          current.payload = payload;
          saveBasket(current);
        }
        const run = await api('/api/projects/' + page.project + '/collections', 'POST', {
          name,
          request_key: current.requestKey,
          items
        });
        current.items.clear();
        current.name = defaultName;
        current.payload = current.requestKey = null;
        saveBasket(current);
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
    page.snapshot = JSON.stringify(result);
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
            basket.items.set(key, {run_id: item.run_id, material_id: Number(item.material_id ?? item.key), title: item.title, source: item.source});
          } else {
            basket.items.delete(key);
          }
          saveBasket(basket);
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
    page.message.textContent = result.total ? '共 ' + result.total + ' 条' : '没有找到匹配的条目。';
    page.prev.disabled = page.offset === 0;
    page.next.disabled = page.offset + limit >= result.total;
    page.position.textContent = result.total
      ? (page.offset + 1) + '–' + (page.offset + page.items.length)
      : '0';
    const saved = page.restoreList;
    const match = page.items.find(item => item.key === wanted && (!saved || item.run_id === saved.run));
    select(page, wanted == null ? page.items[0] : match, Boolean(match && !saved), saved?.version);
    if (saved && match && !saved.background) page.rows[page.items.indexOf(match)].focus();
    if (wanted != null && !match) page.message.textContent = '该条目已不可用。';
    page.restoreList = null;
    const conditions = [['搜索',page.query.value.trim()], ['状态',page.status.value.trim()], ['来源',page.source.value],
      ['批次',page.run], ['起始日期',page.since?.value], ['结束日期',page.until?.value], ['解析',page.options.partial_only && '有缺项']];
    page.filtersApplied.textContent = conditions.filter(([,value]) => value).map(([label,value]) => label + '：' + value).join(' · ');
  }

  async function load(page, wanted, delay = 0, background = false) {
    if (background && page.pending) return;
    clearTimeout(page.timer);
    const revision = ++page.revision;
    page.pending = {wanted};
    if (!background) {
      page.root.classList.remove('lib-reading');
      page.selected = null;
      page.detail.replaceChildren();
      page.list.replaceChildren();
      page.position.textContent = '';
      page.prev.disabled = page.next.disabled = true;
      page.message.textContent = '正在读取…';
    }
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
            partial_only: page.options.partial_only,
            since: page.since?.value,
            until: page.until?.value
          })) {
            if (value != null && value !== '') params.set(key, value);
          }
          if (page.since?.value) params.set('since_offset', new Date(page.since.value + 'T00:00:00').getTimezoneOffset());
          if (page.until?.value) {
            const boundary = new Date(page.until.value + 'T00:00:00');
            boundary.setDate(boundary.getDate() + 1);
            params.set('until_offset', boundary.getTimezoneOffset());
          }
          const result = await api('/api/projects/' + page.project + '/library/' + page.kind + '?' + params);
          if (!fresh()) return;
          if (!page.restoreList && wanted != null && result.items.length && page.offset + limit < result.total && !result.items.some(item => item.key === wanted)) {
            page.offset += limit;
            continue;
          }
          if (page.offset && !result.items.length) {
            page.offset = Math.max(0, Math.floor((result.total - 1) / limit) * limit);
            continue;
          }
          page.pending = null;
          if (background) {
            if (JSON.stringify(result) === page.snapshot) return;
            const selected = page.selected;
            const reading = page.root.classList.contains('lib-reading');
            const focus = document.activeElement;
            const rowIndex = page.rows.indexOf(focus.closest('.lib-row')?.querySelector('.lib-item'));
            const focusedKey = rowIndex < 0 ? null : materialKey(page.items[rowIndex]);
            const scroll = page.list.scrollTop;
            const versionPicker = page.detail.querySelector('select');
            const focusedDetail = [...page.detail.querySelectorAll('button,a,select,summary')].indexOf(focus);
            page.restoreList = selected && {run:selected.run_id, background:true, version:versionPicker ? Number(versionPicker.value) : undefined};
            draw(page, result, selected?.key);
            page.root.classList.toggle('lib-reading', reading && Boolean(page.selected));
            const index = page.items.findIndex(item => materialKey(item) === focusedKey);
            const target = index >= 0 ? (focus.type === 'checkbox' ? page.checks.get(focusedKey) : page.rows[index])
              : focus.isConnected ? focus : page.detail.querySelectorAll('button,a,select,summary')[focusedDetail];
            target?.focus({preventScroll:true});
            page.list.scrollTop = scroll;
            return;
          }
          draw(page, result, wanted);
          return;
        }
      } catch (error) {
        if (fresh()) {
          page.pending = null;
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
    basket = projectBasket(projectId);
    const page = {kind, project: projectId, root, run: options.run_id, offset: 0, revision: 0, checks: new Map()};
    session = page;
    page.options = {run_id:options.run_id, key:options.key};
    const saved = options.list?.project === projectId ? options.list : null;
    if (saved) page.options.list = saved;
    if (kind === 'materials' && options.partial_only) page.options.partial_only = true;
    view('library', {kind, options:page.options});
    root.hidden = false;
    navigation(kind);

    const head = node('div', '', 'lib-head');
    const titleWrap = node('div');
    titleWrap.append(node('h1', names[kind]), node('p', descriptions[kind]));
    if (options.origin) titleWrap.prepend(button(options.originLabel, handle(() => options.origin.restore()), 'ghost'));
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
    page.filtersApplied = node('small', '', 'lib-applied');
    root.append(page.filtersApplied);

    if (kind === 'materials') {
      const dates = node('details', '', 'lib-dates');
      dates.append(node('summary', '按批次日期筛选（本地）'));
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

    if (page.options.partial_only) {
      root.append(button('解析有缺项 · 清除限定', event => {
        delete page.options.partial_only;
        event.currentTarget.remove();
        refresh(0);
      }, 'lib-run-chip'));
    }
    if (page.run != null) {
      root.append(button('批次 ' + page.run + ' · 清除限定', event => {
        page.run = null;
        delete page.options.run_id;
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

    if (saved) {
      page.restoreList = saved;
      page.offset = saved.offset;
      page.query.value = saved.query;
      page.status.value = saved.status;
      if (saved.source) page.source.add(new Option(saved.source, saved.source, true, true));
      if (page.since) {
        page.since.value = page.until.min = saved.since;
        page.until.value = page.since.max = saved.until;
        page.since.closest('details').open = Boolean(saved.since || saved.until);
      }
    }
    await load(page, saved?.key ?? (options.key == null ? null : String(options.key)));
  };

  function bind() {
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && session && activePage(session)) load(session, null, 0, true);
    });
    for (const kind of Object.keys(names)) {
      const link = byId(kind + 'Nav');
      if (link) link.onclick = handle(() => window.openLibrary(kind));
    }
    byId('project')?.addEventListener('change', resetProject, true);
    const root = byId('library');
    if (root) {
      new MutationObserver(() => {
        if (root.hidden) {
          clearTimeout(session?.timer);
          session = null;
        }
      }).observe(root, {attributes: true, attributeFilter: ['hidden']});
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind, {once: true});
  else bind();
})();
