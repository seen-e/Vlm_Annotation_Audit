const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

function expand(exportPath, resultRoot) {
  const bytes = fs.readFileSync(exportPath);
  const saved = JSON.parse(bytes.toString('utf8').replace(/^\uFEFF/, ''));
  const tasks = fs.readdirSync(resultRoot).filter(n => /^task_\d+$/.test(n)).sort()
    .map(n => JSON.parse(fs.readFileSync(path.join(resultRoot, n, 'final.json'), 'utf8')));
  const manifest = tasks.map(t => ({task_id: t.task_id, task: t.task,
    groups: t.groups.map(g => ({cluster_id: g.cluster_id, episode_ids: [...g.member_episode_ids].sort()}))}));
  if (saved.version !== 1 || JSON.stringify(saved.dataset_manifest) !== JSON.stringify(manifest)) {
    throw Error('Export manifest does not match the clustering results');
  }
  const taskIds = new Set(tasks.map(t => String(t.task_id)));
  const groupIds = new Set(tasks.flatMap(t => t.groups.map(g => `${t.task_id}/${g.cluster_id}`)));
  for (const [field, allowed] of [['task_standards', taskIds], ['group_standards', groupIds]]) {
    const entries = saved[field];
    if (!entries || typeof entries !== 'object' || Array.isArray(entries)) throw Error(`Invalid ${field}`);
    for (const [key, value] of Object.entries(entries)) {
      if (!allowed.has(key) || typeof value !== 'string') throw Error(`Invalid standard: ${field}/${key}`);
    }
  }
  const episodes = {}, missing = [];
  let groupEpisodes = 0, taskEpisodes = 0, groupCount = 0;
  for (const t of tasks) {
    const ids = new Set();
    for (const g of t.groups) {
      groupCount++;
      const own = saved.group_standards[`${t.task_id}/${g.cluster_id}`] ?? '';
      const inherited = saved.task_standards[String(t.task_id)] ?? '';
      const source = own.trim() ? 'group' : 'task';
      const standard = source === 'group' ? own : inherited;
      if (!standard.trim()) missing.push({task_id: t.task_id, cluster_id: g.cluster_id, episodes: g.member_episode_ids.length});
      for (const id of g.member_episode_ids) {
        if (Object.hasOwn(episodes, id)) throw Error(`Duplicate episode: ${id}`);
        ids.add(id);
        episodes[id] = {task_id: t.task_id, task: t.task, cluster_id: g.cluster_id,
          standard_source: source, review_standard: standard};
        if (source === 'group') groupEpisodes++; else taskEpisodes++;
      }
    }
    if (ids.size !== t.episode_count || t.assignments.length !== ids.size) throw Error(`Task coverage mismatch: ${t.task_id}`);
    for (const a of t.assignments) if (!ids.has(a.episode_id)) throw Error(`Missing assignment: ${a.episode_id}`);
  }
  if (missing.length) throw Error('Groups without an effective standard: ' + JSON.stringify(missing));
  const summary = JSON.parse(fs.readFileSync(path.join(resultRoot, 'summary.json'), 'utf8'));
  if (Object.keys(episodes).length !== summary.episodes) throw Error('Total episode coverage mismatch');
  return {schema_version: 1, generated_at: new Date().toISOString(),
    source_export: path.basename(exportPath), source_export_sha256: crypto.createHash('sha256').update(bytes).digest('hex'),
    standards_updated_at: saved.updated_at, precedence: 'nonempty_group_standard_else_task_standard',
    task_count: tasks.length, group_count: groupCount, episode_count: Object.keys(episodes).length,
    group_standard_episode_count: groupEpisodes, task_standard_episode_count: taskEpisodes,
    episodes: Object.fromEntries(Object.entries(episodes).sort(([a], [b]) => a.localeCompare(b)))};
}

if (require.main === module) {
  const [exportPath, resultRoot, outputPath] = process.argv.slice(2);
  if (!exportPath || !resultRoot || !outputPath) throw Error('Usage: node expand_review_standards.cjs EXPORT_JSON RESULT_ROOT OUTPUT_JSON');
  const result = expand(exportPath, resultRoot);
  fs.writeFileSync(outputPath, JSON.stringify(result, null, 2) + '\n', {flag: 'wx'});
  console.log(JSON.stringify({output: outputPath, episodes: result.episode_count,
    tasks: result.task_count, groups: result.group_count, group_standards: result.group_standard_episode_count,
    task_standards: result.task_standard_episode_count}));
}
module.exports = {expand};
