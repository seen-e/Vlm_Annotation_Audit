"""Run image grouping followed by text refinement, or either step independently."""
import argparse
from pathlib import Path
import subprocess
import sys

import yaml


def commands(config, stage, dry_run=False):
    config = Path(config).resolve()
    settings = yaml.safe_load(config.read_text(encoding='utf-8'))
    image_config = (config.parent / settings['image_config']).resolve()
    text_config = (config.parent / settings['text_config']).resolve()
    prefix = 'episode_clustering.vlm_clustering.'
    result = []
    if stage in ('image', 'all'):
        result.append([sys.executable, '-m', prefix + 'image_base_clustering.run', '--config', str(image_config)])
    if stage in ('text', 'all'):
        command = [sys.executable, '-m', prefix + 'txt_base_clustering.run', '--config', str(text_config)]
        if stage == 'all':
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
    parser.add_argument('--stage', choices=('image', 'text', 'all'), default='all')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    # Module execution must not depend on the caller's working directory.
    root = Path(__file__).resolve().parents[2]
    for command in commands(args.config, args.stage, args.dry_run):
        if args.dry_run and args.stage == 'all' and '--image-results-root' in command:
            image_output = Path(command[command.index('--image-results-root') + 1])
            if not (image_output / 'summary.json').exists():
                print('Text preflight pending: finish the image stage first. No API called.')
                continue
        result = subprocess.run(command, cwd=root, check=False)
        if result.returncode:
            raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
