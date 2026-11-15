"""Run scene-only, text-only, or progressive scene-then-text clustering."""
import argparse
from pathlib import Path
import subprocess
import sys

import yaml


ALIASES = {'image': 'scene_only', 'text': 'scene_text_step', 'all': 'scene_then_text'}


def commands(config, mode, dry_run=False):
    config = Path(config).resolve()
    settings = yaml.safe_load(config.read_text(encoding='utf-8'))
    image_config = (config.parent / settings['image_config']).resolve()
    text_config = (config.parent / settings['text_config']).resolve()
    prefix = 'episode_clustering.vlm_clustering.'
    mode = ALIASES.get(mode, mode)
    if mode not in ('scene_only', 'text_only', 'scene_then_text', 'scene_text_step'):
        raise ValueError('Invalid clustering mode: ' + str(mode))
    result = []
    if mode in ('scene_only', 'scene_then_text'):
        result.append([sys.executable, '-m', prefix + 'image_base_clustering.run', '--config', str(image_config)])
    if mode in ('text_only', 'scene_then_text', 'scene_text_step'):
        input_mode = 'task_scope' if mode == 'text_only' else 'scene_groups'
        command = [sys.executable, '-m', prefix + 'txt_base_clustering.run', '--config', str(text_config),
                   '--input-mode', input_mode]
        if mode == 'scene_then_text':
            image = yaml.safe_load(image_config.read_text(encoding='utf-8'))
            output = (image_config.parent / image['output_root']).resolve()
            command.extend(['--image-results-root', str(output)])
        result.append(command)
    if dry_run:
        for command in result:
            command.append('--dry-run')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(Path(__file__).with_name('config.yaml')))
    parser.add_argument('--mode', choices=('scene_only', 'text_only', 'scene_then_text'))
    parser.add_argument('--stage', choices=('image', 'text', 'all'), help='Deprecated compatibility alias')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    # Module execution must not depend on the caller's working directory.
    root = Path(__file__).resolve().parents[2]
    settings = yaml.safe_load(Path(args.config).resolve().read_text(encoding='utf-8'))
    mode = args.mode or args.stage or settings.get('default_mode', 'scene_then_text')
    resolved_mode = ALIASES.get(mode, mode)
    for command in commands(args.config, mode, args.dry_run):
        if args.dry_run and resolved_mode == 'scene_then_text' and '--image-results-root' in command:
            image_output = Path(command[command.index('--image-results-root') + 1])
            if not (image_output / 'summary.json').exists():
                print('Text preflight pending: finish the image stage first. No API called.')
                continue
        result = subprocess.run(command, cwd=root, check=False)
        if result.returncode:
            raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
