import assert from 'node:assert/strict';
import test from 'node:test';
import {base, draft, onPage, pages} from './workbench.mjs';
import {fill} from './fill.mjs';

const job = {origin:'http://127.0.0.1:8765', project:1, post:2, target:3};
test('loads only the selected local record and ignores supplied launch URLs', async t => {
  const target = {id:3, status:'pending', platform:'bilibili', url:'https://example.com'};
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    assert.equal(url, 'http://127.0.0.1:8765/api/projects/1/publishing');
    assert.equal(opts.redirect, 'error');
    return Response.json({posts:[{id:2, targets:[target]}]});
  });
  assert.deepEqual((await draft(job)).target, target);
  await assert.rejects(draft({...job, target:4}));
  target.status = 'published';
  await assert.rejects(draft(job), /已发布/);
  target.status = 'failed';
  await assert.rejects(draft(job), /待发布状态/);
  assert.throws(() => base({...job, origin:'http://localhost.example'}));
  assert.throws(() => base({...job, project:'../1'}));
  for (const [platform, page] of pages) {
    assert.ok(onPage(page + '?mode=upload', platform));
    assert.equal(onPage(page.replace('.com/', '.com.example/'), platform), false);
  }
});

test('a popup opened while pending checks the target again before filling', async t => {
  const controls = new Map();
  t.mock.method(globalThis, 'fetch', () => Response.json({posts:[{id:2, targets:[{id:3, platform:'bilibili', status:targetStatus}]}]}));
  let targetStatus = 'pending';
  globalThis.document = {getElementById(name) {
    if (!controls.has(name)) controls.set(name, {});
    return controls.get(name);
  }};
  const executeScript = t.mock.fn();
  globalThis.chrome = {
    tabs:{query:async () => [{id:1, url:pages.get('bilibili')}]},
    storage:{session:{get:async () => ({1:{...job, platform:'bilibili', expires:Date.now() + 60000}})}},
    scripting:{executeScript}
  };
  t.after(() => { delete globalThis.document; delete globalThis.chrome; });
  await import('./popup.js');
  await new Promise(resolve => setImmediate(resolve));
  targetStatus = 'published';
  await controls.get('apply').onclick();
  assert.equal(executeScript.mock.callCount(), 0);
  assert.match(controls.get('status').textContent, /已发布/);
});

for (const [platform, page] of pages) test(`${platform} transfers media bytes and literal text to matched controls`, async t => {
  t.mock.method(globalThis, 'setTimeout', () => 0);
  globalThis.location = new URL(page);
  globalThis.HTMLInputElement = class { set value(text) { this.text = text; } };
  globalThis.DataTransfer = class {
    files = [];
    items = {add:file => this.files.push(file)};
  };
  const events = [];
  const control = {getClientRects:() => [1], dispatchEvent:e => events.push(e.type)};
  const file = {...control, accept:'video/*'};
  const title = Object.assign(new HTMLInputElement(), control, {tagName:'INPUT'});
  const body = {...control, isContentEditable:true};
  globalThis.document = {querySelectorAll: selector => {
    if (selector.startsWith('input[type=file]')) return [file];
    if (selector.startsWith('input[placeholder')) return [title];
    if (selector.startsWith('textarea[placeholder')) return [body];
    throw Error('Unexpected DOM access');
  }};
  const run = (command, data) => fill(command, data, page, 'test');
  run('start');
  run('chunk', 'AAH/');
  run('file', {name:'clip.mp4', type:'video/mp4'});
  assert.match(run('apply', {title:'My title', body:'<script>example</script>'}), /已选择 1/);
  assert.equal(title.text, 'My title');
  assert.equal(body.textContent, '<script>example</script>');
  assert.deepEqual([...new Uint8Array(await file.files[0].arrayBuffer())], [0, 1, 255]);
  assert.deepEqual(events, ['change', 'input', 'change', 'input', 'change']);
  run('start');
  globalThis.document.querySelectorAll = () => [];
  assert.match(run('apply', {title:'Retry'}), /尚未就绪/);
  globalThis.location = new URL('https://example.com');
  assert.throws(() => run('start'));
});
