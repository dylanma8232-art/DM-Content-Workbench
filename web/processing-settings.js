(() => {
  const local=byId('connectLocalProcessing');
  const custom=byId('customProcessing');
  const toggle=byId('showCustomProcessing');
  const status=byId('processingSettingsStatus');
  let busy=false;
  const names=['model_base','model_name','asr_base','asr_name'];
  function fill(settings) {
    delete connectionChecks.model; delete connectionChecks.transcription;
    for (const name of names) custom.elements[name].value=settings[name] || '';
    custom.elements.model_key.value=''; custom.elements.asr_key.value='';
    const savedKeyHint = '当前地址已保存密钥；更换地址时请重填';
    custom.elements.model_key.placeholder=settings.model_key_saved ? savedKeyHint : '没有密钥可留空';
    custom.elements.asr_key.placeholder=settings.asr_key_saved ? savedKeyHint : '没有密钥可留空';
  }
  function controls(value) {
    busy=value;
    local.disabled=value; toggle.disabled=value; custom.querySelector('button[type=submit]').disabled=value;
    status.setAttribute('aria-busy',String(value));
  }
  toggle.onclick=()=>{
    custom.hidden=!custom.hidden;
    toggle.setAttribute('aria-expanded',String(!custom.hidden));
    if (!custom.hidden) custom.elements.model_base.focus();
  };
  local.onclick=async()=>{
    if (busy) return;
    controls(true);
    status.textContent='正在连接这台电脑的本地模型和语音服务…';
    try {
      const result=await api('/api/processing-settings/local','POST',{});
      fill(result);
      renderEnvMatrix(await api('/api/connections'));
      custom.hidden=true; toggle.setAttribute('aria-expanded','false');
      status.textContent='已连接本机内容理解和语音服务。现在可以返回「采集管理」开始采集。';
    } catch (error) {
      status.textContent=error.message;
    } finally {controls(false);}
  };
  custom.onsubmit=async event=>{
    event.preventDefault();
    if (busy) return;
    const body=Object.fromEntries(names.map(name=>[name,custom.elements[name].value.trim()]));
    if (Boolean(body.asr_base)!==Boolean(body.asr_name)) {
      status.textContent='语音服务地址和模型名称需要一起填写，或者一起留空。'; return;
    }
    for (const key of ['model_key','asr_key']) if (custom.elements[key].value) body[key]=custom.elements[key].value;
    controls(true); status.textContent='正在保存并检测内容理解连接…';
    let saved=false;
    try {
      const settings=await api('/api/processing-settings/custom','POST',body);
      saved=true; fill(settings);
      const result=await api('/api/connections/check','POST',{target:'model'});
      connectionChecks.model={ok:true,status:'连接已验证',summary:checkSummary('model','内容理解服务已响应。图片和视频画面的理解效果需另行试用。'),message:result.message};
      renderEnvMatrix(await api('/api/connections'));
      status.textContent='设置已保存，内容理解连接检测通过。可以开始采集；语音效果请用一段短音频验证。';
    } catch (error) {
      status.textContent=(saved ? '设置已保存，但连接检测未通过。' : '设置未保存。')+error.message;
      if (saved) {
        connectionChecks.model={ok:false,status:'检测未通过',summary:checkSummary('model',error.message+'。请核对模型地址、名称及密钥。')};
        renderEnvMatrix(await api('/api/connections'));
      }
    } finally {controls(false);}
  };
  const scanForm = byId('scanSettings');
  const scanStatus = byId('scanSettingsStatus');
  const scanSave = scanForm.querySelector('button[type=submit]');
  scanForm.onsubmit = async event => {
    event.preventDefault();
    if (scanSave.disabled) return;
    scanSave.disabled = true;
    scanStatus.textContent = '正在保存…';
    try {
      const settings = await api('/api/processing-settings/ocr', 'POST', {
        enabled: scanForm.elements.enabled.checked, language: scanForm.elements.language.value.trim()
      });
      delete connectionChecks.ocr;
      scanStatus.textContent = settings.ocr_enabled ? '已启用。可在「更多连接检测」检查 OCR 工具，然后导入一份扫描件。' : '已停用扫描 PDF 文字识别。';
    } catch (error) {
      scanStatus.textContent = error.message;
      return;
    } finally { scanSave.disabled = false; }
    try { renderEnvMatrix(await api('/api/connections')); }
    catch { scanStatus.textContent += ' 能力状态暂未刷新，请重新打开页面查看。'; }
  };
  api('/api/processing-settings').then(settings=>{
    fill(settings);
    scanForm.elements.enabled.checked = settings.ocr_enabled;
    scanForm.elements.language.value = settings.ocr_language;
    scanSave.disabled = false;
    status.textContent=settings.mode==='local' ? '当前使用这台电脑的本地服务；如连接失效，可点击上方按钮重新连接。' : settings.model_base ? '当前使用自有服务。展开「使用我自己的模型服务」可以修改连接。' : '尚未选择服务。已安装本地模型时，点击上方按钮连接。';
  }).catch(()=>{scanStatus.textContent = status.textContent = '连接设置暂时没有加载成功，请刷新页面重试。';});
})();
