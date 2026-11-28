import {base, draft, get, id, onPage, pages} from './workbench.mjs';
import {showCollection} from './collection-popup.mjs';
import {fill} from './fill.mjs';

const el = name => document.getElementById(name);
const status = text => { el('status').textContent = text; };
const report = error => status(`${error.message}\n请检查工作台连接、平台登录和上传模式，然后重试。`);
const limit = 64 * 1024 * 1024;
const types = {mp4:'video/mp4', mov:'video/quicktime', webm:'video/webm', jpg:'image/jpeg', jpeg:'image/jpeg', png:'image/png', webp:'image/webp', gif:'image/gif'};

async function init() {
  const [tab] = await chrome.tabs.query({active:true, currentWindow:true});
  const job = (await chrome.storage.session.get(String(tab.id)))[tab.id];
  if (!job || Date.now() >= job.expires) throw Error('当前标签页没有有效任务，请从工作台重新打开。');
  if (job.kind === 'collection') return showCollection(tab,job);
  if (!onPage(tab.url, job.platform)) throw Error('请确认平台登录状态并返回上传页。');
  const {post, target} = await draft(job);
  if (target.platform !== job.platform) throw Error('目标平台已修改，请从工作台重新打开。');
  const assets = post.assets || [];
  el('account').textContent = `目标平台 ${target.platform} · 工作台账号 ${target.account || '未指定'}\n请在创作页核对实际登录账号。`;
  el('copy').textContent = [post.title, post.body].filter(Boolean).join('\n\n') || '无标题和正文';
  el('assets').textContent = assets.map(a => a.name).join('\n') || '无素材';
  el('media').disabled = !assets.length;
  el('media').checked = !!assets.length;
  el('review').hidden = false;
  status('填入后请在平台检查草稿。传输期间请保持弹窗打开。');
  el('apply').onclick = async () => {
    el('apply').disabled = true;
    const token = crypto.randomUUID();
    let dest = {tabId:tab.id};
    const run = async (command, data = null) => {
      const [result] = await chrome.scripting.executeScript({target:dest, func:fill, args:[command, data, pages.get(job.platform), token]});
      if (!result || (command !== 'clear' && !result.result)) throw Error('传输中断，请检查上传页并重新打开助手。');
      return result;
    };
    try {
      if (Date.now() >= job.expires) throw Error('任务已过期，请从工作台重新打开。');
      await draft(job);
      const start = await run('start');
      dest = {tabId:tab.id, documentIds:[start.documentId]};
      let total = 0;
      const transferAssets = el('media').checked && assets.length > 0;
      if (transferAssets) for (const asset of assets) {
        status(`正在传输 ${asset.name}…`);
        const response = await get(`${base(job)}/assets/${id(asset.id)}`);
        const type = response.headers.get('Content-Type')?.split(';')[0].toLowerCase();
        const mime = type && type !== 'application/octet-stream' ? type : types[asset.name?.split('.').pop().toLowerCase()];
        if (!/^(image|video)\//.test(mime)) throw Error(`素材类型无法识别：${asset.name}。请手动上传。`);
        const reader = response.body.getReader();
        while (true) {
          const {value, done} = await reader.read();
          if (done) break;
          total += value.length;
          if (total > limit) { await reader.cancel(); throw Error('素材合计超过 64 MiB，请手动上传，并取消勾选素材以填入文案。'); }
          for (let i = 0; i < value.length; i += 32768) {
            await run('chunk', btoa(String.fromCharCode(...value.subarray(i, i + 32768))));
          }
        }
        await run('file', {name:asset.name, type:mime});
      }
      if (transferAssets) await draft(job);
      const result = await run('apply', {title:post.title || '', body:post.body || ''});
      status(result.result);
      el('media').checked = false;
    } catch (error) { report(error); }
    finally {
      if (dest.documentIds) await run('clear').catch(() => {});
      el('apply').disabled = false;
    }
  };
}
init().catch(report);
