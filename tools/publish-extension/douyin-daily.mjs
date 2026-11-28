import {postDaily, projectBase} from './workbench.mjs';
import {collectBound, creatorPermission, shanghaiClock} from './douyin-page.mjs';

const key = 'douyinDaily';
const pendingKey = 'douyinDailyApproval';
const alarm = 'douyin-daily-noon';
const approvalUrl = chrome.runtime.getURL('douyin-connect.html');
const binding = async () => (await chrome.storage.local.get(key))[key];
const pending = async () => (await chrome.storage.session.get(pendingKey))[pendingKey];
const save = job => chrome.storage.local.set({[key]: job});
const same = (a, b) => a?.origin === b.origin && a.project === b.project;
let queue = Promise.resolve();
let running;
function locked(fn) {
  const result = queue.then(fn);
  queue = result.catch(() => {});
  return result;
}
function status(job, message) {
  return {connected: !!job, enabled: !!job?.enabled, account: job?.account || '', platform_uid: job?.platform_uid || '', day: job?.day || '', message: message || job?.message || '尚未连接。'};
}
async function schedule(job) {
  if (!job?.enabled) return chrome.alarms.clear(alarm);
  const {nextNoon, yesterday} = shanghaiClock();
  const retryAt = job.delivery?.report.day === yesterday && job.delivery.retryAt;
  await chrome.alarms.create(alarm, {when: retryAt > Date.now() ? retryAt : nextNoon});
}
async function run(job, day, cached) {
  try {
    if (!await chrome.permissions.contains(creatorPermission)) throw Error('创作者中心权限已关闭，请重新授权。');
    const report = cached || await collectBound(job, day);
    await locked(async () => {
      const current = await binding();
      if (current?.token !== job.token) return;
      if (shanghaiClock().yesterday !== day) throw Error('上海日期已切换，请重试。');
      let response;
      let failure;
      try {
        response = await postDaily(current, report);
      } catch { failure = '本机工作台暂时无法连接。'; }
      if (response && !response.ok) failure = `工作台返回 HTTP ${response.status}。`;
      if (failure) {
        const transient = !response || [408, 429].includes(response.status) || response.status >= 500;
        const now = Date.now();
        const {noon, yesterday} = shanghaiClock(now);
        if (transient && current.enabled && day === yesterday && now >= noon) {
          const minutes = Math.min((current.delivery?.minutes || 2.5) * 2, 60);
          const retryAt = Math.min(now + minutes * 60000, noon + 12 * 3600000 - 60000);
          if (retryAt > now) {
            const deferred = {...current, delivery: {report, retryAt, minutes}, message: `${failure}日报已暂存，稍后自动重试。`};
            await save(deferred);
            await schedule(deferred);
            return;
          }
        }
        throw Error(failure);
      }
      const complete = {...current, delivery: null, day, message: `已回填 ${day} 日报。`};
      await save(complete);
      await schedule(complete);
    });
  } catch (error) {
    await locked(async () => {
      const current = await binding();
      if (current?.token !== job.token) return;
      const failed = {...current, delivery: null, message: `${error.message} 可点击重试。`};
      await save(failed);
      await schedule(failed);
    });
  } finally { if (running === job.token) running = undefined; }
}
async function start(job, delivery = null) {
  if (running === job.token) return status(job);
  const day = shanghaiClock().yesterday;
  const active = {...job, delivery, message: `正在读取 ${day} 日报。`};
  await save(active);
  running = job.token;
  void run(active, day, delivery?.report);
  return status(active);
}
async function due(job) {
  const {noon, yesterday} = shanghaiClock();
  if (job?.enabled && Date.now() >= noon && job.day !== yesterday) {
    const delivery = job.delivery?.report.day === yesterday ? job.delivery : null;
    if (!delivery || delivery.retryAt <= Date.now()) return start(job, delivery);
  }
  return status(job);
}
async function dispatch(msg, sender) {
  if (sender.id !== chrome.runtime.id || sender.frameId !== 0 || !sender.tab) throw Error('无效的扩展请求。');
  const job = await binding();
  const request = await pending();
  if (msg.type === 'douyin-approval') {
    if (sender.url !== approvalUrl || request?.tabId !== sender.tab.id || Date.now() > request.expires) throw Error('连接请求已过期，请回工作台重新连接。');
    if (msg.action === 'info') return {origin: request.origin, project: request.project};
    if (msg.action !== 'confirm' || job || !await chrome.permissions.contains(creatorPermission) || !msg.account || !msg.platform_uid) throw Error('请重新核对账号并授权。');
    const fresh = {origin: request.origin, project: request.project, token: crypto.randomUUID(), account: msg.account, platform_uid: msg.platform_uid, enabled: false};
    await save(fresh);
    await chrome.storage.session.remove(pendingKey);
    return start(fresh);
  }
  const scope = {origin: sender.origin, project: msg.project};
  if (new URL(sender.url).origin !== scope.origin) throw Error('工作台来源无效。');
  projectBase(scope);
  const own = job && same(job, scope);
  if (msg.action === 'status') {
    const result = status(own ? job : null, !own && (job ? '扩展已绑定其他工作台或项目。' : request && same(request, scope) ? '等待扩展授权与账号确认。' : ''));
    if (job && !own && job.origin === scope.origin) result.boundProject = job.project;
    return result;
  }
  if (job && !own) throw Error('请先在原工作台项目解除绑定。');
  if (msg.action === 'connect') {
    if (own) return status(job);
    if (request && request.expires > Date.now()) {
      if (!same(request, scope)) throw Error('另一个项目正在等待授权。');
      if (await chrome.tabs.update(request.tabId, {active: true}).catch(() => null)) return status(null, '等待扩展授权与账号确认。');
    }
    const tab = await chrome.tabs.create({url: approvalUrl});
    await chrome.storage.session.set({[pendingKey]: {...scope, tabId: tab.id, expires: Date.now() + 15 * 60000}});
    return status(null, '等待扩展授权与账号确认。');
  }
  if (msg.action === 'disconnect') {
    if (request && same(request, scope)) await chrome.storage.session.remove(pendingKey);
    if (own) { await chrome.storage.local.remove(key); await schedule(null); }
    return status(null);
  }
  if (!own) throw Error('请先连接并核对抖音账号。');
  if (msg.action === 'retry') return start(job);
  if (!['enable','disable'].includes(msg.action)) throw Error('未知日报操作。');
  const updated = {...job, enabled: msg.action === 'enable'};
  if (!updated.enabled && updated.delivery) {
    updated.delivery = null;
    updated.message = '已停止自动重试，可手动读取日报。';
  }
  await save(updated);
  await schedule(updated);
  return due(updated);
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (!['douyin-daily','douyin-approval'].includes(msg?.type)) return;
  locked(() => dispatch(msg, sender)).then(status => reply({ok: true, status}), error => reply({ok: false, error: error.message}));
  return true;
});
chrome.tabs.onRemoved.addListener(id => locked(async () => {
  if ((await pending())?.tabId === id) await chrome.storage.session.remove(pendingKey);
}));
chrome.alarms.onAlarm.addListener(event => {
  if (event.name === alarm) locked(async () => {
    const job = await binding();
    await schedule(job);
    await due(job);
  });
});
async function resume() {
  let job = await binding();
  if (job && !running && job.message?.startsWith('正在读取')) {
    job = {...job, message: '上次读取中断，可点击重试。'};
    await save(job);
  }
  await schedule(job);
  await due(job);
}
chrome.runtime.onStartup.addListener(() => locked(resume));
chrome.runtime.onInstalled.addListener(() => locked(resume));
