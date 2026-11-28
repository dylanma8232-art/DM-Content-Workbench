// Chrome copies this function into the top document's isolated world.
export function fill(command, data, page, token) {
  const expected = new URL(page);
  if (location.origin !== expected.origin || location.pathname.replace(/\/$/, '') !== expected.pathname) throw Error('请登录并返回创作上传页，再打开助手。');
  if (command === 'start') {
    globalThis.dmPublishBuffer = {token, files: [], chunks: []};
    setTimeout(() => {
      if (globalThis.dmPublishBuffer?.token === token) delete globalThis.dmPublishBuffer;
    }, 30 * 60_000);
    return true;
  }
  const state = globalThis.dmPublishBuffer;
  if (state?.token !== token) throw Error('传输已中断，请重新打开助手。');
  if (command === 'clear') { delete globalThis.dmPublishBuffer; return; }
  if (command === 'chunk') {
    state.chunks.push(Uint8Array.from(atob(data), c => c.charCodeAt(0)));
    return true;
  }
  if (command === 'file') {
    state.files.push(new File(state.chunks, data.name, {type: data.type}));
    state.chunks = [];
    return true;
  }
  const results = [];
  if (state.files.length) {
    const inputs = [...document.querySelectorAll('input[type=file]:not(:disabled)')].filter(input => {
      const accepts = input.accept.toLowerCase().split(',').map(x => x.trim()).filter(Boolean);
      return (state.files.length === 1 || input.multiple) && state.files.every(file => accepts.some(a =>
        a.startsWith('.') ? file.name.toLowerCase().endsWith(a) : a.endsWith('/*') ? file.type.startsWith(a.slice(0, -1)) : file.type === a));
    });
    if (inputs.length === 1) {
      const transfer = new DataTransfer();
      state.files.forEach(file => transfer.items.add(file));
      inputs[0].files = transfer.files;
      inputs[0].dispatchEvent(new Event('change', {bubbles: true}));
      results.push(`已选择 ${state.files.length} 个素材，请检查上传进度。`);
    } else results.push('未找到唯一匹配的素材入口，请切换图文或视频模式，或手动上传。');
  }
  const fields = [
    ['标题', data.title, 'input[placeholder*="标题"],textarea[placeholder*="标题"]'],
    ['正文', data.body, 'textarea[placeholder*="简介"],textarea[placeholder*="正文"],[contenteditable="true"][data-placeholder*="简介"],[contenteditable="true"][data-placeholder*="正文"],.tiptap[contenteditable="true"],.ql-editor[contenteditable="true"],.desc-editor[contenteditable="true"]']
  ];
  for (const [label, value, selector] of fields) {
    if (typeof value !== 'string' || !value) continue;
    const nodes = [...document.querySelectorAll(selector)].filter(n => n.getClientRects().length && !n.disabled && !n.readOnly);
    if (nodes.length !== 1) { results.push(`${label}输入框尚未就绪，请打开编辑表单后再次填入文案。`); continue; }
    const field = nodes[0];
    if (field.isContentEditable) field.textContent = value;
    else {
      const prototype = field.tagName === 'INPUT' ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
      Object.getOwnPropertyDescriptor(prototype, 'value').set.call(field, value);
    }
    field.dispatchEvent(new Event('input', {bubbles: true}));
    field.dispatchEvent(new Event('change', {bubbles: true}));
    results.push(`已填入${label}，请核对。`);
  }
  delete globalThis.dmPublishBuffer;
  return results.join('\n') || '该稿件没有可填入的内容。';
}
