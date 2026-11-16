import base64
import hashlib
import json
from pathlib import Path

from ..episode_level_filter.client import credentials


def messages(prompt, payload, images, detail):
    content = [{'type': 'text', 'text': json.dumps(payload, ensure_ascii=False)}]
    for image in images:
        label = {'frame_ref': image['frame_ref'], 'frame_index': image['frame_index']}
        content.append({'type': 'text', 'text': 'VISUAL_FRAME ' + json.dumps(label, ensure_ascii=False)})
        raw = Path(image['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != image['sha256']:
            raise ValueError('Image changed after preflight: ' + image['frame_ref'])
        encoded = base64.b64encode(raw).decode('ascii')
        content.append({'type': 'image_url', 'image_url': {
            'url': f'data:{image["mime_type"]};base64,{encoded}', 'detail': detail}})
    return [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': content}]


def complete(prompt, payload, images, cfg, auth):
    from openai import OpenAI
    request_messages = messages(prompt, payload, images, cfg['image_detail'])
    kwargs = dict(model=auth['model'], messages=request_messages, temperature=cfg['temperature'],
                  max_tokens=cfg['max_tokens'], stream=cfg['stream'])
    if cfg['thinking'] is not None:
        kwargs['extra_body'] = {'chat_template_kwargs': {'enable_thinking': cfg['thinking']}}
    with OpenAI(api_key=auth['api_key'], base_url=auth['base_url'],
                timeout=cfg['timeout_sec'], max_retries=0) as client:
        response = client.chat.completions.create(**kwargs)
        if not cfg['stream']:
            return response.choices[0].message.content or '', response.choices[0].finish_reason
        parts, finish = [], None
        with response:
            for chunk in response:
                if chunk.choices:
                    choice = chunk.choices[0]
                    if choice.delta.content:
                        parts.append(choice.delta.content)
                    if choice.finish_reason:
                        finish = choice.finish_reason
        return ''.join(parts), finish


__all__ = ['complete', 'credentials', 'messages']
