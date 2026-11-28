/* A single home for each material source, independent of account reporting. */
(() => {
  const root=byId('channels'),tabs=root.querySelector('.sourceWorkspaceTabs');
  const library=byId('onlineLibraries');library.classList.add('workCard');root.append(library);
  const interval = byId('source').elements.minutes;
  const minutes = element('input');
  minutes.name = 'minutes'; minutes.type = 'number'; minutes.required = true;
  minutes.min = '0'; minutes.max = '43200'; minutes.step = '1';
  minutes.defaultValue = interval.value;
  const presets = element('datalist');
  presets.id = 'sourceIntervalOptions';
  for (const option of interval.options) presets.append(new Option(option.text, option.value));
  minutes.setAttribute('list', presets.id);
  interval.replaceWith(minutes, presets, element('small', '单位为分钟，填 0 表示手动更新。', 'fieldHelp'));
  const panels=['collectionTasks','onlineLibraries','sourcesCard'];
  let selected='collectionTasks';
  function open(id=selected){
    if(!panels.includes(id))return false;
    selected=id;
    if (route?.panel === 'channels') recordRoute('channels', {section:id});
    for(const key of panels){const panel=byId(key);panel.hidden=key!==id;if(panel.tagName==='DETAILS')panel.open=key===id;}
    for(const button of tabs.querySelectorAll('[role=tab]')){
      const active=button.dataset.config===id;
      button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;
    }
    return true;
  }
  tabs.onkeydown=event=>{
    const buttons=[...tabs.querySelectorAll('[role=tab]')],i=buttons.indexOf(document.activeElement);
    if(i<0)return;let next;
    if(event.key==='ArrowRight')next=(i+1)%buttons.length;
    else if(event.key==='ArrowLeft')next=(i-1+buttons.length)%buttons.length;
    else if(event.key==='Home')next=0;else if(event.key==='End')next=buttons.length-1;else return;
    event.preventDefault();buttons[next].click();buttons[next].focus();
  };
  window.sourceWorkspace={open, get current(){return selected;}};open();
  window.renderCollectionMedia = async (detail, item, project, refresh) => {
    const panel = element('section');
    panel.setAttribute('aria-live','polite');
    detail.append(panel);
    const path = '/api/projects/'+project+'/collection-materials/'+item.run_id+'/'+item.material_id;
    const current = () => panel.isConnected && project === projectId;
    function draw(report, difference='') {
      panel.replaceChildren(element('h3','媒体提取结果'));
      for (const [label, present] of Object.entries(report.parts)) panel.append(element('p',label+' · '+(present?'已取得':'未取得')));
      if (report.speech) {
        const transcript=element('details');transcript.append(element('summary','查看字幕或转写'),element('p',report.speech));panel.append(transcript);
      }
      if (difference) panel.append(element('p',difference));
      if (report.can_retry) {
        const retry=element('button','仅补本条字幕或转写','secondary');retry.type='button';panel.append(retry);
        retry.onclick=async()=>{
          if(!current() || retry.disabled)return;
          retry.disabled=true;
          try {
            const result=await api(path+'/retry-speech','POST');
            if(current()) await refresh(result.after,'字幕或转写：'+(result.before.parts['字幕或转写']?'已取得':'未取得')+' → '+(result.after.parts['字幕或转写']?'已取得':'未取得')+'；剩余缺项 '+result.after.missing.length+' 项。');
          } catch(error){if(current())draw(report,error.message);}
        };
      }
      if (report.missing.length || report.can_retry) {
        for (const reason of report.missing) panel.append(element('p',reason,'fieldHelp'));
        if(report.task_id){
          const label=report.latest_batch?'前往原任务重新解析本批':'前往原任务重新采集';
          const go=element('button',label,'secondary');go.type='button';panel.append(go);
          go.onclick=handle(async()=>{
            await window.openConfigSection('collectionTasks');
            if(project!==projectId)return;
            await window.collectionTasks.focusTask(report.task_id);
            if(project!==projectId)return;
            const task=byId('taskList').querySelector('[data-task-id="'+report.task_id+'"]');
            if(!task){notify('原任务已删除，可重新添加平台采集任务。','info');return;}
            const action=task.querySelector('[data-operation="'+(report.latest_batch?'reprocess':'run')+'"]');
            action?.focus();task.scrollIntoView({block:'center'});
          });
        }else{
          const retry=element('button','重新导入原文件或正文','secondary');retry.type='button';
          retry.onclick=()=>window.openImportView();panel.append(retry);
        }
        const settings=element('button','检查内容处理服务','ghost');settings.type='button';
        settings.onclick=()=>window.openConfigSection('connectionSettings');panel.append(settings);
      }
    }
    try {const report=await api(path);if(current())draw(report);}
    catch(error){if(current())panel.textContent=error.message;}
  };
})();
