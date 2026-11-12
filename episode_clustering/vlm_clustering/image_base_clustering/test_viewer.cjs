const { chromium } = require('C:/Users/34927/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs = require('fs');
const {pathToFileURL} = require('url');
(async()=>{
  const root=process.argv[2],errors=[];
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const p=await browser.newPage({viewport:{width:1440,height:1000}});
  p.on('pageerror',e=>errors.push(e.message));
  await p.goto(pathToFileURL(root+'/index.html').href);
  const data=await p.evaluate(()=>DATA);
  if(data.summary.episodes!==data.tasks.reduce((n,t)=>n+t.episode_count,0))throw Error('Unexpected total');
  const seen=new Set();
  for(let ti=0;ti<data.tasks.length;ti++){
  if(data.tasks.length>1)await p.selectOption('#task-select',String(ti));
  for(let i=0;i<data.tasks[ti].groups.length;i++){
    await p.locator(`[data-group="${i}"]`).click();
    const g=data.tasks[ti].groups[i];
    if(await p.locator('#grid article').count()!==g.member_episode_ids.length)throw Error('Group count');
    await p.waitForFunction(()=>Array.from(document.querySelectorAll('#grid img')).every(im=>im.complete&&im.naturalWidth>0));
    for(const eid of g.member_episode_ids){
      if(seen.has(eid))throw Error('Duplicate member');seen.add(eid);
      for(const role of ['first','last'])if(!fs.existsSync(root+'/'+data.tasks[ti].episodes[eid][role].image))throw Error('Missing image');
    }
    await p.locator('#reps').check();
    if(await p.locator('#grid article').count()!==g.member_episode_ids.filter(eid=>data.tasks[ti].episodes[eid].representative).length)throw Error('Representative filter');
    await p.locator('#reps').uncheck();
  }
  }
  if(seen.size!==data.summary.episodes)throw Error('Coverage');
  if(data.tasks.length>1)await p.selectOption('#task-select','0');
  await p.locator('[data-group="0"]').click();
  const eid=data.tasks[0].groups[0].member_episode_ids[0];
  await p.locator('#search').fill(eid);
  if(await p.locator('#grid article').count()!==1)throw Error('Search');
  await p.locator('#grid .frame').first().click();
  if(!await p.locator('#detail').isVisible())throw Error('Detail');
  await p.waitForFunction(()=>Array.from(document.querySelectorAll('#detail img')).every(im=>im.complete&&im.naturalWidth>0));
  await p.screenshot({path:root+'/detail.png'});
  await p.locator('#close').click();
  await p.locator('#search').fill('');
  await p.screenshot({path:root+'/desktop.png'});
  await p.setViewportSize({width:390,height:844});
  await p.screenshot({path:root+'/mobile.png'});
  if(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Horizontal overflow');
  if(errors.length)throw Error(errors.join('\n'));
  await browser.close();console.log(JSON.stringify({episodes:seen.size,tasks:data.tasks.length,groups:data.tasks.reduce((n,t)=>n+t.groups.length,0),images:seen.size*2,errors}));
})().catch(e=>{console.error(e);process.exit(1)});
