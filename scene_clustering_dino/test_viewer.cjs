const { chromium } = require('C:/Users/34927/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs = require('fs');
(async () => {
  const root = process.argv[2], errors = [];
  const b = await chromium.launch({ channel: 'msedge', headless: true });
  const p = await b.newPage({ viewport: { width: 1440, height: 1000 } });
  p.on('pageerror', e => errors.push(e.message));
  await p.goto('file:///' + root + '/index.html');
  const tasks = await p.evaluate(() => DATA);
  if (tasks.length !== 35 || tasks.reduce((n,t)=>n+t.episodes.length,0)!==3500) throw Error('Coverage');
  for (let i=0;i<tasks.length;i++) {
    for(const r of tasks[i].episodes) if(!fs.existsSync(root+'/'+r.image)) throw Error('Missing image');
    await p.locator(`[data-task="${i}"]`).click();
    await p.waitForFunction(()=>Array.from(document.querySelectorAll('#grid img')).slice(0,2).every(im=>im.complete&&im.naturalWidth===640));
    if(await p.locator('#grid article').count()!==tasks[i].episode_count) throw Error('Task cards');
    await p.selectOption('#mode','noise');
    if(await p.locator('#grid article').count()!==tasks[i].noise_count) throw Error('Noise filter');
    await p.selectOption('#mode','center');
    if(await p.locator('#grid article').count()!==tasks[i].episodes.filter(r=>r.center).length) throw Error('Center filter');
  }
  const multi=tasks.findIndex(t=>t.cross_pairs.length>0);
  await p.locator(`[data-task="${multi<0?0:multi}"]`).click();
  await p.waitForFunction(()=>Array.from(document.querySelectorAll('#grid img')).slice(0,2).every(im=>im.complete&&im.naturalWidth===640));
  await p.screenshot({path:root+'/desktop.png'});
  await p.selectOption('#mode','cross');
  if(multi>=0&&!await p.locator('#pairs div').count()) throw Error('Cross-cluster pairs missing');
  await p.setViewportSize({width:390,height:844});
  await p.screenshot({path:root+'/mobile.png',fullPage:true});
  if(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth)) throw Error('Mobile overflow');
  await b.close();
  console.log(JSON.stringify({tasks:tasks.length,episodes:3500,errors}));
  if(errors.length) process.exitCode=1;
})().catch(e=>{console.error(e);process.exit(1)});
