const fs=require('fs'),path=require('path');
const root=process.argv[2],images=process.argv[3],out=path.join(root,'viewer');
fs.mkdirSync(path.join(out,'images'),{recursive:true});
const tasks=[];
for(const name of fs.readdirSync(root).filter(n=>/^task_\d+$/.test(n)).sort()){
 const folder=path.join(root,name),t=JSON.parse(fs.readFileSync(path.join(folder,'final.json')));
 t.candidates=JSON.parse(fs.readFileSync(path.join(folder,'round1_candidates.json')));t.episodes={};
 for(const a of t.assignments){const c=t.candidates.find(c=>c.group_id===a.round1_group_id);t.episodes[a.episode_id]={...a,representative:c.representative_episode_ids.includes(a.episode_id)};
 const src=path.join(images,a.episode_id+'.jpg'),dest=path.join(out,'images',a.episode_id+'.jpg');
 if(!fs.existsSync(src))throw Error('Missing cached preview '+src);
 if(!fs.existsSync(dest)){try{fs.linkSync(src,dest)}catch{fs.copyFileSync(src,dest)}}}
 tasks.push(t);
}
const summary=JSON.parse(fs.readFileSync(path.join(root,'summary.json')));
if(tasks.reduce((n,t)=>n+t.episode_count,0)!==summary.episodes)throw Error('Coverage mismatch');
fs.writeFileSync(path.join(out,'data.js'),'const DATA='+JSON.stringify({summary,tasks})+';');
fs.copyFileSync(path.join(__dirname,'quick_viewer.html'),path.join(out,'index.html'));
fs.copyFileSync(path.join(__dirname,'review_standards.js'),path.join(out,'review_standards.js'));
console.log(JSON.stringify({tasks:tasks.length,episodes:summary.episodes,groups:summary.groups,page:path.join(out,'index.html')}));
