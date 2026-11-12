const fs = require('node:fs');
const path = require('node:path');

function build(cfg) {
  const root = path.resolve(cfg.results_root), images = path.resolve(cfg.images_root);
  const out = path.resolve(cfg.output_root), viewer = path.join(out, 'viewer');
  const summary = JSON.parse(fs.readFileSync(path.join(root, 'summary.json'), 'utf8'));
  const names = fs.readdirSync(root).filter(n => /^task_\d+$/.test(n)).sort();
  const tasks = [], allIds = new Set();
  for (const name of names) {
    const folder = path.join(root, name);
    const task = JSON.parse(fs.readFileSync(path.join(folder, 'final.json'), 'utf8'));
    task.candidates = JSON.parse(fs.readFileSync(path.join(folder, 'round1_candidates.json'), 'utf8'));
    task.episodes = {};
    const candidates = new Map(task.candidates.map(c => [c.group_id, c]));
    const grouped = task.groups.flatMap(g => g.member_episode_ids);
    if (new Set(grouped).size !== grouped.length || grouped.length !== task.episode_count) throw Error(`Invalid groups: ${name}`);
    const remaining = new Set(grouped);
    for (const a of task.assignments) {
      const c = candidates.get(a.round1_group_id);
      if (!c || !remaining.delete(a.episode_id) || allIds.has(a.episode_id)) throw Error(`Invalid assignment: ${a.episode_id}`);
      if (!/^[a-zA-Z0-9_-]+$/.test(a.episode_id)) throw Error('Invalid episode ID');
      if (!fs.existsSync(path.join(images, a.episode_id + '.jpg'))) throw Error(`Missing paired preview: ${a.episode_id}`);
      allIds.add(a.episode_id);
      task.episodes[a.episode_id] = {...a, representative: c.representative_episode_ids.includes(a.episode_id)};
    }
    if (remaining.size) throw Error(`Missing assignments: ${name}`);
    tasks.push(task);
  }
  const groups = tasks.reduce((n, t) => n + t.groups.length, 0);
  if (allIds.size !== summary.episodes || groups !== summary.groups || tasks.length !== summary.completed_tasks) throw Error('Summary coverage mismatch');
  fs.mkdirSync(path.join(viewer, 'images'), {recursive: true});
  function copy(src, dest) {
    if (path.resolve(src) !== path.resolve(dest)) fs.copyFileSync(src, dest);
  }
  copy(path.join(root, 'summary.json'), path.join(out, 'summary.json'));
  for (const name of names) {
    fs.mkdirSync(path.join(out, name), {recursive: true});
    for (const file of ['final.json', 'round1_candidates.json']) copy(path.join(root, name, file), path.join(out, name, file));
  }
  for (const id of allIds) {
    const src = path.join(images, id + '.jpg'), dest = path.join(viewer, 'images', id + '.jpg');
    if (path.resolve(src) === path.resolve(dest)) continue;
    if (fs.existsSync(dest)) fs.copyFileSync(src, dest);
    else {try {fs.linkSync(src, dest);} catch {fs.copyFileSync(src, dest);}}
  }
  fs.writeFileSync(path.join(viewer, 'data.js'), 'const DATA=' + JSON.stringify({summary, tasks}) + ';');
  copy(path.join(__dirname, 'quick_viewer.html'), path.join(viewer, 'index.html'));
  copy(path.join(__dirname, 'review_standards.js'), path.join(viewer, 'review_standards.js'));
  return {tasks: tasks.length, groups, episodes: allIds.size, page: path.join(viewer, 'index.html')};
}

if (require.main === module) {
  const file = path.resolve(process.argv[2] || path.join(__dirname, 'config.json'));
  const cfg = JSON.parse(fs.readFileSync(file, 'utf8'));
  for (const key of ['results_root', 'images_root', 'output_root']) cfg[key] = path.resolve(path.dirname(file), cfg[key]);
  console.log(JSON.stringify(build(cfg)));
}
module.exports = {build};
