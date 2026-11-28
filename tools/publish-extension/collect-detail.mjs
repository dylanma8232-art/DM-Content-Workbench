// Self-contained: used by both Chrome executeScript and public Playwright reads.
export function collectDetail(task, item) {
  const result = {...item, content_level: item.content_level || (item.body ? 'summary' : 'title_only'), depth_status:'detail_unavailable', detail_error:'详情页未提供可读取正文'};
  const patterns = {
    instagram:['www.instagram.com', /^\/(?:p|reel)\/([\w-]+)\/?$/],
    tiktok:['www.tiktok.com', /^\/@[\w.]+\/video\/(\d+)\/?$/],
    douyin:['www.douyin.com', /^\/(?:video|note)\/(\d+)\/?$/],
    xiaohongshu:['www.xiaohongshu.com', /^\/(?:explore|discovery\/item)\/([a-f\d]{24})\/?$/i],
    bilibili:['www.bilibili.com', /^\/video\/(BV[A-Za-z0-9]{10}|av\d+)\/?$/],
    x:['x.com', /^\/[\w]+\/status\/(\d+)(?:\/photo\/\d+)?\/?$/]
  };
  const rule = patterns[task.platform];
  if (!rule) return result;
  const current = new URL(location.href);
  const match = rule[1].exec(current.pathname);
  if (current.protocol !== 'https:' || current.host !== rule[0] || !match || match[1] !== item.external_id) {
    return {...result, depth_status:'login_required', detail_error:'页面跳转或要求登录，未读取目标作品详情'};
  }
  const visible = n => !!n?.getClientRects().length && getComputedStyle(n).visibility !== 'hidden';
  const specs = {
    bilibili:['body', '.video-desc-container, .basic-desc-info, .desc-info-text', '.video-info-title'],
    xiaohongshu:['#noteContainer, .note-container', '#detail-desc, .note-text', '#detail-title'],
    douyin:['[data-e2e="detail-video-info"], [data-e2e="video-detail"], main', '[data-e2e="video-desc"], [data-e2e="detail-video-desc"]', '[data-e2e="detail-video-title"]'],
    tiktok:['[data-e2e="browse-video-desc"], [data-e2e="video-detail"], main', '[data-e2e="browse-video-desc"], [data-e2e="video-desc"]', '[data-e2e="browse-video-desc"]'],
    instagram:['main article, [role="dialog"] article', 'h1, [data-testid="post-caption"]', 'h1'],
    x:['article[data-testid="tweet"]', '[data-testid="tweetText"]', '']
  };
  const [rootSelector, textSelector, titleSelector] = specs[task.platform];
  let roots = [...document.querySelectorAll(rootSelector)].filter(visible);
  if (task.platform === 'x') roots = roots.filter(root => [...root.querySelectorAll('a[href]')].some(a => {
    try { return new URL(a.href).pathname.match(/\/status\/(\d+)/)?.[1] === item.external_id; } catch { return false; }
  }));
  const root = roots[0];
  if (!root) return result;
  const mediaSelectors = {
    xiaohongshu: '.note-slider img, .swiper-slide img, .media-container img',
    instagram: 'img[alt]', x: '[data-testid="tweetPhoto"] img',
    douyin: '[data-e2e="detail-image"] img, .swiper-slide img',
    tiktok: '[data-e2e="photo-image"] img', bilibili: '.video-cover img'
  };
  const images = [...root.querySelectorAll(mediaSelectors[task.platform])].filter(img => visible(img) && !img.closest('.swiper-slide-duplicate, .slick-cloned'));
  const slideIndex = img => Number(img.closest('[data-swiper-slide-index]')?.getAttribute('data-swiper-slide-index') ?? 0);
  images.sort((a, b) => slideIndex(a) - slideIndex(b));
  result.image_urls = [...new Set(images.map(img => img.currentSrc || img.src).filter(value => {
    try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password; } catch { return false; }
  }))].slice(0, 24);
  // Carousel clones are deduplicated. No avatars, comments or recommendation images.
  const video = [...root.querySelectorAll('video')].length > 0 || task.platform === 'bilibili' || task.platform === 'tiktok' || /\/(?:video|reel)\//.test(current.pathname);
  result.media_kind = video ? 'video' : result.image_urls.length ? 'image' : task.platform === 'x' ? 'text' : 'unknown';
  const expectedImages = root.querySelectorAll('.swiper-pagination-bullet').length;
  result.images_incomplete = expectedImages > result.image_urls.length || images.length > 24;
  // Expand only an explicit content control inside the selected work.
  const expand = [...root.querySelectorAll('button, [role="button"], .toggle-btn, .desc-info__show-all')].filter(visible).find(n => /^(展开|展开全文|展开更多|Show more|more)$/i.test((n.innerText || '').trim()));
  if (expand) expand.click();
  const nodes = [ ...(root.matches(textSelector) ? [root] : []), ...root.querySelectorAll(textSelector)].filter(visible);
  // Text belongs to the selected work, never comments or recommended cards.
  const bodies = nodes.filter(n => !n.closest('[data-testid="reply"], .comments-container, #comment, .recommend-list')).map(n => (n.innerText || '').trim()).filter(Boolean);
  const body = (task.platform === 'x' ? bodies[0] : bodies.sort((a,b) => b.length-a.length)[0]) || '';
  const title = titleSelector ? [...root.querySelectorAll(titleSelector)].filter(visible).map(n => (n.innerText || '').trim()).find(Boolean) : '';
  if (body) {
    result.body = body.slice(0, 50000);
    if (title) result.title = title.slice(0, 500);
    result.content_level = 'detail_text';
    result.depth_status = 'detail_read';
    result.detail_source = 'browser_detail';
    result.detail_error = body.length > 50000 ? '正文超过保存上限，已截取前 50000 字' : '';
  }
  const players = [...root.querySelectorAll('video')].filter(player => visible(player) && !player.closest('aside, .recommend-list, .comments-container'));
  if (players.length === 1) {
    const tracks = Array.from(players[0].textTracks || []).filter(track => ['captions','subtitles'].includes(track.kind) && track.cues?.length);
    const track = tracks.find(candidate => candidate.mode === 'showing') || tracks[0];
    const segments = [];
    let length = 0;
    for (const cue of Array.from(track?.cues || [])) {
      if (!Number.isFinite(cue.startTime) || !Number.isFinite(cue.endTime) || cue.startTime < 0 || cue.endTime <= cue.startTime) continue;
      const text = (cue.getCueAsHTML?.().textContent || cue.text || '').trim().slice(0, Math.min(5000,50000 - length));
      if (!text) continue;
      segments.push({start:cue.startTime,end:cue.endTime,text});
      length += text.length + 1;
      if (length >= 50000 || segments.length === 2000) break;
    }
    if (segments.length) {
      result.transcript_segments = segments;
      result.transcript_text = segments.map(segment => segment.text).join('\n');
      result.language = (track.language || '').slice(0,40);
    }
  }
  return result;
}
