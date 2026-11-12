import os
from pathlib import Path

import yaml


def credentials(cfg):
    raw = {}
    if cfg.get('credentials_file'):
        raw = yaml.safe_load(Path(cfg['credentials_file']).read_text(encoding='utf-8'))
        if not isinstance(raw, dict):
            raise ValueError('Invalid credentials file')
        if isinstance(raw.get('model'), dict):
            raw = raw['model']
    auth = {
        'base_url': cfg.get('base_url') or raw.get('base_url') or os.environ.get(cfg.get('base_url_env', ''), ''),
        'api_key': os.environ.get(cfg.get('api_key_env', ''), '') or raw.get('api_key'),
        'model': cfg.get('name') or raw.get('model') or os.environ.get(cfg.get('model_env', ''), ''),
    }
    if any(not isinstance(v, str) or not v.strip() for v in auth.values()):
        raise ValueError('Configure base URL, API key and model name; no implicit API fallback')
    return auth


def complete(messages, cfg, auth):
    from openai import OpenAI
    kwargs = dict(model=auth['model'], messages=messages, temperature=cfg['temperature'],
                  max_tokens=cfg['max_tokens'], stream=cfg['stream'])
    if cfg['thinking'] is not None:
        kwargs['extra_body'] = {'chat_template_kwargs': {'enable_thinking': cfg['thinking']}}
    with OpenAI(api_key=auth['api_key'], base_url=auth['base_url'],
                timeout=cfg['timeout_sec'], max_retries=0) as client:
        response = client.chat.completions.create(**kwargs)
        if not cfg['stream']:
            return response.choices[0].message.content or '', response.choices[0].finish_reason
        content, finish = [], None
        with response:
            for chunk in response:
                if chunk.choices:
                    choice = chunk.choices[0]
                    if choice.delta.content:
                        content.append(choice.delta.content)
                    if choice.finish_reason:
                        finish = choice.finish_reason
        return ''.join(content), finish
