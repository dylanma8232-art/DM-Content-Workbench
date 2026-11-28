/* Shared 24px vector vocabulary. All paths are fixed application artwork. */
(() => {
  const paths={
    overview:'<rect x="3" y="3" width="7" height="7" rx="1.8"/><rect x="14" y="3" width="7" height="7" rx="1.8"/><rect x="3" y="14" width="7" height="7" rx="1.8"/><rect x="14" y="14" width="7" height="7" rx="1.8"/>',
    workflow:'<path d="M5 4v12a3 3 0 0 0 3 3h11M5 9h14"/><circle cx="5" cy="4" r="2"/><circle cx="19" cy="9" r="2"/><circle cx="19" cy="19" r="2"/>',
    collect:'<path d="M5 8a10 10 0 0 1 14 0M8 11a6 6 0 0 1 8 0M11 14a2 2 0 0 1 2 0"/><circle cx="12" cy="18" r="1"/>',
    import:'<path d="M12 3v12m-4-4 4 4 4-4M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/>',
    materials:'<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/><path d="M3 10h18"/>',
    topic:'<path d="M9 18h6m-5 3h4M8 14a6 6 0 1 1 8 0c-1 .8-1 2-1 2H9s0-1.2-1-2Z"/>',
    draft:'<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9ZM14 3v6h6M8 13h8m-8 4h5"/>',
    publish:'<path d="m21 3-7 18-4-7-7-4ZM10 14 21 3"/>',
    settings:'<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/>',
    plus:'<path d="M12 5v14M5 12h14"/>',
    chevron:'<path d="m9 5 7 7-7 7"/>',
    down:'<path d="m6 9 6 6 6-6"/>',
    edit:'<path d="m15 5 4 4M4 20l4-1L20 7a2.8 2.8 0 0 0-4-4L4 15ZM12 20h8"/>',
    user:'<circle cx="10" cy="8" r="3"/><path d="M3 21v-2a7 7 0 0 1 12-5m4-2v8m-4-4h8"/>',
    cloud:'<path d="M7 18H6a4 4 0 0 1-.5-8A6.5 6.5 0 0 1 18 8a5 5 0 0 1 0 10h-1M12 13v8m-3-5 3-3 3 3"/>',
    menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',
    check:'<path d="m5 12 4 4L19 6"/>',
    arrow:'<path d="M4 12h16m-6-6 6 6-6 6"/>'
  };
  function icon(name){
    const node=document.createElementNS('http://www.w3.org/2000/svg','svg');
    for(const [k,v] of Object.entries({viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':'1.7','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true',focusable:'false',class:'uiIcon'}))node.setAttribute(k,v);
    node.innerHTML=paths[name]||paths.draft;
    return node;
  }
  window.DMIcons={icon};
  const icons={home:'overview',workNav:'workflow',channelNav:'collect',materialsNav:'materials',topicsNav:'topic',draftsNav:'draft',publishingNav:'publish',projectSettings:'settings',quickNewProject:'plus',heroImportBtn:'import',sourceImportBtn:'import',addReport:'plus',platformSetupBtn:'user',importDailyCsv:'import'};
  for(const [id,name] of Object.entries(icons)){
    const button=document.getElementById(id);if(!button)continue;
    if(id==='quickNewProject'){
      const label=document.createElement('span');label.className='uiVisuallyHidden';label.textContent='新建项目';
      button.replaceChildren(label);button.setAttribute('aria-label','新建项目');
    }
    button.prepend(icon(name));
    button.classList.add('withIcon');
  }
  for(const button of document.querySelectorAll('.sourceEntryChoice[data-config]'))button.prepend(icon(button.dataset.config==='onlineLibraries'?'cloud':'collect'));
})();
