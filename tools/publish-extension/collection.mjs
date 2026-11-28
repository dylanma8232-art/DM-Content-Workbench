import {get, id, projectBase} from './workbench.mjs';

// Each rule identifies a list page and the path segment or parameter naming its owner/search.
const routes = {
  instagram: [
    ['account','www.instagram.com',/^\/([\w.]+)$/],
    ['topic','www.instagram.com',/^\/explore\/search\/keyword$/,'q']],
  tiktok: [
    ['account','www.tiktok.com',/^\/@([\w.]+)$/],
    ['topic','www.tiktok.com',/^\/search(?:\/video)?$/,'q']],
  douyin: [
    ['account','www.douyin.com',/^\/user\/([\w-]+)$/],
    ['topic','www.douyin.com',/^\/search\/([^/]+)$/]],
  xiaohongshu: [
    ['account','www.xiaohongshu.com',/^\/user\/profile\/([\w-]+)$/],
    ['topic','www.xiaohongshu.com',/^\/search_result$/,'keyword']],
  bilibili: [
    ['account','space.bilibili.com',/^\/(\d+)(?:\/upload(?:\/video)?|\/video)?$/],
    ['topic','search.bilibili.com',/^\/(?:all|video)$/,'keyword']],
  x: [
    ['account','x.com',/^\/([\w]+)$/],
    ['topic','x.com',/^\/search$/,'q']],
};

function parseRoute(task, value) {
  const u = new URL(value);
  if (u.protocol !== 'https:' || u.username || u.password || u.port || u.hash) return;
  const path = u.pathname.replace(/\/$/, '');
  for (const [mode, host, regex, param] of Object.hasOwn(routes, task.platform) ? routes[task.platform] : []) {
    if (u.hostname !== host || task.mode !== mode) continue;
    const match = path.match(regex);
    if (!match || (param && u.searchParams.getAll(param).length !== 1)) continue;
    const name = param ? u.searchParams.get(param) : decodeURIComponent(match[1]);
    if (!name || /[\x00-\x1f]/.test(name)) continue;
    if (mode === 'account' && ['instagram','x'].includes(task.platform) && /^(?:home|explore|search|accounts|login|notifications|messages|settings|direct|reels|i|intent|share|compose|p|stories)$/i.test(name)) continue;
    return {u, name, param};
  }
}

export function matchesTask(task, value) {
  try {
    const original = parseRoute(task, task.url), current = parseRoute(task, value);
    if (!original || !current || current.name !== original.name) return false;
    // Only Bilibili search uses numbered result pages. Preserve every other filter.
    for (const {u} of [original,current]) {
      if (task.platform === 'bilibili' && task.mode === 'topic' && u.searchParams.getAll('page').length === 1 && /^[1-9]\d*$/.test(u.searchParams.get('page'))) {
        u.searchParams.delete('page');
        if (u.searchParams.getAll('o').length === 1 && /^\d+$/.test(u.searchParams.get('o'))) u.searchParams.delete('o');
      }
      u.searchParams.sort();
    }
    const path = original.u.pathname.replace(/\/$/,''), destination = current.u.pathname.replace(/\/$/,'');
    const uploads = task.platform === 'bilibili' && task.mode === 'account' && destination === `${path}/upload/video`;
    return original.u.origin === current.u.origin && (path === destination || uploads) && original.u.search === current.u.search;
  } catch { return false; }
}

export function endpoint(job) { return `${projectBase(job)}/collection-tasks/${id(job.source)}`; }

export async function collectionTask(job) {
  const {id:source, platform, mode, query, limit, url, attempt_token, collector_label} = await (await get(endpoint(job))).json();
  const task = {id:source, platform, mode, query, limit, url, attempt_token};
  if (collector_label != null && typeof collector_label !== 'string') throw Error('工作台返回的采集账号无效。');
  if (collector_label?.trim()) task.collector_label = collector_label.trim();
  if (source !== job.source || typeof query !== 'string' || !query.trim() || !Number.isSafeInteger(limit) || limit < 1 || !matchesTask(task,url) || (attempt_token !== undefined && (typeof attempt_token !== 'string' || !attempt_token))) throw Error('工作台返回的采集任务或平台地址无效。');
  const {name, param, u} = parseRoute(task,url);
  if ([...u.searchParams.keys()].some(key => key !== param) || limit > 50) throw Error('任务地址含未知参数或条数超限。');
  let expected = query.trim();
  if (mode === 'account') {
    if (/^https?:\/\//i.test(expected)) expected = parseRoute(task,expected)?.name;
    else expected = expected.replace(/^@/,'');
  }
  const insensitive = mode === 'account' && ['x','instagram','tiktok'].includes(platform);
  if (!(insensitive ? expected?.toLowerCase() === name.toLowerCase() : expected === name)) throw Error('平台地址与任务账号或搜索词不一致。');
  return task;
}

export async function upload(job,items,confirmed = false) {
  const payload = {items,attempt_token:job.task.attempt_token};
  if (job.task.collector_label) {
    if (confirmed !== true) throw Error('请先核对并确认当前平台登录账号。');
    payload.collector_confirmed = true;
  }
  const response = await fetch(`${endpoint(job)}/results`, {method:'POST', credentials:'include', redirect:'error', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  if (!response.ok) throw Error(`工作台返回 HTTP ${response.status}，请查看采集任务状态。`);
  return response.json();
}
