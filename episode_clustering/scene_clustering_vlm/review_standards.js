(() => {
  'use strict';
  const schemaVersion = 1;
  const storageKey = 'vlm-review-standards-v1:' + location.pathname;
  const groupKey = (t, g) => `${t.task_id}/${g.cluster_id}`;
  const manifest = DATA.tasks.map(t => ({task_id:t.task_id, task:t.task,
    groups:t.groups.map(g => ({cluster_id:g.cluster_id, episode_ids:[...g.member_episode_ids].sort()}))}));
  const taskIds = new Set(DATA.tasks.map(t => String(t.task_id)));
  const groupIds = new Set(DATA.tasks.flatMap(t => t.groups.map(g => groupKey(t,g))));
  let state = {version:schemaVersion, dataset_manifest:manifest, task_standards:{}, group_standards:{}, updated_at:null};
  const message = (text, error=false) => {
    $('standards-status').textContent = text;
    $('standards-status').classList.toggle('error', error);
  };
  function validate(value) {
    if (!value || value.version !== schemaVersion || JSON.stringify(value.dataset_manifest) !== JSON.stringify(manifest)) {
      throw Error('标准文件与当前任务或分组不匹配');
    }
    for (const [name, ids] of [['task_standards',taskIds],['group_standards',groupIds]]) {
      const obj=value[name];
      if (!obj || typeof obj!=='object' || Array.isArray(obj)) throw Error('标准文件格式错误');
      for (const [key,text] of Object.entries(obj)) {
        if (!ids.has(key) || typeof text!=='string') throw Error('标准文件包含未知编号或非文本标准');
      }
    }
    return {version:schemaVersion, dataset_manifest:manifest,
      task_standards:{...value.task_standards}, group_standards:{...value.group_standards}, updated_at:value.updated_at??null};
  }
  try {
    const saved=localStorage.getItem(storageKey);
    if (saved) {state=validate(JSON.parse(saved));message('已恢复本地标准');}
  } catch(e) {message('无法读取本地标准，请导入备份：'+e.message,true);}
  function effective(t,g) {
    const own=state.group_standards[groupKey(t,g)]??'', inherited=state.task_standards[String(t.task_id)]??'';
    return own.trim()?{source:'group',text:own}:inherited.trim()?{source:'task',text:inherited}:{source:'none',text:''};
  }
  function refresh() {
    const t=task(), active=effective(t,group());
    $('effective-source').textContent=active.source==='group'?'当前生效：本组审核标准':active.source==='task'?`当前生效：任务 ${t.task_id} 审核标准`:'当前生效：未设置';
    $('effective-standard').textContent=active.text||'未设置审核标准';
    document.querySelectorAll('#groups [data-group]').forEach(button=>{
      let badge=button.querySelector('.standard-badge');
      if (!badge) {badge=document.createElement('small');badge.className='standard-badge';button.append(badge);}
      const e=effective(t,t.groups[Number(button.dataset.group)]);
      badge.textContent=e.source==='group'?'本组独立标准':e.source==='task'?'沿用任务标准':'标准未设置';
    });
  }
  function sync() {
    $('task-save-status').textContent='';
    $('group-save-status').textContent='';
    $('task-standard-label').textContent=`任务 ${task().task_id} 的默认标准`;
    $('group-standard-label').textContent=`任务 ${task().task_id} / 组 ${gi+1} · ${group().cluster_id}`;
    $('task-standard').value=state.task_standards[String(task().task_id)]??'';
    $('group-standard').value=state.group_standards[groupKey(task(),group())]??'';
    document.querySelector('main').scrollTop=0;
    refresh();
  }
  function persist(statusId=null) {
    state.updated_at=new Date().toISOString();
    try {
      localStorage.setItem(storageKey,JSON.stringify(state));message('已保存到本地浏览器');
      if(statusId) $(statusId).textContent='已保存到本地浏览器 · '+new Date().toLocaleTimeString();
    }
    catch(e) {
      message('本地保存失败，请导出标准备份',true);
      if(statusId) $(statusId).textContent='保存失败，请导出标准备份';
    }
    refresh();
  }
  function saveTask() {state.task_standards[String(task().task_id)]=$('task-standard').value;persist('task-save-status');}
  function saveGroup() {state.group_standards[groupKey(task(),group())]=$('group-standard').value;persist('group-save-status');}
  $('task-standard').addEventListener('input',saveTask);
  $('group-standard').addEventListener('input',saveGroup);
  $('save-task-standard').onclick=saveTask;
  $('save-group-standard').onclick=saveGroup;
  $('export-standards').onclick=()=>{
    const output={...state, exported_at:new Date().toISOString(),resolved_groups:DATA.tasks.flatMap(t=>t.groups.map(g=>{
      const e=effective(t,g);return {task_id:t.task_id,cluster_id:g.cluster_id,episode_ids:g.member_episode_ids,
        source:e.source,effective_standard:e.text};
    }))};
    const url=URL.createObjectURL(new Blob([JSON.stringify(output,null,2)],{type:'application/json'}));
    const a=document.createElement('a');a.href=url;a.download='robodojo_review_standards_'+new Date().toISOString().replace(/[:.]/g,'-')+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);
  };
  $('import-standards').onclick=()=>$('standards-file').click();
  $('standards-file').onchange=async()=>{
    const file=$('standards-file').files[0];if(!file)return;
    try {
      const incoming=validate(JSON.parse(await file.text()));
      const conflicts=['task_standards','group_standards'].some(name=>Object.entries(incoming[name]).some(([key,text])=>state[name][key]?.trim()&&state[name][key]!==text));
      if(conflicts&&!confirm('导入文件将覆盖相同任务或组的已有标准，继续吗？'))return;
      state.task_standards={...state.task_standards,...incoming.task_standards};
      state.group_standards={...state.group_standards,...incoming.group_standards};
      persist();sync();
    } catch(e) {message('导入失败：'+e.message,true);}
    finally {$('standards-file').value='';}
  };
  window.reviewStandards={sync};sync();
})();
