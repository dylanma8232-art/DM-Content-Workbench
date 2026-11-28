import {creatorHome, creatorPermission, identity, readCreator} from './douyin-page.mjs';

const read = document.querySelector('#read');
const confirm = document.querySelector('#confirm');
const message = document.querySelector('#message');
let candidate;
async function request(action, data) {
  const result = await chrome.runtime.sendMessage({type: 'douyin-approval', action, ...data});
  if (!result?.ok) throw Error(result?.error || '请从工作台重新打开连接页。');
  return result.status;
}
try {
  const {origin, project} = await request('info');
  document.querySelector('#scope').textContent = `工作台 ${origin} · 项目 ${project}`;
  read.disabled = false;
} catch (error) { message.textContent = error.message; }
read.onclick = async () => {
  read.disabled = true;
  confirm.hidden = true;
  let tab;
  try {
    if (!await chrome.permissions.request(creatorPermission)) throw Error('未获得创作者中心读取权限。');
    message.textContent = '正在核对创作者中心显示的账号…';
    tab = await chrome.tabs.create({url: creatorHome, active: false});
    candidate = await readCreator(tab.id, creatorHome, identity);
    message.textContent = `昵称：${candidate.account}\n抖音号：${candidate.platform_uid}`;
    confirm.hidden = false;
  } catch (error) { message.textContent = error.message; }
  finally { read.disabled = false; if (tab) await chrome.tabs.remove(tab.id).catch(() => {}); }
};
confirm.onclick = async () => {
  confirm.disabled = read.disabled = true;
  try {
    const result = await request('confirm', candidate);
    message.textContent = `已绑定 ${result.account}（${result.platform_uid}）。请返回工作台查看回填结果或开启每日定时。`;
    confirm.hidden = read.hidden = true;
  } catch (error) { message.textContent = error.message; confirm.disabled = read.disabled = false; }
};
