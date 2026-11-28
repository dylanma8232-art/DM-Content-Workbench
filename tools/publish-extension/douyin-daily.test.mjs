import assert from 'node:assert/strict';
import test from 'node:test';
import {postDaily} from './workbench.mjs';
import {creatorHome, identity, operationPage, overview, shanghaiClock} from './douyin-page.mjs';

const day = '2026-11-26';
const profile = '示例账号\n抖音号：test.uid_2026';
const period = `统计周期：${day}（每日10点更新前一日数据）\n昨天\n近7天\n近30天\n11.26-11.26\n导出数据`;
const page = `作品数据\n${period}\n投稿\n合集\n直播\n投稿量\n0\n总播放量\n1,280\n总点赞量\n1\n总分享量\n0\n总评论量\n0\n粉丝数据\n${period}\n总粉丝量\n28\n粉丝净增\n0`;
const totals = {day, published:0, views:1280, likes:1, shares:0, comments:0, followers:28};

test('Shanghai noon, midnight and year boundary', () => {
  const noon = Date.parse('2026-01-01T04:00:00Z');
  assert.deepEqual(shanghaiClock(noon - 1), {yesterday:'2025-12-31', noon, nextNoon:noon});
  assert.equal(shanghaiClock(noon).nextNoon, noon + 86400000);
  assert.equal(shanghaiClock(Date.parse('2026-01-01T16:00:00Z')).yesterday, '2026-01-01');
});

test('requires both single-day sections, exact complete totals, and one visible account', () => {
  assert.deepEqual(overview(page, day), totals);
  for (const invalid of [page.replace(day, '2026-11-25'), page.replaceAll(day, '2026-11-20-2026-11-26'), page.replace('11.26-11.26','11.20-11.26'), page.slice(0, -30), page.replace('总分享量','分享率'), page.replace('总粉丝量\n28','总粉丝量\n—'), page.replace('1,280','1.28万'), page.replace('1,280','9007199254740992'), page.replace('总点赞量','总点赞量\n1\n总点赞量')]) assert.throws(() => overview(invalid, day));
  assert.throws(() => overview('账号总览\n' + period, day));
  assert.throws(() => identity(profile + '\n' + profile));
  assert.deepEqual(identity(profile), {account:'示例账号', platform_uid:'test.uid_2026'});
});

test('delivery checks permission, port and navigation and closes only owned tabs', async t => {
  const origin = 'http://localhost:8765';
  const job = {origin, project:3};
  const available = [{id:2, url:'http://localhost:8080/', status:'complete'}];
  const closed = [];
  let allowed = true, navigate = false, owned, writes = 0;
  globalThis.chrome = {
    permissions:{contains:async request => {
      assert.deepEqual(request.origins, ['http://localhost/*']);
      return allowed;
    }},
    tabs:{query:async () => available, create:async options => {
      assert.deepEqual(options, {url:origin+'/', active:false});
      return owned = {id:3, url:options.url, status:'complete'};
    }, get:async id => available.find(tab => tab.id === id) || owned, remove:async id => closed.push(id)},
    scripting:{executeScript:async ({target, world, func, args}) => {
      assert.deepEqual(target.frameIds,[0]);
      assert.equal(world,'ISOLATED');
      globalThis.location = new URL(navigate ? 'http://localhost:8080/' : origin);
      return [{frameId:0, result:await func(...args)}];
    }}
  };
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url,origin+'/api/projects/3/douyin-daily');
    assert.equal(options.credentials,'same-origin');
    writes++;
    return new Response(null, {status:204});
  });
  assert.equal((await postDaily(job,totals)).status,204);
  navigate = true;
  await assert.rejects(postDaily(job,totals));
  available.push({id:4,url:origin+'/',status:'complete'});
  await assert.rejects(postDaily(job,totals));
  assert.deepEqual(closed,[3,3]);
  assert.equal(writes,1);
  allowed = false;
  await assert.rejects(postDaily(job,totals),/权限/);
  await assert.rejects(postDaily({...job,project:'../2'},totals));
  await assert.rejects(postDaily({...job,origin:'https://localhost'},totals));
});

function store() {
  const values = {};
  return {get: async key => ({[key]:values[key]}), set: async update => Object.assign(values, update), remove: async key => delete values[key]};
}
function event() { return {addListener(fn) { this.fire = fn; }}; }

test('approval pins scope and account, captures stable report, schedules at noon and rejects identity changes', async t => {
  let now = Date.parse('2026-11-27T04:01:00Z');
  t.mock.method(Date, 'now', () => now);
  t.mock.method(globalThis, 'setTimeout', fn => setImmediate(fn));
  const tabs = new Map();
  const approvals = 'chrome-extension://ext/douyin-connect.html';
  const alarms = new Map();
  const posts = [];
  const clicks = [];
  let tabId = 0, homeReads = 0, operationReads = 0, switchAccount = false;
  globalThis.chrome = {
    runtime:{id:'ext', getURL:() => approvals, onMessage:event(), onStartup:event(), onInstalled:event()},
    storage:{local:store(), session:store()}, permissions:{contains: async () => true},
    alarms:{onAlarm:event(), create:async (name, spec) => alarms.set(name,spec), clear:async name => alarms.delete(name)},
    tabs:{onRemoved:event(), query:async () => [...tabs.values()], create:async ({url}) => { const tab = {url, id:++tabId, status:'complete'}; tabs.set(tabId, tab); return tab; }, get:async id => tabs.get(id), update: async (id, props) => Object.assign(tabs.get(id),props), remove:async id => tabs.delete(id)},
    scripting:{executeScript:async ({target,func,args,world}) => {
      const url = tabs.get(target.tabId).url;
      globalThis.location = new URL(url);
      if (url.startsWith(origin + '/')) {
        assert.equal(world, 'ISOLATED');
        assert.deepEqual(target.frameIds, [0]);
        return [{frameId:0, result:await func(...args)}];
      }
      globalThis.getComputedStyle = () => ({visibility:'visible'});
      const spans = [0,1].map(i => ({childElementCount:0, innerText:'昨天', getClientRects:() => [1], click:() => clicks.push(i)}));
      let text;
      if (url === creatorHome) text = switchAccount && ++homeReads > 2 ? profile.replace('test.uid','other') : profile;
      else {
        assert.equal(url, operationPage);
        operationReads++;
        const yesterday = shanghaiClock(now).yesterday;
        const sample = page.replaceAll(day, yesterday).replaceAll('11.26', yesterday.slice(5).replace('-', '.'));
        text = operationReads === 3 ? sample.replace(day, '2026-11-25') : operationReads === 4 ? sample.replace('1,280','0') : sample;
      }
      globalThis.document = {body:{innerText:text}, querySelectorAll:() => spans};
      return [{result:func(...args)}];
    }}
  };
  let httpStatus = 200;
  t.mock.method(globalThis,'fetch', async (url,opts) => {
    assert.equal(location.origin, origin);
    assert.equal(opts.mode, 'same-origin');
    assert.equal(opts.redirect, 'error');
    posts.push([url,JSON.parse(opts.body)]);
    if (!httpStatus) throw new TypeError('Failed to fetch');
    return Response.json({}, {status: httpStatus});
  });
  await import('./douyin-daily.mjs');
  const origin = 'http://127.0.0.1:8765';
  tabs.set(45, {id:45, url:origin+'/', status:'complete'});
  const sender = {id:'ext', frameId:0, tab:{id:45}, url:origin+'/', origin};
  function send(action, extra = {}, source = sender) {
    return new Promise(resolve => chrome.runtime.onMessage.fire({type:'douyin-daily', project:1, action, ...extra},source,resolve));
  }
  async function settled() {
    for (let i = 0; i < 100; i++) {
      const {status} = await send('status');
      if (!status.message.includes('正在读取')) return status;
      await new Promise(resolve => setImmediate(resolve));
    }
    assert.fail('job did not settle');
  }
  assert.equal((await send('connect')).status.connected,false);
  const authSender = {...sender, url:approvals, tab:{id:tabId}};
  const confirm = {type:'douyin-approval', ...identity(profile)};
  assert.equal((await send('confirm',confirm)).ok,false);
  const bound = await send('confirm',confirm,authSender);
  assert.equal(bound.status.enabled,false);
  assert.equal((await settled()).day, day);
  assert.deepEqual(clicks,[0,1]);
  assert.deepEqual(posts,[[origin+'/api/projects/1/douyin-daily',{...totals,...identity(profile)}]]);
  assert.equal((await send('retry',{project:2})).ok,false);
  assert.equal((await send('retry',{}, {...sender, origin:'http://localhost:8765',url:'http://localhost:8765/'})).ok,false);
  assert.equal((await send('enable')).status.enabled,true);
  assert.equal(alarms.get('douyin-daily-noon').when, shanghaiClock(now).nextNoon);
  chrome.alarms.onAlarm.fire({name:'douyin-daily-noon'});
  assert.equal((await settled()).day,day);
  assert.equal(posts.length,1);
  now += 86400000 + 7200000;
  httpStatus = 0;
  chrome.alarms.onAlarm.fire({name:'douyin-daily-noon'});
  assert.match((await settled()).message, /暂存/);
  assert.equal(posts.length,2);
  const tabsAfterCapture = tabId;
  assert.equal(alarms.get('douyin-daily-noon').when, now + 5 * 60000);
  chrome.runtime.onStartup.fire();
  await settled();
  assert.equal(posts.length,2);
  httpStatus = 503;
  now += 5 * 60000;
  chrome.alarms.onAlarm.fire({name:'douyin-daily-noon'});
  assert.match((await settled()).message, /503/);
  assert.equal(alarms.get('douyin-daily-noon').when, now + 10 * 60000);
  now += 10 * 60000;
  httpStatus = 200;
  chrome.alarms.onAlarm.fire({name:'douyin-daily-noon'});
  assert.equal((await settled()).day,'2026-11-27');
  assert.equal(posts.length,4);
  assert.deepEqual(posts[1],posts[3]);
  assert.equal(tabId,tabsAfterCapture);
  assert.equal(alarms.get('douyin-daily-noon').when, Date.parse('2026-11-29T04:00:00Z'));
  assert.equal((await send('disable')).status.connected,true);
  assert.equal(alarms.size,0);
  switchAccount = true;
  await send('retry');
  assert.match((await settled()).message,/账号不符/);
  assert.equal(posts.length,4);
  assert.equal((await send('disconnect')).status.connected,false);
});
