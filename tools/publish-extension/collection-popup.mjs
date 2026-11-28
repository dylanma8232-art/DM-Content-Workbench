import {matchesTask, upload} from './collection.mjs';
import {collectPage} from './collect-page.mjs';
import {collectDetail} from './collect-detail.mjs';

const pause = ms => new Promise(resolve => setTimeout(resolve,ms));

const detailHosts = {instagram:'www.instagram.com', tiktok:'www.tiktok.com', douyin:'www.douyin.com', xiaohongshu:'www.xiaohongshu.com', bilibili:'www.bilibili.com', x:'x.com'};
const loginMessage = '采集已暂停。请在当前平台官网完成登录，然后重新打开助手继续原任务。';
class LoginRequired extends Error {}

export function loginRedirect(platform,value) {
  try {
    const url = new URL(value);
    const domain = detailHosts[platform].replace(/^www\./,'');
    return url.protocol === 'https:' && !url.port && !url.username && !url.password &&
      (url.hostname === domain || url.hostname.endsWith('.' + domain)) &&
      /^(?:\/(?:accounts|i\/flow|account|passport\/web))?\/(?:login|signin|sign-in|passport)(?:\/|$)/i.test(url.pathname);
  } catch { return false; }
}

export function loginWall() {
  const visible = node => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden';
  const panels = document.querySelectorAll('dialog, [role="dialog"], [aria-modal="true"], form, .login-container, .login-modal, [data-e2e="login-modal"]');
  return [...panels].some(panel => visible(panel) && (
    /登录|登錄|\blog\s*in\b|\bsign\s*in\b/i.test(panel.innerText) ||
    [...panel.querySelectorAll('input[type="password"]')].some(visible)
  )) || /(?:请先登录|登录后(?:即可|才能|可)(?:查看|继续)|(?:log|sign) in to (?:continue|view|see))/i.test(document.body?.innerText?.trim().slice(0,180) || '');
}

export async function collectTab(tabId,job,progress,confirmed = false) {
  if (job.task.collector_label && confirmed !== true) throw Error('请先核对并确认当前平台登录账号。');
  const {platform,mode,query,limit,url} = job.task;
  const scope = {platform,mode,query,limit,url};
  const items = new Map();
  const visited = new Set();
  let documentId, idle = 0;
  async function readTab() {
    const tab = await chrome.tabs.get(tabId);
    if (Date.now() >= job.expires || !tab.active) throw Error('任务已过期或标签页已切换。');
    if (loginRedirect(platform,tab.pendingUrl) || loginRedirect(platform,tab.url)) throw new LoginRequired(loginMessage);
    return tab;
  }
  async function checkWall(target) {
    const [frame] = await chrome.scripting.executeScript({target,func:loginWall,args:[{}]});
    if (frame?.result === true) throw new LoginRequired(loginMessage);
  }
  async function check() {
    const tab = await readTab();
    if (tab.pendingUrl || !matchesTask(scope,tab.url)) throw Error('页面已切换或搜索条件已改变，请返回原任务页面。');
    return tab.url;
  }
  while (items.size < limit && idle < 5) {
    const current = await check();
    const target = documentId ? {tabId,documentIds:[documentId]} : {tabId};
    const [frame] = await chrome.scripting.executeScript({target,func:collectPage,args:[scope,current,[...items.keys()],idle === 2]});
    if (!frame?.result || !frame.documentId) throw Error('页面读取中断，请确认登录状态并重试。');
    documentId = frame.documentId;
    await checkWall({tabId,documentIds:[documentId]});
    const before = items.size;
    for (const item of frame.result.items) items.set(item.external_id,item);
    progress(`已读取 ${items.size} / ${limit} 条作品，请保持弹窗打开。`);
    idle = items.size === before ? idle + 1 : 0;
    if (items.size === limit) break;
    const next = frame.result.next;
    if (idle >= 2 && next && matchesTask(scope,next) && !visited.has(next) && visited.size < limit && current !== next) {
      await check();
      visited.add(current);
      visited.add(next);
      await chrome.tabs.update(tabId,{url:next});
      let loaded = false;
      for (let i = 0; i < 40; i++) {
        await pause(250);
        const tab = await readTab();
        if (tab.status === 'complete' && matchesTask(scope,tab.url) && new URL(tab.url).pathname === new URL(next).pathname && new URL(tab.url).searchParams.get('page') === new URL(next).searchParams.get('page')) { loaded = true; break; }
      }
      if (!loaded) throw Error('下一页未能打开，请重新采集。');
      documentId = undefined;
      idle = 0;
    } else await pause(950);
  }
  await check();
  const records = [...items.values()].map(item => ({...item, content_level: item.body ? 'summary' : 'title_only', depth_status: 'detail_unavailable', detail_error: '详情尚未读取'}));
  let finalTarget = {tabId,documentIds:[documentId]};
  if (job.deepPermission && records.length) {
    const deadline = Date.now() + 90_000;
    for (let i = 0; i < records.length; i++) {
      if (Date.now() >= Math.min(deadline, job.expires)) {
        records[i].detail_error = '本轮详情读取时间已到，再次采集可重试';
        continue;
      }
      progress(`正在读取作品详情 ${i + 1} / ${records.length}，请保持弹窗打开。`);
      const item = records[i];
      try {
        await readTab();
        await chrome.tabs.update(tabId, {url: item.access_url || item.url});
        let loaded = false;
        for (let n = 0; n < 40; n++) {
          await pause(250);
          const current = await readTab();
          if (!current.pendingUrl && current.status === 'complete') { loaded = true; break; }
        }
        if (!loaded) throw Error('详情页加载超时');
        await pause(600);
        await checkWall({tabId});
        await chrome.scripting.executeScript({target:{tabId}, func:collectDetail, args:[scope,item]});
        await pause(350);
        const [frame] = await chrome.scripting.executeScript({target:{tabId}, func:collectDetail, args:[scope,item]});
        if (!frame?.documentId || !frame.result) throw Error('详情页读取中断，请返回原任务重试。');
        finalTarget = {tabId,documentIds:[frame.documentId]};
        await checkWall(finalTarget);
        records[i] = frame.result;
      } catch (error) {
        await readTab();
        throw error;
      }
    }
  }
  await readTab();
  await checkWall(finalTarget);
  await readTab();
  const receipt = await upload(job,records,confirmed);
  return items.size ? (receipt?.message || `已提交 ${items.size} 条作品。`) : '未读取到有可见文字的作品（0 条）。请检查登录状态、页面是否加载及搜索结果。';
}

export function showCollection(tab,job) {
  const el = id => document.getElementById(id);
  const {platform,mode,query,limit} = job.task;
  document.querySelector('h1').textContent = 'DM 采集助手';
  el('scope').textContent = `${platform} · ${mode === 'account' ? '账号' : '关键词搜索'} ${query} · 最多 ${limit} 条`;
  el('collection').hidden = false;
  const status = text => { el('status').textContent = text; };
  const confirmation = el('confirmed');
  confirmation.checked = false;
  if (job.task.collector_label) {
    el('collector').hidden = false;
    el('collector-label').textContent = '我已核对当前平台登录账号为 ' + job.task.collector_label;
  }
  const resetConfirmation = () => { el('collect').disabled = !!job.task.collector_label && !confirmation.checked; };
  confirmation.onchange = resetConfirmation;
  resetConfirmation();
  el('return-task').onclick = async () => {
    try {
      if (Date.now() >= job.expires) throw Error('任务已过期，请从工作台重新打开。');
      await chrome.tabs.update(tab.id,{url:job.task.url});
      window.close();
    } catch (error) { status(error.message); }
  };
  if (!matchesTask(job.task,tab.url)) {
    el('collect').hidden = true;
    el('collector').hidden = true;
    el('return-task').hidden = false;
    return status(loginRedirect(platform,tab.url) ? loginMessage : '请返回原任务页面，再打开助手继续采集。');
  }
  status('先读取作品列表，再逐篇打开详情；可用的字幕或转写由工作台继续提取。请保持弹窗打开。');
  el('collect').onclick = async () => {
    el('collect').disabled = true;
    try {
      const origins = new Set([new URL(job.task.url).origin, 'https://' + detailHosts[platform]]);
      job.deepPermission = await chrome.permissions.request({origins:[...origins].map(origin => origin + '/*')});
      if (!job.deepPermission) throw Error('未获得详情页访问权限。本次未开始采集，请重新授权后继续。');
      confirmation.disabled = true;
      status(await collectTab(tab.id,job,status,confirmation.checked));
    }
    catch (error) {
      confirmation.disabled = false;
      confirmation.checked = false;
      resetConfirmation();
      el('return-task').hidden = false;
      status(error.message);
    }
  };
}
