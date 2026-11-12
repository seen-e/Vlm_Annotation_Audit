"""Report experiment status without displaying secrets or image payloads."""
import json
import os
from pathlib import Path
import sys
import time


root = Path(sys.argv[1])
launch = json.loads((root / 'launch.json').read_text())
try:
    os.kill(launch['pid'], 0)
    alive = True
except ProcessLookupError:
    alive = False
result = dict(output=str(root), alive=alive, elapsed_sec=round(time.time()-launch['started_at'], 1))
for name in ('progress', 'summary', 'failures'):
    path = root / (name + '.json')
    if path.exists():
        result[name] = json.loads(path.read_text())
result['requests'] = []
for path in sorted(root.glob('task_*/round*/**/input.json')):
    data = json.loads(path.read_text())
    item = dict(path=str(path.parent.relative_to(root)), images=len(data['images']),
                complete=(path.parent/'output.json').exists(),
                attempts=len(list(path.parent.glob('attempt_*'))))
    errors = sorted(path.parent.glob('attempt_*/error.json'))
    if errors:
        item['last_error'] = json.loads(errors[-1].read_text())
    result['requests'].append(item)
print(json.dumps(result, ensure_ascii=False, indent=2))
