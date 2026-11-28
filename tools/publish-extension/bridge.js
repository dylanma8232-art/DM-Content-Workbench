document.documentElement.dataset.dmDouyinDailyExtension = '1';
window.addEventListener('message', async event => {
  if (event.source !== window || event.origin !== location.origin || event.data?.type !== 'dm-douyin-daily') return;
  const {action, project, requestId} = event.data;
  const response = {type: 'dm-douyin-daily-result', action, project, requestId};
  try {
    if (!['connect','status','enable','retry','disable','disconnect'].includes(action)) throw Error('未知日报操作。');
    if (action !== 'status' && !navigator.userActivation.isActive) throw Error('请点击工作台的日报按钮。');
    const result = await chrome.runtime.sendMessage({type: 'douyin-daily', action, project});
    if (!result?.ok) throw Error(result?.error || '扩展未响应。');
    window.postMessage({...response, ok: true, status: result.status}, location.origin);
  } catch (error) {
    window.postMessage({...response, ok: false, error: error.message}, location.origin);
  }
});

document.documentElement.dataset.dmPublishExtension = '1';
document.documentElement.dataset.dmCollectExtension = '1';
window.addEventListener('message', async event => {
  if (event.source !== window || event.origin !== location.origin || !['dm-publish-open','dm-collect-open'].includes(event.data?.type)) return;
  try {
    if (!navigator.userActivation.isActive) throw Error('请点击工作台的任务按钮。');
    const {project, post, target, source} = event.data;
    const result = await chrome.runtime.sendMessage(event.data.type === 'dm-collect-open' ? {type:'collect-open',project,source} : {type:'open', project, post, target});
    if (!result?.ok) throw Error(result?.error || '发布助手未响应。');
  } catch (error) {
    window.alert(`DM 发布助手\n${error.message}`);
  }
});
