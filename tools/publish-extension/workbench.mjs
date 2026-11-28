export const pages = new Map([
  ['bilibili', 'https://member.bilibili.com/platform/upload/video/frame'],
  ['xiaohongshu', 'https://creator.xiaohongshu.com/publish/publish'],
]);

export function id(value) {
  if (!Number.isSafeInteger(value) || value <= 0) throw Error('工作台记录 ID 无效，请重新打开发布任务。');
  return String(value);
}

export function projectBase(job) {
  const url = new URL(job.origin);
  if (url.origin !== job.origin || url.protocol !== 'http:' || !['localhost', '127.0.0.1'].includes(url.hostname) || url.port === '0') throw Error('请从本机 HTTP 工作台打开发布任务。');
  return `${url.origin}/api/projects/${id(job.project)}`;
}

// Injected into the workbench document. Check again after tab selection because navigation can race injection.
async function postDailyFromPage(origin, project, report) {
  if (location.origin !== origin || !Number.isSafeInteger(project) || project <= 0) return null;
  const response = await fetch(`${origin}/api/projects/${project}/douyin-daily`, {
    method: 'POST', mode: 'same-origin', credentials: 'same-origin', redirect: 'error', cache: 'no-store',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(report), signal: AbortSignal.timeout(15000),
  });
  return response.status;
}

export async function postDaily(job, report) {
  projectBase(job);
  const {origin, project} = job;
  const pattern = `http://${new URL(origin).hostname}/*`;
  if (!await chrome.permissions.contains({origins: [pattern]})) throw Error('缺少本机工作台权限。');
  const matches = tab => tab.url?.startsWith(`${origin}/`) && !tab.pendingUrl && !tab.discarded && tab.status === 'complete';
  const existing = (await chrome.tabs.query({url: pattern})).find(matches);
  const tab = existing || await chrome.tabs.create({url: `${origin}/`, active: false});
  try {
    for (let attempt = 0; attempt < 40; attempt++) {
      const current = await chrome.tabs.get(tab.id);
      if (matches(current)) {
        const [frame] = await chrome.scripting.executeScript({target: {tabId: tab.id, frameIds: [0]}, world: 'ISOLATED', func: postDailyFromPage, args: [origin, project, report]});
        if (frame?.frameId !== 0 || !Number.isInteger(frame.result) || frame.result < 100 || frame.result > 599) break;
        return {ok: frame.result >= 200 && frame.result < 300, status: frame.result};
      }
      if (existing || (current.pendingUrl && !current.pendingUrl.startsWith(`${origin}/`)) || (current.url && current.url !== 'about:blank' && !current.url.startsWith(`${origin}/`))) break;
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    throw Error('工作台页面未就绪或已跳转。');
  } finally {
    if (!existing) await chrome.tabs.remove(tab.id).catch(() => {});
  }
}

export function base(job) {
  return `${projectBase(job)}/publishing`;
}

export async function get(url) {
  const response = await fetch(url, {credentials: 'include', redirect: 'error', cache: 'no-store'});
  if (!response.ok) throw Error(`工作台返回 HTTP ${response.status}，请检查本机登录状态和素材权限。`);
  return response;
}

export async function draft(job) {
  id(job.post);
  id(job.target);
  const response = await get(base(job));
  const data = await response.json();
  const post = data.posts?.find(p => p.id === job.post);
  const target = post?.targets?.find(t => t.id === job.target);
  if (!target) throw Error('稿件或目标已移除，请在工作台重新选择。');
  if (target.status === 'published') throw Error('此目标已发布，请关闭旧助手标签页。');
  if (target.status !== 'pending') throw Error('当前目标未处于待发布状态，请返回工作台查看任务。');
  if (!pages.has(target.platform)) throw Error('该平台暂不支持填入。');
  return {post, target};
}

export function onPage(url, platform) {
  const expected = new URL(pages.get(platform));
  const current = new URL(url);
  return current.origin === expected.origin && current.pathname.replace(/\/$/, '') === expected.pathname;
}
