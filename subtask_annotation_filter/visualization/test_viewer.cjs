const { chromium } = require('C:/Users/34927/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');
const assert = require('assert');
(async()=>{
 const dir=process.argv[2];
 const data=JSON.parse(fs.readFileSync(path.join(dir,'selection.json'),'utf8'));
 const groups={};for(const e of data.episodes){groups[e.task_id]=(groups[e.task_id]||0)+1;assert.equal(e.review.review_status,'anomaly');for(const issue of e.review.issues)for(const ev of issue.evidence){let val=e.annotation;for(const key of ev.path.split('/').slice(1))val=val[key.replace(/~1/g,'/').replace(/~0/g,'~')];assert.equal(typeof val,'string');assert(val.includes(ev.quote));}for(const b of e.bounds)assert(b.start!==null&&b.end>b.start&&b.end<=e.duration);}
 assert(Object.values(groups).every(n=>n<=2));
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1000}});
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(pathToFileURL(path.join(dir,'index.html')).href);
 assert.equal(await page.locator('.episode').count(),data.episodes.length);
 for(const e of data.episodes){
   await page.locator(`.episode[data-id="${e.episode_id}"]`).click();
   await page.waitForFunction(()=>document.getElementById('video').readyState>=2);
   const m=await page.locator('video').evaluate(v=>({w:v.videoWidth,h:v.videoHeight,t:v.duration}));
   assert.equal(m.w,640);assert.equal(m.h,480);assert(Math.abs(m.t-e.duration)<0.15);
 }
 await page.locator('.episode').first().click();await page.waitForFunction(()=>document.getElementById('video').readyState>=2);
 await page.locator('#subtasks [data-jump]').first().click();
 const end=data.episodes[0].bounds[0].end;
 await page.locator('video').evaluate((v,t)=>{v.currentTime=t+0.1;v.dispatchEvent(new Event('timeupdate'))},end);
 assert(await page.locator('video').evaluate(v=>v.paused));
 assert(Math.abs(await page.locator('video').evaluate(v=>v.currentTime)-end)<0.1);
 await page.locator('#human').selectOption('false_positive');await page.locator('#save').click();await page.reload();assert.equal(await page.locator('#human').inputValue(),'false_positive');
 await page.waitForFunction(()=>document.getElementById('video').readyState>=2);
 await page.screenshot({path:path.join(dir,'qa-desktop.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:path.join(dir,'qa-mobile.png'),fullPage:true});
 assert.deepEqual(errors,[]);await browser.close();
 console.log(JSON.stringify({episodes:data.episodes.length,tasks:Object.keys(groups).length,max_per_task:Math.max(...Object.values(groups)),video_loads:'all passed',evidence:'all exact',range_playback:'passed',manual_save:'passed',errors}));
})().catch(e=>{console.error(e);process.exit(1)});
