/* Keep configuration in one workspace; every control still uses its original API. */
(() => {
  const settings=byId('settings'), nav=settings.querySelector('.sectionJumps');
  const workspace=element('div','','settingsWorkspace');
  settings.prepend(byId('backFromSettings'));
  const body=element('div','','settingsGroup');
  const panels=['storageAdvanced','thirdParty','rulesCard','connectionSettings','projectCard'].map(byId).filter(Boolean);
  nav.classList.add('settingsTabs');nav.setAttribute('aria-label','配置分类');
  settings.append(workspace);workspace.append(nav,body);
  for(const panel of panels){panel.classList.add('configSection');body.append(panel);}
  let current='rulesCard';
  const stages=['cleaningConfig','analysisConfig','topicConfig','writingConfig','archiveConfig'];
  const narrowRules = matchMedia('(max-width: 700px)');
  let mobileStage = stages[0];
  const scopes=element('p','','configHelp');scopes.setAttribute('role','status');
  body.prepend(scopes);
  function open(target=current) {
    const connector=['reportConnector','draftConnector'].includes(target);
    const node=byId(connector?'thirdParty':target);
    const panel=panels.find(item=>item===node||item.contains(node));
    if(!panel)return false;
    current=target;
    if (route?.panel === 'settings') recordRoute('settings', {section:target});
    for(const item of panels)item.hidden=item!==panel;
    byId('storageSettings').hidden=false;
    if(connector){
      byId('connectorKind').value=target==='draftConnector'?'drafts':'reports';
      showConnectorForm();
    }
    byId('connectorKind').closest('label').hidden=connector;
    const specificStage = stages.includes(target);
    if (specificStage) mobileStage = target;
    const selectedStage = specificStage ? target : narrowRules.matches ? mobileStage : null;
    for (const id of stages) byId(id).hidden = panel.id === 'rulesCard' && !!selectedStage && id !== selectedStage;
    const stageNav = byId('workflowSettingsNav');
    if (stageNav) {
      stageNav.hidden = specificStage && !narrowRules.matches;
      for (const button of stageNav.querySelectorAll('button')) {
        if (selectedStage) button.setAttribute('aria-pressed', String(button.getAttribute('aria-controls') === selectedStage));
        else button.removeAttribute('aria-pressed');
      }
    }
    for(const preset of byId('rulesCard').querySelectorAll(':scope > .presetSection'))preset.hidden=stages.includes(target);
    const credentials=byId('serviceCredentials');
    if(credentials)credentials.hidden=panel.id==='connectionSettings'&&target!=='serviceCredentials';
    for(const id of ['processingConnection','envMatrix','scanSettingsPanel','advancedConnectionCheck'])byId(id).hidden=panel.id==='connectionSettings'&&target==='serviceCredentials';
    for(const link of nav.querySelectorAll('a')) {
      const category = target === 'newProject' || target === 'restorePage' ? 'projectCard' : stages.includes(target) ? 'rulesCard' : target;
      const active=link.hash==='#'+category;
      if(active)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
    }
    const global=['storageAdvanced','connectionSettings'].includes(panel.id);
    scopes.textContent=global ? '全局服务与存储 · 影响这台工作台的所有项目。' : panel.id==='rulesCard' ? '项目默认规则 · '+currentProject().name+'。导入时可在「本批清洗与分析参数」单独覆盖。' : '当前项目：'+currentProject().name;
    return true;
  }
  for(const link of nav.querySelectorAll('a[href^="#"]'))link.onclick=handle(async event=>{
    const target=link.getAttribute('href').slice(1);
    if(['onlineLibraries','collectionTasks','sourcesCard'].includes(target)){await openConfigSection(target);return;}
    open(target);(byId(target)||byId('thirdParty')).scrollIntoView({block:'start'});
  });
  narrowRules.addEventListener('change', () => { if (current === 'rulesCard' || stages.includes(current)) open(current); });
  byId('rules').addEventListener('invalid', event => {
    const group = event.target.closest('fieldset.stageSettings');
    if (group?.hidden) open(group.id);
  }, true);
  window.configurationCenter={open,showCurrent:()=>open(current), get current(){return current;}};
  open();
})();
