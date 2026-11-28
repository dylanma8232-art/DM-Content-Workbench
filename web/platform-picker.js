/* One fixed platform catalog for collection, onboarding and account reporting. */
window.DMPlatforms = [
  {id:'xiaohongshu', label:'小红书', accountName:'小红书', icon:'xhs', aliases:['xhs','rednote'], color:'#b22846', tint:'#fff1f2'},
  {id:'bilibili', label:'B站', accountName:'哔哩哔哩', icon:'bilibili', aliases:['b站','哔哩哔哩','bilibili'], color:'#12779a', tint:'#edf8fc'},
  {id:'douyin', label:'抖音', accountName:'抖音', icon:'douyin', aliases:['douyin'], color:'#333640', tint:'#f1f2f5'},
  {id:'instagram', label:'Instagram', accountName:'Instagram', icon:'instagram', aliases:['instagram'], color:'#873e83', tint:'#fbf0fa'},
  {id:'tiktok', label:'TikTok', accountName:'TikTok', icon:'tiktok', aliases:['tiktok'], color:'#333640', tint:'#f1f2f5'},
  {id:'x', label:'X', accountName:'X', icon:'x', aliases:['x','twitter'], color:'#333640', tint:'#f1f2f5'}
];

(() => {
  const catalog = window.DMPlatforms;
  function brand(value) {
    const key=String(value || '').trim().toLowerCase();
    return catalog.find(p=>[p.id,p.label.toLowerCase(),p.accountName.toLowerCase(),...p.aliases].includes(key));
  }
  window.syncPlatformPicker = form => {
    const picker=form.querySelector('[data-platform-picker]');
    if (!picker) return;
    const input=form.elements.platform;
    const selected=brand(input.value) || catalog[0];
    input.value=picker.dataset.platformValue === 'account' ? selected.accountName : selected.id;
    for (const button of picker.querySelectorAll('[data-platform]')) {
      const active=button.dataset.platform===selected.id;
      button.setAttribute('aria-checked',String(active));
      button.tabIndex=active ? 0 : -1;
    }
  };
  window.mountPlatformPicker = (form, selected, onChange) => {
    const picker=form.querySelector('[data-platform-picker]');
    if (!picker) return;
    picker.setAttribute('role','radiogroup');
    picker.setAttribute('aria-label','选择内容平台');
    if (!picker.children.length) {
      for (const platform of catalog) {
        const button=document.createElement('button');
        button.type='button'; button.dataset.platform=platform.id;
        button.className='platformPick'; button.setAttribute('role','radio');
        const icon=document.createElement('img');
        icon.src='/icons/'+platform.icon+'.svg?v=20261127-ui6'; icon.alt=''; icon.width=28; icon.height=28;
        const label=document.createElement('span'); label.textContent=platform.label;
        button.append(icon,label);
        button.onclick=()=>{
          form.elements.platform.value=picker.dataset.platformValue==='account' ? platform.accountName : platform.id;
          window.syncPlatformPicker(form);
          form.elements.platform.dispatchEvent(new Event('input',{bubbles:true}));
          form.elements.platform.dispatchEvent(new Event('change',{bubbles:true}));
          if (picker.onPlatformChange) picker.onPlatformChange(platform.id);
        };
        picker.append(button);
      }
      picker.addEventListener('keydown',event=>{
        const buttons=[...picker.querySelectorAll('[data-platform]')];
        const current=buttons.indexOf(document.activeElement);
        if (current<0) return;
        const offsets={ArrowRight:1,ArrowDown:1,ArrowLeft:-1,ArrowUp:-1};
        let next;
        if (event.key in offsets) next=(current+offsets[event.key]+buttons.length)%buttons.length;
        else if (event.key==='Home') next=0;
        else if (event.key==='End') next=buttons.length-1;
        else return;
        event.preventDefault(); buttons[next].click(); buttons[next].focus();
      });
    }
    picker.onPlatformChange=onChange;
    if (selected) form.elements.platform.value=selected;
    window.syncPlatformPicker(form);
  };
  // Loaded after the HTML and before application scripts: logos are ready on first render.
  document.querySelectorAll('form:has([data-platform-picker])').forEach(form=>window.mountPlatformPicker(form));
})();
