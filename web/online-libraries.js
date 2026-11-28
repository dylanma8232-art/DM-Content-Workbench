(() => {
  const form=byId('onlineLibraryForm'), list=byId('onlineLibraryList'), status=byId('onlineLibraryStatus');
  let editing=null, owner=null, rows=[], busy=false;
  const base=()=>'/api/projects/'+projectId+'/online-libraries';
  function provider(value) {
    form.elements.provider.value=value;
    byId('onlineNotionCredentials').hidden=value!=='notion';
    byId('onlineFeishuCredentials').hidden=value!=='feishu';
    byId('onlineAddressLabel').textContent=value==='notion'?'Notion 数据库链接':'飞书多维表格链接';
    form.elements.body_field.required=value==='feishu';
    form.elements.body_field.placeholder=value==='notion'?'留空读取 Notion 页面正文':'例如：正文';
    byId('onlineBodyLabel').textContent=value==='notion'?'正文列名称（选填）':'正文列名称';
    byId('onlineFieldHelp').textContent=value==='notion'?'标题列填写实际名称。正文在页面里时，正文列留空；正文存在某一列时，填写该列名称。':'填写多维表格中实际的标题和正文列名，不要填列编号。';
    const instructions=byId('onlineLibraryInstructions');
    instructions.replaceChildren();
    const steps=value==='notion' ? [
      ['在 Notion 集成管理页创建内部集成，复制集成密钥。','https://www.notion.so/profile/integrations'],
      ['打开要读取的数据库，在「连接」中添加该集成，赋予读取权限。'],
      ['复制数据库链接填到下方，再填入两列的实际名称。']
    ] : [
      ['在飞书开放平台创建企业自建应用，找到 App ID 和 App Secret。','https://open.feishu.cn/app'],
      ['给应用开通多维表格的读取权限并发布可用版本；在目标多维表格中添加该应用为协作者。'],
      ['打开具体数据表，复制包含 table 参数的链接，再填入标题列和正文列的名称。']
    ];
    const ol=element('ol');
    for(const [text,url] of steps) {
      const li=element('li',text);
      if(url) {const a=element('a',' 打开管理页');a.href=url;a.target='_blank';a.rel='noopener noreferrer';li.append(a);}
      ol.append(li);
    }
    instructions.append(ol,element('p','保存连接后可手动检测和导入。要归档审核稿，请另外授予写入权限，再到稿件详情确认归档。','fieldHelp'));
  }
  function reset(value='notion') {
    editing=null;form.reset();provider(value);
    form.elements.token.placeholder='填写 Notion 集成密钥';
    form.elements.app_secret.placeholder='填写飞书 App Secret';
    form.elements.title_field.value=value==='notion'?'Name':'标题';
    form.elements.body_field.value=value==='notion'?'':'正文';
    form.querySelector('[type=submit]').textContent='保存连接';
  }
  window.selectOnlineProvider=value=>{reset(value);form.elements.name.focus({preventScroll:true});};
  function setBusy(value) {
    busy=value;
    for(const control of form.elements) control.disabled=value;
    for(const button of list.querySelectorAll('button')) button.disabled=value;
    status.setAttribute('aria-busy',String(value));
  }
  function render() {
    list.replaceChildren();
    if(!rows.length) {list.append(element('p','当前项目还没有线上资料库连接。按上面的步骤添加一条即可。','fieldHelp'));return;}
    for(const row of rows) {
      const section=element('section','','onlineLibraryEntry');
      section.append(element('h4',row.name),element('p',(row.provider==='notion'?'Notion':'飞书多维表格')+' · '+(row.status==='ready'?'读取已验证':'已保存，尚未检测')),
        element('p','标题列：'+row.title_field+'；正文：'+(row.body_field||'Notion 页面内容'),'fieldHelp'));
      const actions=element('div','','formActions');
      if(row.fields?.length) section.append(element('p','可读取字段：'+row.fields.join('、')));
      if(row.last_import){
        const receipt=row.last_import;
        section.append(element('p',`最近导入 ${receipt.added} 条 · 来源新增 ${receipt.source_added} / 更新 ${receipt.source_updated} / 跳过 ${receipt.skipped}`));
        if(receipt.run_id){
          const batch=element('button','查看最近导入批次','secondary');batch.type='button';
          batch.onclick=()=>window.openLibrary('materials',{run_id:receipt.run_id});actions.append(batch);
        }
      }
      const edit=element('button','修改连接','ghost');edit.type='button';
      edit.onclick=()=>{
        reset(row.provider);editing=row.id;
        for(const name of ['name','address','title_field','body_field','app_id','table_id']) form.elements[name].value=row[name]||'';
        form.elements.token.placeholder=row.key_saved?'已保存；留空保留':'填写 Notion 集成密钥';
        form.elements.app_secret.placeholder=row.key_saved?'已保存；留空保留':'填写 App Secret';
        form.querySelector('[type=submit]').textContent='保存修改';form.elements.name.focus();
      };
      const check=element('button','检测读取权限','secondary');check.type='button';
      check.onclick=()=>operate(row,'check');
      const read=element('button','导入到素材库','primary');read.type='button';
      read.onclick=()=>operate(row,'import');
      const disconnect=element('button','断开连接','ghost');disconnect.type='button';
      disconnect.onclick=()=>{
        if(!busy && window.confirm('断开「'+row.name+'」并清除本机保存的授权？已导入素材会保留。有稿件归档记录的连接需保留以便查看回执。尚未完成的检测或导入将无法提交结果。')) operate(row,'disconnect');
      };
      actions.append(edit,check,read,disconnect);section.append(actions);list.append(section);
    }
  }
  async function refresh() {
    const id=projectId;
    if(owner!==id) {owner=id;rows=[];status.replaceChildren();reset();render();}
    const result=await api(base());
    if(projectId!==id)return;
    rows=Array.isArray(result)?result:result.connections;
    render();
    await window.workflowSettings?.refreshArchiveTargets?.();
  }
  window.refreshOnlineLibraries=refresh;
  async function operate(row,action) {
    if(busy)return;
    const id=projectId;
    setBusy(true);
    status.textContent={check:'正在检查读取权限和字段，请稍候…',import:'正在读取线上记录并导入，请稍候…',disconnect:'正在断开连接…'}[action];
    try {
      const path=base()+'/'+row.id;
      const result=action==='disconnect'?await api(path,'DELETE'):await api(path+'/'+action,'POST',{});
      if(projectId!==id)return;
      if(action==='disconnect' && editing===row.id)reset();
      await refresh();
      if(projectId!==id)return;
      if(action==='disconnect') {
        status.textContent='连接已断开，本机授权已清除。已导入素材可继续查看。';
      } else if(action==='check') {
        const titles=result.titles||result.sample_titles||[];
        status.textContent='读取检测通过。'+(result.count!==undefined?'本次检测读取 '+result.count+' 条记录。':'')+(titles.length?'\n示例标题：'+titles.join('、'):'')+(result.partial?'\n检测仅读取了部分记录，导入时仍受单次数量限制。':'');
      } else {
        const changes = result.source_added == null ? '' : `（来源新增 ${result.source_added} 条，更新 ${result.source_updated} 条）`;
        status.textContent = result.run_id
          ? `本批导入 ${result.added ?? result.count} 条素材${changes}。点击「查看本次素材」继续清洗和分析。`
          : '没有新增或变更记录，本次未创建重复素材。';
        if (result.skipped) status.append(` 跳过未变更或重复记录 ${result.skipped} 条。`);
        if(result.deferred>0) status.append(element('span',' 还有 '+result.deferred+' 个页面正文待读取；再次点击「导入到素材库」继续，已读取内容会复用。'));
        if(result.partial) status.append(element('span',' 本次只读取了部分内容；嵌套内容或超出分页上限的记录可能尚未取得，请核对原资料库。'));
        if(result.run_id) {
          const button=element('button','查看本次素材','secondary');button.type='button';
          button.onclick=handle(async()=>{if(projectId===id)await window.openLibrary('materials',{run_id:result.run_id});});
          status.append(document.createElement('br'),button);
        }
      }
    }catch(error){if(projectId===id){
      status.textContent=row.name+' · '+error.message;
      const fix=element('button',error.message.includes('字段')?'修改字段映射':'检查授权与连接','secondary');fix.type='button';
      fix.onclick=()=>{
        const section=list.querySelectorAll('section')[rows.findIndex(entry=>entry.id===row.id)];
        section?.querySelector('button.ghost')?.click();
        const field=error.message.includes('正文字段')?'body_field':error.message.includes('字段')?'title_field':'address';
        form.elements[field].focus();
      };status.append(fix);
    }}
    finally{setBusy(false);}
  }
  form.querySelectorAll('[name=provider]').forEach(input=>input.onchange=()=>reset(input.value));
  byId('resetOnlineLibrary').onclick=()=>{reset(form.elements.provider.value);status.textContent='请填写新连接。已保存的连接会保留在下方。';};
  form.onsubmit=async event=>{
    event.preventDefault();if(busy)return;
    const id=projectId,body={};
    for(const name of ['provider','name','address','title_field','body_field','app_id','table_id'])body[name]=form.elements[name].value.trim();
    if(editing!==null)body.id=editing;
    for(const name of ['token','app_secret'])if(form.elements[name].value)body[name]=form.elements[name].value;
    if(editing===null && !(body.provider==='notion'?body.token:body.app_id&&body.app_secret)) {
      status.textContent=body.provider==='notion'?'首次连接请填写 Notion 集成密钥。':'首次连接请填写飞书 App ID 和 App Secret。';return;
    }
    setBusy(true);status.textContent='正在保存连接…';
    try {
      await api(base(),'POST',body);
      if(projectId!==id)return;
      reset(body.provider);await refresh();status.textContent='连接已保存。请点击下方「检测读取权限」；通过后再导入素材。';
    }catch(error){if(projectId===id)status.textContent=error.message;}
    finally{setBusy(false);}
  };
  reset();
})();
