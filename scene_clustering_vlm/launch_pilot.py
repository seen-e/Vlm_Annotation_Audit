"""Launch an explicit, isolated API experiment; never run on package import."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

import yaml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('credentials_source', type=Path)
    parser.add_argument('--all-tasks', action='store_true')
    parser.add_argument('--task-workers', type=int, default=1)
    args = parser.parse_args()
    if args.task_workers < 1:
        raise ValueError('task_workers must be positive')
    root = Path('/mnt/workspace/EmbeddingCluster_Exp')
    source = args.credentials_source
    raw = yaml.safe_load(source.read_text(encoding='utf-8'))
    matches = []

    def visit(value):
        if isinstance(value, dict):
            if 'xb4v' in str(value.get('base_url', '')) and value.get('api_key'):
                matches.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(raw)
    if not matches or len({(m['base_url'], m['api_key']) for m in matches}) != 1:
        raise ValueError('Expected exactly one distinct xb4v credential pair')
    entry = matches[0]
    model = entry.get('model') or entry.get('model_name') or entry.get('name')
    if not isinstance(model, str) or not model:
        raise ValueError('No explicit model name in xb4v configuration')
    auth = {k: entry[k] for k in ('base_url', 'api_key')}
    auth['model'] = model
    private = root / '.credentials'
    private.mkdir(mode=0o700, exist_ok=True)
    credentials = private / 'xb4v.yaml'
    credentials.write_text(yaml.safe_dump(auth), encoding='utf-8')
    credentials.chmod(0o600)
    cfg = yaml.safe_load((root / 'scene_clustering_vlm/config.yaml').read_text())
    label = 'full3500' if args.all_tasks else 'pilot100'
    out = Path('/mnt/data/chachaxu/embedding_cluster/robodojo_vlm_experiments') / time.strftime('first_last_batch50_xb4v_' + label + '_%Y%m%d_%H%M%S')
    out.mkdir(parents=True, exist_ok=False)
    cfg.update(output_root=str(out), frame_mode='first_last', episodes_per_batch=50,
               task_ids=[] if args.all_tasks else [0], task_workers=args.task_workers)
    cfg['api']['credentials_file'] = str(credentials)
    path = out / 'run_config.yaml'
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding='utf-8')
    command = [sys.executable, '-u', '-m', 'scene_clustering_vlm.run', '--config', str(path)]
    # The runner validates all metadata and saves the full plan before API requests.
    if not args.all_tasks:
        subprocess.run(command + ['--dry-run'], cwd=root, check=True)
    with (out / 'run.log').open('a') as log:
        process = subprocess.Popen(command, cwd=root, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    info = dict(pid=process.pid, output=str(out), endpoint_tag='xb4v', model=model,
                frame_mode='first_last', episodes_per_batch=50, task_ids=cfg['task_ids'],
                task_workers=cfg['task_workers'],
                started_at=time.time())
    (out / 'launch.json').write_text(json.dumps(info, indent=2), encoding='utf-8')
    print(json.dumps(info))


if __name__ == '__main__':
    main()
