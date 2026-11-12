const fs=require('fs'),path=require('path'),vm=require('vm');
const {pathToFileURL}=require('url');
const {chromium}=require('C:/Users/34927/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
  const source='C:/Users/34927/Desktop/robodojo_last_cls_hdbscan_20261111/viewer';
  const context={window:{}};vm.runInNewContext(fs.readFileSync(source+'/data.js','utf8'),context);
  const data=JSON.parse(JSON.stringify(context.window.DATA)),summary=JSON.parse(JSON.stringify(context.window.SUMMARY));
  for(const t of data)for(const e of t.episodes)e.image=pathToFileURL(source+'/'+e.image).href;
  const out=path.resolve('scene_clustering_dino/.viewer_test');fs.mkdirSync(out,{recursive:true});
  fs.copyFileSync('scene_clustering_dino/viewer.html',out+'/index.html');
  const write=(d,s)=>fs.writeFileSync(out+'/data.js','window.DATA='+JSON.stringify(d)+';window.SUMMARY='+JSON.stringify(s)+';');
  write(data,summary);
  const b=await chromium.launch({channel:'msedge',headless:true}),p=await b.newPage({viewport:{width:1440,height:1000}}),errors=[];
  p.on('pageerror',e=>errors.push(e.message));
  await p.goto(pathToFileURL(out+'/index.html').href);
  for(let i=0;i<data.length;i++){
    await p.locator(`[data-task="${i}"]`).click();
    if(await p.locator('#grid article').count()!==data[i].episode_count)throw Error('DINO regression');
    await p.selectOption('#mode','noise');
    if(await p.locator('#grid article').count()!==data[i].noise_count)throw Error('Noise regression');
  }
  const t=data[0],e=t.episodes.slice(0,4).map((r,i)=>({...r,source:i<2?'dino':i===2?'vlm':'unresolved',
    cluster_id:i<2?'dino:t000:c0':i===2?'vlm:t000_c000':-1,dino_cluster_id:i<2?0:-1,
    vlm_status:i===2?'uncertain':undefined,common_features:'TEST ONLY',center:i===2,medoid:false}));
  const mixed={...t,episodes:e,hybrid:true,episode_count:4,dino_cluster_count:1,vlm_cluster_count:1,
    original_noise_count:2,noise_count:1,noise_ratio:.25,vlm_uncertain_count:1,cluster_count:2,
    clusters:[{cluster_id:'dino:t000:c0',dino_cluster_id:0,source:'dino',size:2},
      {cluster_id:'vlm:t000_c000',source:'vlm',status:'uncertain',size:1,common_features:'TEST ONLY',reason:'MOCK'}]};
  write([mixed],{...summary,hybrid:true,tasks:1,episodes:4,dino_clusters:1,vlm_clusters:1,vlm_uncertain:1,noise:1,vlm_frame_mode:'first_last'});
  await p.reload();
  for(const [mode,count] of [['dino',2],['vlm',1],['uncertain',1],['noise',1],['all',4]]){
    await p.selectOption('#mode',mode);
    if(await p.locator('#grid article').count()!==count)throw Error('Source filter '+mode);
  }
  await p.selectOption('#cluster','vlm:t000_c000');
  if(await p.locator('#grid article').count()!==1||!(await p.locator('#pairs').innerText()).includes('MOCK'))throw Error('Group filter');
  await p.selectOption('#cluster','all');
  await p.waitForFunction(()=>[...document.querySelectorAll('#grid img')].every(im=>im.complete&&im.naturalWidth>0));
  await p.screenshot({path:out+'/desktop.png'});
  await p.setViewportSize({width:390,height:844});
  await p.screenshot({path:out+'/mobile.png'});
  if(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Mobile overflow');
  await b.close();
  if(errors.length)throw Error(errors.join('\n'));
  console.log(JSON.stringify({dino_tasks:data.length,dino_episodes:data.reduce((n,t)=>n+t.episode_count,0),hybrid_fixture:true,errors}));
})().catch(e=>{console.error(e);process.exit(1)});
