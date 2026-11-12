"""Build a portable, read-only viewer from actual two-round outputs."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil

from PIL import Image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('run_root', type=Path)
    parser.add_argument('--cache-root', type=Path, default=Path('/mnt/data/chachaxu/embedding_cluster/robodojo'))
    parser.add_argument('--completed-only', action='store_true')
    args = parser.parse_args()
    root = args.run_root
    summary = json.loads((root / 'summary.json').read_text())
    if summary['status'] != 'complete' and not args.completed_only:
        raise ValueError('This viewer requires a completed experiment')
    out = root / 'viewer'
    (out / 'images').mkdir(parents=True, exist_ok=True)
    (out / 'raw').mkdir(exist_ok=True)
    tasks = []
    for folder in sorted(root.glob('task_*')):
        if not (folder/'final.json').is_file():
            if args.completed_only:
                continue
            raise ValueError('Missing final result: '+str(folder))
        final = json.loads((folder / 'final.json').read_text())
        candidates = json.loads((folder / 'round1_candidates.json').read_text())
        lookup = {g['group_id']: g for g in candidates}
        expected = {a['episode_id'] for a in final['assignments']}
        members = [eid for g in final['groups'] for eid in g['member_episode_ids']]
        if len(members) != len(expected) or set(members) != expected:
            raise ValueError('Invalid member coverage')
        final['candidates'] = candidates
        final['episodes'] = {}
        sent = {}
        for p in sorted(folder.glob('round1/*/input.json')):
            for image in json.loads(p.read_text())['images']:
                key = (image['episode_id'], image['frame_role'])
                if key in sent:
                    raise ValueError('Duplicate request image')
                sent[key] = image

        def export_episode(assignment):
            eid = assignment['episode_id']
            if Path(eid).name != eid:
                raise ValueError('Invalid episode identifier')
            source = args.cache_root / eid
            metadata = None if all((eid,r) in sent for r in ('first','last')) else json.loads((source/'metadata.json').read_text())
            if metadata is not None and metadata['source']['episode_id'] != eid:
                raise ValueError('Mismatched image metadata')
            entry = dict(assignment, representative=eid in lookup[assignment['round1_group_id']]['representative_episode_ids'])
            for role in ('first', 'last'):
                relative = 'images/' + eid + '_' + role + '.jpg'
                if (eid,role) in sent:
                    image = sent[eid,role]
                    image_path = Path(image['sent_path'])
                    if hashlib.sha256(image_path.read_bytes()).hexdigest() != image['sent_sha256']:
                        raise ValueError('Sent image hash mismatch')
                    destination = out / relative
                    if not destination.exists():
                        try:
                            os.link(image_path, destination)
                        except OSError:
                            shutil.copyfile(image_path, destination)
                    elif hashlib.sha256(destination.read_bytes()).hexdigest() != image['sent_sha256']:
                        # Replace this viewer copy only; never modify a linked source image.
                        destination.unlink()
                        shutil.copyfile(image_path, destination)
                    entry[role] = dict(image=relative, timestamp_sec=image['timestamp_sec'], frame_index=image['episode_frame_index'])
                    continue
                frame = next(f for f in metadata['frames'] if f['file'] == role + '.png')
                path = source / frame['file']
                if hashlib.sha256(path.read_bytes()).hexdigest() != frame['sha256']:
                    raise ValueError('Image hash mismatch: ' + str(path))
                with Image.open(path) as im:
                    im.convert('RGB').save(out / relative, quality=93)
                entry[role] = dict(image=relative, timestamp_sec=frame['timestamp_sec'], frame_index=frame['episode_frame_index'])
            return eid, entry
        with ThreadPoolExecutor(max_workers=16) as pool:
            final['episodes'] = dict(pool.map(export_episode, final['assignments']))
        final['raw_final'] = 'raw/' + folder.name + '_final.json'
        final['raw_round1'] = 'raw/' + folder.name + '_round1.json'
        shutil.copyfile(folder / 'final.json', out / final['raw_final'])
        shutil.copyfile(folder / 'round1_candidates.json', out / final['raw_round1'])
        tasks.append(final)
        print(json.dumps(dict(task_id=final['task_id'], episodes=final['episode_count'])),flush=True)
    if not tasks:
        raise ValueError('No completed tasks available')
    plan = json.loads((root/'plan.json').read_text()) if (root/'plan.json').exists() else []
    pending = sorted({t['task_id'] for t in plan}-{t['task_id'] for t in tasks})
    view_summary = dict(summary, episodes=sum(t['episode_count'] for t in tasks),
                        completed_tasks=len(tasks), groups=sum(t['final_group_count'] for t in tasks),
                        pending_task_ids=pending, status='partial' if pending else 'complete')
    cfg_path=root/'config_snapshot.json'
    cfg=json.loads(cfg_path.read_text()) if cfg_path.exists() else dict(frame_mode='first_last',episodes_per_batch=50)
    data = dict(summary=view_summary, tasks=tasks, config=dict(frame_mode=cfg['frame_mode'], episodes_per_batch=cfg['episodes_per_batch']))
    (out / 'data.js').write_text('const DATA = ' + json.dumps(data, ensure_ascii=False) + ';\n', encoding='utf-8')
    shutil.copyfile(Path(__file__).with_name('viewer.html'), out / 'index.html')
    print(json.dumps(dict(viewer=str(out), episodes=sum(t['episode_count'] for t in tasks), images=2*sum(t['episode_count'] for t in tasks))))


if __name__ == '__main__':
    main()
