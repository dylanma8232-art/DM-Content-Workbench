(() => {
  let pendingRun=null, pending=new Map();
  function mountCleaning(run, container) {
    if (run.stage!==2 || !run.payload.cleaned) return;
    pendingRun=run.id; pending=new Map();
    const fold=element('details','','workFold');
    fold.id = 'currentCleaningReview';
    fold.append(element('summary','人工核对清洗结果 · 保留、剔除或恢复素材'));
    fold.append(element('p','原始素材会完整保留。选择「自动判断」恢复项目规则；保存筛选后，再执行分析。','configHelp'));
    const controls=element('div','','formActions');
    const only=element('input');only.type='checkbox';
    const onlyLabel=element('label','只看被剔除的素材');onlyLabel.prepend(only);
    const sources = new Map(run.payload.materials.map(item => [item.id, item]));
    const counts = new Map();
    function filterReason(row) {
      const label = row.automatic_reason || row.reason;
      return label === '低于最短正文长度' && !row.body ? '空正文' : label;
    }
    for (const row of run.payload.cleaned) {
      const label = filterReason(row);
      counts.set(label, (counts.get(label) || 0) + 1);
    }
    const reason = element('select');
    reason.add(new Option('全部原因（' + run.payload.cleaned.length + '）', ''));
    for (const [label, count] of counts) reason.add(new Option(label + '（' + count + '）', label));
    const reasonLabel = element('label', '按清洗原因筛选');
    reasonLabel.append(reason);
    const rows=element('div','','cleaningChoices');
    const status=element('p','','configHelp');status.setAttribute('role','status');
    const save=element('button','保存人工筛选','secondary');save.type='button';save.disabled=true;
    const complete=element('button','确认本批复核完成','secondary');complete.type='button';
    const previous=element('button','上一页','ghost'), next=element('button','下一页','ghost');
    previous.type=next.type='button';
    let page=0, saving=false;
    const changes=pending;
    function render() {
      const all=run.payload.cleaned.filter(row=>(!only.checked||row.reason!=='保留') && (!reason.value || reason.value === filterReason(row)));
      page=Math.max(0,Math.min(page,Math.max(0,Math.ceil(all.length/30)-1)));
      rows.replaceChildren();
      for(const row of all.slice(page*30,(page+1)*30)) {
        const entry=element('div','','cleaningChoice');
        const source=sources.get(row.id);
        entry.append(element('strong','#'+row.id+' '+(source?.title||'未命名素材')),element('p',row.reason+(row.same_as?' · 与 #'+row.same_as+' 重复':''),'configHelp'));
        const preview=element('details');preview.append(element('summary','查看清洗文本'),element('p',row.body||'正文为空，请在素材库核对图片、语音和原始依据。'));entry.append(preview);
        const original = sources.get(row.same_as);
        if (original) {
          const comparison = element('details');
          comparison.append(element('summary','对照两条原文'));
          const pair = element('div','','pair');
          for (const item of [source, original].filter(Boolean)) {
            const section = element('section');
            section.append(element('h4','#' + item.id + ' ' + item.title), element('p',item.body || '原文无正文'));
            pair.append(section);
          }
          comparison.append(pair); entry.append(comparison);
        }
        const decision=element('select');
        for(const [value,text] of [['auto','自动判断'],['keep','人工保留'],['exclude','人工剔除']])decision.add(new Option(text,value));
        decision.value=pending.get(row.id)||row.manual_decision||'auto';
        decision.setAttribute('aria-label','素材 '+row.id+' 的清洗决定');
        decision.onchange=()=>{pending.set(row.id,decision.value);save.disabled=false;status.textContent='有 '+pending.size+' 条尚未保存的决定。请先保存再分析。';};
        entry.append(decision);rows.append(entry);
      }
      previous.disabled=page===0;next.disabled=(page+1)*30>=all.length;
      if(!pending.size)status.textContent='共 '+all.length+' 条，当前第 '+(page+1)+' 页。';
    }
    async function persist(reviewed) {
      if((!changes.size && !reviewed) || saving)return;
      saving=true;
      for(const control of fold.querySelectorAll('input,select,button')) control.disabled=true;
      status.textContent='正在保存筛选结果…';
      try {
        const result=await api('/api/runs/'+run.id+'/cleaning','PUT',{revision:run.payload.cleaning_revision||0,decisions:Object.fromEntries(changes),reviewed});
        changes.clear();
        if (fold.isConnected && active === run && !byId('detail').hidden) {
          await show(result);notify('人工筛选已保存，可以继续分析。','ok');
        }
      }catch(error){
        saving=false;only.disabled=false;reason.disabled=false;save.disabled=!changes.size;complete.disabled=false;render();status.textContent=error.message;
      }
    }
    save.onclick=()=>persist(false);complete.onclick=()=>persist(true);
    previous.onclick=()=>{page--;render();};next.onclick=()=>{page++;render();};only.onchange=()=>{page=0;render();};
    reason.onchange=()=>{page=0;render();};
    controls.append(reasonLabel,onlyLabel,previous,next,save,complete);fold.append(controls,status,rows);container.append(fold);render();
  }
  window.stageTools={mountCleaning,hasPending:run=>pendingRun===run&&pending.size>0};
})();
