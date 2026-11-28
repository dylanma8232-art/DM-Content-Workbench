import './douyin-daily.mjs';
import {collectionTask} from './collection.mjs';
import {base, draft, pages} from './workbench.mjs';

async function open(message, sender) {
  if (sender.id !== chrome.runtime.id || !sender.tab || sender.frameId !== 0 || new URL(sender.url).origin !== sender.origin) throw Error('请在本机工作台打开发布任务。');
  if (message.type === 'collect-open') {
    const job = {kind:'collection', origin:sender.origin, project:message.project, source:message.source};
    const task = await collectionTask(job);
    const tab = await chrome.tabs.create({url:task.url});
    await chrome.storage.session.set({[String(tab.id)]:{...job, task, expires:Date.now() + 30 * 60_000}});
    return;
  }
  const {project, post, target} = message;
  const job = {project, post, target, origin: sender.origin};
  base(job);
  const record = await draft(job);
  const tab = await chrome.tabs.create({url: pages.get(record.target.platform)});
  await chrome.storage.session.set({[String(tab.id)]: {...job, platform: record.target.platform, expires: Date.now() + 30 * 60_000}});
}

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (!['open','collect-open'].includes(message?.type)) return;
  open(message, sender).then(() => reply({ok: true}), error => reply({ok: false, error: error.message}));
  return true;
});
chrome.tabs.onRemoved.addListener(tabId => chrome.storage.session.remove(String(tabId)));
