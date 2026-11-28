// Chrome serializes this function into the active tab's isolated world.
export function collectPage(task, expectedUrl, knownIds, advance = false) {
  if (location.href !== expectedUrl) throw Error('页面地址已改变。');
  const specs = {
    instagram: ['main, [role="main"]','article, a[href*="/p/"], a[href*="/reel/"]','www.instagram.com',/^\/(?:[\w.]+\/)?(p|reel)\/([\w-]+)\/?$/],
    tiktok: [task.mode === 'account' ? '[data-e2e="user-post-item-list"]' : '[data-e2e="search_video-item-list"], main, [role="main"]','[data-e2e="user-post-item"], [data-e2e="search_video-item"], [class*="DivItemContainer"]','www.tiktok.com',/^\/@([\w.]+)\/(video)\/(\d+)\/?$/],
    douyin: [task.mode === 'account' ? '[data-e2e="user-post-list"]' : '#search-content-area, main, [role="main"]','li, [data-e2e="search-result-card"], [class*="videoCard"]','www.douyin.com',/^\/(video|note)\/(\d+)\/?$/],
    xiaohongshu: [task.mode === 'account' ? '#userPostedFeeds' : '.feeds-container, .search-result-container','section.note-item, .note-item','www.xiaohongshu.com',/^\/(?:explore|discovery\/item|search_result|user\/profile\/[\w-]+)\/([a-f\d]{24})\/?$/i],
    bilibili: [task.mode === 'account' ? '.submit-video-list, .space-upload, .section.video, .upload-video, .space-home__section.video, .masterpiece-block, .section-wrap.video-section' : '.search-page, .video-list, .search-content, main','.bili-video-card, .small-item, .bili-video-card__wrap, .video-card','www.bilibili.com',/^\/video\/(BV[a-zA-Z0-9]{10}|av\d+)\/?$/],
    x: ['[data-testid="primaryColumn"]','article[data-testid="tweet"]','x.com',/^\/([\w]+)\/status\/(\d+)\/?$/],
  };
  const [root,card,host,pattern] = specs[task.platform];
  const visible = node => !!node?.getClientRects().length && getComputedStyle(node).visibility === 'visible';
  const text = node => visible(node) ? (node.innerText || '').trim() : '';
  if ([...document.querySelectorAll('[aria-modal="true"], dialog[open], [role="dialog"]')].some(visible)) return {items:[],next:null};
  const accountNames = {bilibili:'.upinfo-detail__top .nickname',tiktok:'[data-e2e="user-title"]',xiaohongshu:'.user-info .user-name',douyin:'[data-e2e="user-info"] h1'};
  const profileName = task.mode === 'account' && accountNames[task.platform] ? text(document.querySelector(accountNames[task.platform])) : '';
  const known = new Set(knownIds);
  const remaining = task.limit - known.size;
  const seen = new Map();
  for (const a of document.querySelectorAll(`:is(${root}) a[href]`)) {
    if (!visible(a) || a.closest('aside, nav, header, [role="dialog"], [class*="recommend-list"], [data-testid="placementTracking"]')) continue;
    if (typeof a.href !== 'string' || !a.href.startsWith('https://')) continue;
    let u;
    try { u = new URL(a.href); } catch { continue; }
    if (u.origin !== `https://${host}` || u.username || u.password) continue;
    const m = pattern.exec(u.pathname);
    if (!m) continue;
    const container = (task.platform === 'instagram' && a.closest('article')) || a.closest(card);
    if (!container || !container.closest(root)) continue;
    if (task.platform === 'x' && !a.querySelector('time')) continue;
    if (task.mode === 'account' && ['tiktok','x'].includes(task.platform) && m[1].toLowerCase() !== new URL(task.url).pathname.split('/')[1].replace(/^@/,'').toLowerCase()) continue;
    if (task.mode === 'account' && task.platform === 'instagram' && m[0].split('/').filter(Boolean).length === 3 && !m[0].toLowerCase().startsWith(`/${task.query.toLowerCase()}/`)) continue;
    const field = selector => text(container.querySelector(selector));
    const author = (field('[data-testid="User-Name"], [data-e2e="video-author-uniqueid"], .author-wrapper .name, .up-name, .bili-video-card__info--author, [class*="author-name"]') || profileName).slice(0,256);
    const body = field('[data-testid="tweetText"], [data-e2e="user-post-item-desc"], [data-e2e="search-card-desc"], [data-e2e="video-desc"], [data-e2e="search-card-video-caption"], [data-e2e="video-card-desc"], .desc, .caption, figcaption').slice(0,8000);
    const linkText = task.platform === 'x' ? '' : text(a).split('\n').find(line => /\p{L}/u.test(line) && !/^[\d.,\s]+[万亿kKmM]$/.test(line));
    const title = (field('h3, h2, .title, .bili-video-card__title, .bili-video-card__info--tit, [data-e2e="search-card-desc"]') || body || linkText || '').split('\n')[0].slice(0,240);
    if (!title && !body) continue;
    const external_id = m.at(-1);
    if (known.has(external_id)) continue;
    let path = u.pathname.replace(/\/$/,'');
    if (task.platform === 'xiaohongshu') path = `/explore/${external_id}`;
    if (task.platform === 'instagram') path = `/${m[1]}/${external_id}/`;
    const record = {external_id,title,body,url:u.origin + path,author};
    if (task.platform === 'xiaohongshu' && u.searchParams.get('xsec_token')) record.access_url = u.href;
    seen.set(external_id,record);
    if (seen.size >= remaining) break;
  }
  let next = null;
  if (task.platform === 'bilibili' && task.mode === 'account') {
    const uploads = new URL(task.url).pathname.replace(/\/$/,'') + '/upload/video';
    const link = [...document.querySelectorAll('a[href]')].find(a => visible(a) && a.href === location.origin + uploads);
    if (link) next = link.href;
    else if (location.pathname !== uploads && (seen.size || known.size)) next = location.origin + uploads;
    if (advance && location.pathname === uploads && !seen.size) {
      const button = document.querySelector('.space-upload button.vui_pagenation--btn[aria-label="下一页"]');
      if (visible(button) && !button.disabled && button.getAttribute('aria-disabled') !== 'true') button.click();
    }
  }
  if (task.platform === 'bilibili' && task.mode === 'topic') {
    const control = [...document.querySelectorAll('.vui_pagenation button, .vui_pagenation a[href], .pagination a[href], .vui_pagenation--btn')].find(n => visible(n) && !n.disabled && n.getAttribute('aria-disabled') !== 'true' && /^(下一页|Next)$/i.test(text(n)));
    if (control) {
      const page = new URL(expectedUrl);
      page.searchParams.set('page', String(Number(page.searchParams.get('page') || 1) + 1));
      page.searchParams.delete('o');
      next = control.href || page.href;
    }
  }
  const last = [...document.querySelectorAll(card)].filter(n => n.closest(root) && visible(n)).at(-1);
  if (last && seen.size < remaining) last.scrollIntoView({block:'end'});
  return {items:[...seen.values()],next};
}
