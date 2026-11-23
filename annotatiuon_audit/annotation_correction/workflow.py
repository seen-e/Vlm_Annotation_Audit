"""Optional sequential audit -> correction orchestration; never publishes automatically."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import yaml

from ..keyframe_grounded_filter.two_round.run import load_two_round_config
from .correction.io import digest, read, save
from .handoff import select_candidates

ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parents[1]


def resolve_config(path):
    path = Path(path).resolve()
    config = yaml.safe_load(path.read_text(encoding='utf-8'))
    if config.get('audit_backend') != 'text_first_two_round':
        raise ValueError('Only text_first_two_round has a verified correction adapter')
    for key in ('audit_config', 'correction_config', 'output_root'):
        config[key] = str((path.parent / config[key]).resolve())
    return config


def build_correction_config(base, audit, handoff, work):
    config = dict(base)
    config.update(annotations_root=audit['annotations_root'], standards_path=audit['standards_path'],
                  audit_root=audit['output_root'], episode_ids=handoff['selected_episodes'],
                  episode_ids_file=None, handoff_manifest=str(work / 'handoff.json'))
    output = Path(config['output_root']).resolve()
    inputs = [Path(config[k]).resolve() for k in ('annotations_root', 'audit_root', 'video_metadata_root')]
    for source in inputs:
        if output == source or output.is_relative_to(source) or source.is_relative_to(output):
            raise ValueError('Correction output must be separate from every input tree')
    for source in inputs:
        if work == source or work.is_relative_to(source) or source.is_relative_to(work):
            raise ValueError('Workflow output must be separate from every input tree')
    if output == work or output.is_relative_to(work) or work.is_relative_to(output):
        raise ValueError('Workflow and correction outputs must be separate directories')
    return config


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(ROOT / 'config/workflow.yaml'))
    parser.add_argument('--mode', choices=['audit', 'correction', 'all'], required=True)
    parser.add_argument('--dry-run', action='store_true', help='Validate handoff/plan only; no API or frame extraction')
    parser.add_argument('--pilot', action='store_true', help='Forward the existing bounded audit pilot gate')
    args = parser.parse_args(argv)
    cfg = resolve_config(args.config)
    audit = load_two_round_config(cfg['audit_config'])
    base = yaml.safe_load(Path(cfg['correction_config']).read_text(encoding='utf-8'))
    work = Path(cfg['output_root'])
    # Validate output isolation before creating status/config files or calling APIs.
    build_correction_config(base, audit, {'selected_episodes': []}, work)
    audit_cmd = [sys.executable, '-m', 'annotatiuon_audit.keyframe_grounded_filter.two_round.run',
                 '--config', cfg['audit_config']]
    if args.pilot:
        audit_cmd.append('--pilot')
    if args.dry_run and args.mode in ('audit', 'all'):
        print(json.dumps({'mode': args.mode, 'audit_command': audit_cmd,
                          'correction_config': cfg['correction_config'], 'api_calls': 0,
                          'note': 'Correction candidates are determined after audit completes.'}))
        return 0
    if args.dry_run:
        handoff = select_candidates(audit, base['audit_statuses'])
        print(json.dumps({'audited': handoff['audited_episodes'], 'counts': handoff['audit_counts'],
                          'correction_candidates': len(handoff['selected_episodes']), 'api_calls': 0}))
        return 0
    work.mkdir(parents=True, exist_ok=True)
    import fcntl
    with (work / '.workflow.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        identity = {'config': cfg, 'audit_config': digest(audit), 'correction_config': digest(base)}
        identity_path = work / 'identity.json'
        if identity_path.exists() and read(identity_path) != identity:
            raise ValueError('Workflow config changed; use a new output_root')
        save(identity_path, identity)
        def status(phase, **extra):
            save(work / 'status.json', {'phase': phase, 'updated_at': time.time(), **extra})
        try:
            if args.mode in ('audit', 'all'):
                status('auditing')
                subprocess.run(audit_cmd, cwd=REPOSITORY, check=True)
            if args.mode == 'audit':
                status('audit_complete'); return 0
            status('validating_handoff')
            handoff = select_candidates(audit, base['audit_statuses'])
            config = build_correction_config(base, audit, handoff, work)
            path = work / 'handoff.json'
            if path.exists() and read(path) != handoff:
                raise ValueError('Audit results or source annotations changed; use a new workflow output')
            save(path, handoff)
            save(work / 'selected_episodes.json', handoff['selected_episodes'])
            if not handoff['selected_episodes']:
                status('complete', candidates=0, message='No selected non-normal episodes')
                return 0
            generated = work / 'correction_config.yaml'
            generated.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding='utf-8')
            status('correcting', candidates=len(handoff['selected_episodes']),
                   progress_path=str(Path(config['output_root']) / 'progress.json'))
            result = subprocess.run([sys.executable, '-m', 'annotatiuon_audit.annotation_correction',
                                     '--config', str(generated)], cwd=REPOSITORY)
            report = read(Path(config['output_root']) / 'progress.json')
            status('complete' if result.returncode == 0 else 'needs_attention',
                   correction_progress=report, published=False, stage4_rerun=False)
            return result.returncode
        except Exception as exc:
            status('blocked', error=str(exc))
            raise


if __name__ == '__main__':
    raise SystemExit(main())
