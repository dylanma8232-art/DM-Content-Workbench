export const creatorHome = 'https://creator.douyin.com/creator-micro/home';
export const operationPage = 'https://creator.douyin.com/creator-micro/data-center/operation';
export const creatorPermission = {origins: ['https://creator.douyin.com/*']};
const dayMs = 86400000;

export function shanghaiClock(now = Date.now()) {
  const today = Math.floor((now + 8 * 3600000) / dayMs) * dayMs;
  const noon = today + 4 * 3600000;
  return {yesterday: new Date(today - dayMs).toISOString().slice(0, 10), noon, nextNoon: noon > now ? noon : noon + dayMs};
}

export function identity(text) {
  const lines = text.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  const accounts = lines.flatMap((s, i) => {
    const match = /^抖音号\s*[:：]\s*([\w.-]+)$/.exec(s);
    return match && i ? [{account: lines[i - 1], platform_uid: match[1]}] : [];
  });
  if (accounts.length !== 1) throw Error('请登录创作者中心并确认页面只显示一个抖音号和昵称。');
  return accounts[0];
}

export function overview(text, day) {
  const lines = text.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  const works = lines.indexOf('作品数据');
  const fans = lines.indexOf('粉丝数据', works + 1);
  if (works < 0 || fans < 0) throw Error('等待作品数据和粉丝数据加载。');
  const result = {day};
  const range = day.slice(5).replace('-', '.');
  function readSection(section, fields) {
    const periods = section.filter(s => s.startsWith('统计周期'));
    const period = /^统计周期\s*[:：]\s*(\d{4}-\d{2}-\d{2})(?:\s*[（(].*[)）])?$/.exec(periods[0] || '');
    if (periods.length !== 1 || period?.[1] !== day || !section.includes(`${range}-${range}`)) throw Error(`等待两个统计周期更新为 ${day}。`);
    for (const [label, field] of fields) {
      const i = section.indexOf(label);
      const raw = section[i + 1];
      if (i < 0 || i !== section.lastIndexOf(label) || !/^(?:\d+|\d{1,3}(?:,\d{3})+)$/.test(raw)) throw Error(`${label}尚未显示唯一的精确整数。`);
      result[field] = Number(raw.replaceAll(',', ''));
      if (!Number.isSafeInteger(result[field])) throw Error(`${label}超出整数范围。`);
    }
  }
  readSection(lines.slice(works + 1, fans), [['投稿量','published'], ['总播放量','views'], ['总点赞量','likes'], ['总分享量','shares'], ['总评论量','comments']]);
  readSection(lines.slice(fans + 1), [['总粉丝量','followers']]);
  return result;
}

// Runs in the top frame's isolated world. Re-query after each click because the filters rerender.
function visiblePage(expected, filter) {
  if (location.origin + location.pathname.replace(/\/$/, '') !== expected) return null;
  if (filter !== null) {
    const spans = [...document.querySelectorAll('span')].filter(s => !s.childElementCount && s.innerText.trim() === '昨天' && s.getClientRects().length && getComputedStyle(s).visibility === 'visible');
    if (spans.length !== 2) return null;
    spans[filter].click();
    return true;
  }
  return document.body?.innerText || '';
}

export async function readCreator(tabId, page, parse, filter = null) {
  const until = Date.now() + 45000;
  let lastError = '请检查创作者中心登录状态和页面是否加载完成。';
  let previous;
  do {
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === 'complete' && tab.url?.split(/[?#]/)[0].replace(/\/$/, '') === page) {
      const [frame] = await chrome.scripting.executeScript({target: {tabId}, func: visiblePage, args: [page, filter]});
      try {
        if (frame.result === true) return;
        if (typeof frame.result !== 'string') throw Error('等待两个昨天筛选项。');
        const value = parse(frame.result);
        const signature = JSON.stringify(value);
        if (signature === previous) return value;
        previous = signature;
      } catch (error) { previous = undefined; lastError = error.message; }
    } else previous = undefined;
    await new Promise(resolve => setTimeout(resolve, 900));
  } while (Date.now() < until);
  throw Error(lastError);
}

export async function collectBound(job, day) {
  const {id} = await chrome.tabs.create({url: creatorHome, active: false});
  async function checkIdentity() {
    const seen = await readCreator(id, creatorHome, identity);
    if (seen.platform_uid !== job.platform_uid || seen.account !== job.account) throw Error('当前登录账号与已绑定账号不符，请切回原账号或解除绑定。');
  }
  try {
    await checkIdentity();
    await chrome.tabs.update(id, {url: operationPage});
    await readCreator(id, operationPage, null, 0);
    await readCreator(id, operationPage, null, 1);
    const report = await readCreator(id, operationPage, text => overview(text, day));
    await chrome.tabs.update(id, {url: creatorHome});
    await checkIdentity();
    return {...report, account: job.account, platform_uid: job.platform_uid};
  } finally { await chrome.tabs.remove(id).catch(() => {}); }
}
