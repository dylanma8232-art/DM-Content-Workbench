import test from 'node:test';
import assert from 'node:assert/strict';
import {collectionTask, matchesTask, upload} from './collection.mjs';
import {collectPage} from './collect-page.mjs';
import {collectTab} from './collection-popup.mjs';

const job = {origin:'http://localhost:8765',project:2,source:3};
const profiles = {
  instagram:['alice','https://www.instagram.com/alice/','https://www.instagram.com/explore/search/keyword/?q=tea','/p/A_b/','A_b'],
  tiktok:['alice','https://www.tiktok.com/@alice','https://www.tiktok.com/search?q=tea','/@alice/video/123','123'],
  douyin:['abcde123','https://www.douyin.com/user/abcde123','https://www.douyin.com/search/tea','/video/123','123'],
  xiaohongshu:['a'.repeat(24),'https://www.xiaohongshu.com/user/profile/'+'a'.repeat(24),'https://www.xiaohongshu.com/search_result?keyword=tea','/search_result/'+'b'.repeat(24),'b'.repeat(24)],
  bilibili:['123','https://space.bilibili.com/123','https://search.bilibili.com/all?keyword=tea','/video/BV16vnH6nEQM','BV16vnH6nEQM'],
  x:['alice','https://x.com/alice','https://x.com/search?q=tea','/alice/status/123','123'],
};
const record = {id:3,limit:5,attempt_token:'original'};
for (const [platform,[owner,profile,search,path,external_id]] of Object.entries(profiles)) {
  test(`${platform} binds account and search URLs and rejects other targets`,async t => {
    let task;
    t.mock.method(globalThis,'fetch',async () => Response.json(task));
    for (const [mode,query,url] of [['account',owner,profile],['topic','tea',search]]) {
      task = {...record,platform,mode,query,url};
      assert.deepEqual(await collectionTask(job),task);
      assert.ok(matchesTask(task,url));
      assert.ok(!matchesTask(task,url.replace('https://','http://')));
      assert.ok(!matchesTask(task,url.replace('https://','https://evil.example/')));
      assert.ok(!matchesTask(task,mode === 'topic' ? url.replace('tea','coffee') : url.replace(owner,'another')));
      task.query = 'different';
      await assert.rejects(collectionTask(job));
    }
  });
  test(`${platform} extracts canonical visible posts, skips unrelated links and known IDs`, t => {
    const task = {...record,platform,mode:'account',query:owner,url:profile};
    const host = platform === 'bilibili' ? 'https://www.bilibili.com' : new URL(profile).origin;
    const node = {getClientRects:()=>[1],innerText:'Card title'};
    const parent = {...node,closest:()=>parent,querySelector:selector=>selector.startsWith('h3') ? node : null};
    const link = {...node,href:host+path+'?tracking=1',closest:selector=>selector.startsWith('aside') ? null : parent,querySelector:()=>node};
    globalThis.location = new URL(profile);
    globalThis.document = {querySelector:()=>null,querySelectorAll:selector=>selector.startsWith(':is(') ? [{...link,href:'https://evil.example'+path},link] : []};
    globalThis.getComputedStyle = () => ({visibility:'visible'});
    t.after(()=>{delete globalThis.location; delete globalThis.document; delete globalThis.getComputedStyle;});
    const canonical = platform === 'xiaohongshu' ? '/explore/'+external_id : path;
    assert.deepEqual(collectPage(task,profile,[]).items,[{external_id,title:'Card title',body:'',author:'',url:host+canonical}]);
    assert.deepEqual(collectPage(task,profile,[external_id]).items,[]);
    assert.throws(()=>collectPage(task,search,[]));
  });
}

test('allows only same-search pagination and same-owner Bilibili uploads', () => {
  const task = {platform:'bilibili',mode:'topic',url:profiles.bilibili[2]};
  assert.ok(matchesTask(task,task.url+'&page=3&o=60'));
  for (const suffix of ['&page=1&page=2','&redirect=x','&keyword=other','&page=0','#other']) assert.ok(!matchesTask(task,task.url+suffix));
  const account = {...task,mode:'account',url:profiles.bilibili[1]};
  assert.ok(matchesTask(account,account.url+'/upload/video'));
  assert.ok(!matchesTask(account,account.url+'/favlist'));
  assert.ok(!matchesTask(account,account.url.replace('/123','/321')+'/upload/video'));
  assert.ok(!matchesTask({platform:'instagram',mode:'topic',url:profiles.instagram[2]},'https://www.instagram.com/explore/tags/tea/'));
});

test('submits only to original project, source and attempt; surfaces stale receipt',async t=>{
  const task = {...record,platform:'x',mode:'topic',query:'tea',url:profiles.x[2]};
  const requests = [];
  t.mock.method(globalThis,'fetch',async (url,options)=>{
    requests.push(url);
    assert.equal(options.redirect,'error');
    if (options.method !== 'POST') return Response.json(task);
    assert.deepEqual(JSON.parse(options.body),{items:[],attempt_token:'original'});
    return new Response(null,{status:409});
  });
  const bound = {...job,task:await collectionTask(job)};
  task.attempt_token = 'later';
  await assert.rejects(upload(bound,[]),/409/);
  assert.deepEqual(requests,['http://localhost:8765/api/projects/2/collection-tasks/3','http://localhost:8765/api/projects/2/collection-tasks/3/results']);
});

test('empty page reports zero and posts the bound attempt once',async t=>{
  const task = {...record,platform:'x',mode:'topic',query:'tea',url:profiles.x[2]};
  globalThis.chrome = {tabs:{get:async()=>({active:true,url:task.url})},scripting:{executeScript:async()=>[{documentId:'page',result:{items:[],next:null}}]}};
  t.after(()=>{delete globalThis.chrome;});
  t.mock.method(globalThis,'setTimeout',fn=>queueMicrotask(fn));
  const fetch = t.mock.method(globalThis,'fetch',async (url,options)=>{
    assert.deepEqual(JSON.parse(options.body),{items:[],attempt_token:'original'});
    return Response.json({message:'读取 0'});
  });
  const result = await collectTab(1,{...job,task,expires:Date.now()+60000},()=>{});
  assert.match(result,/0 条/);
  assert.equal(fetch.mock.callCount(),1);
});

test('stops before upload if navigation changes during extraction',async t=>{
  const task = {...record,limit:1,platform:'x',mode:'topic',query:'tea',url:profiles.x[2]};
  let url = task.url;
  globalThis.chrome = {tabs:{get:async()=>({active:true,url})},scripting:{executeScript:async ({args})=>{
    assert.equal(args[0].attempt_token,undefined);
    url = 'https://x.com/home';
    return [{documentId:'page',result:{items:[{external_id:'1'}]}}];
  }}};
  t.after(()=>{delete globalThis.chrome;});
  const fetch = t.mock.method(globalThis,'fetch');
  await assert.rejects(collectTab(1,{...job,task,expires:Date.now()+60000},()=>{}),/页面已切换/);
  assert.equal(fetch.mock.callCount(),0);
});
