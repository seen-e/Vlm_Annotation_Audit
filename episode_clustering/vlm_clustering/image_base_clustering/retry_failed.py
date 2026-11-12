"""Resume failed tasks with isolated request overrides and explicit provenance."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import time

import yaml

from .api import credentials, request, save
from .run import load_tasks, run_task


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root', type=Path)
    ap.add_argument('--worker', action='store_true')
    args = ap.parse_args()
    root = args.root.resolve()
    if not args.worker:
        with (root / 'recovery.log').open('a') as log:
            p = subprocess.Popen([sys.executable, '-u', '-m', 'episode_clustering.vlm_clustering.image_base_clustering.retry_failed', str(root), '--worker'],
                                 cwd=Path(__file__).resolve().parents[3], stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        save(root / 'recovery_launch.json', dict(pid=p.pid, started_at=time.time(), max_tokens=32768))
        print(json.dumps(dict(pid=p.pid, recovery_log=str(root/'recovery.log'))))
        return
    cfg = yaml.safe_load((root / 'run_config.yaml').read_text())
    auth = credentials(cfg['api'])
    failures = json.loads((root / 'failures.json').read_text())
    if not failures:
        print('No failed tasks');return
    started = time.time()
    recovery = root / time.strftime('recovery_%Y%m%d_%H%M%S')
    recovery.mkdir()
    for name in ('summary.json', 'failures.json', 'progress.json'):
        save(recovery/name, json.loads((root/name).read_text()))
    original_summary = json.loads((root/'summary.json').read_text())
    cfg['task_ids'] = [f['task_id'] for f in failures]
    tasks = load_tasks(cfg)
    overrides = []

    def caller(folder, prompt, payload, images, config, credentials_, validator):
        folder = Path(folder)
        if (folder/'output.json').exists():
            return request(folder, prompt, payload, images, config, credentials_, validator)
        adjusted = deepcopy(config)
        adjusted['api']['max_tokens'] = 32768
        destination = recovery / folder.relative_to(root)
        result = request(destination, prompt, payload, images, adjusted, credentials_, validator)
        link = dict(original_request=str(folder), actual_request=str(destination), max_tokens=32768)
        save(folder/'recovery.json', link)
        overrides.append(link)
        save(recovery/'requests.json', overrides)
        return result

    remaining = []
    for tid, items in tasks.items():
        try:
            run_task(tid, items, cfg, root, auth, caller=caller)
        except Exception as exc:
            remaining.append(dict(task_id=tid, error=str(exc).replace(auth['api_key'], '[REDACTED]')))
        save(root/'recovery_status.json', dict(failed_tasks=remaining, processed_task=tid,
                                              elapsed_sec=time.time()-started, finished=False))
    finals = [json.loads(p.read_text()) for p in sorted(root.glob('task_*/final.json'))]
    summary = dict(original_summary, status='complete_with_failures' if remaining else 'complete',
                   completed_tasks=len(finals), failed_tasks=len(remaining),
                   episodes=sum(t['episode_count'] for t in finals), groups=sum(t['final_group_count'] for t in finals),
                   original_elapsed_sec=original_summary['elapsed_sec'], recovery_elapsed_sec=time.time()-started,
                   elapsed_sec=original_summary['elapsed_sec']+time.time()-started, recovery_directory=str(recovery))
    save(root/'assignments.json', [a for t in finals for a in t['assignments']])
    save(root/'failures.json', remaining)
    save(root/'summary.json', summary)
    save(root/'progress.json', dict(total_tasks=summary['tasks'], completed_tasks=len(finals), failed_tasks=len(remaining), elapsed_sec=summary['elapsed_sec']))
    save(root/'recovery_status.json', dict(failed_tasks=remaining, elapsed_sec=time.time()-started, finished=True))
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
