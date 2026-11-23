import hashlib
import json
import os
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False).encode()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def source_digest(root):
    files = {str(p.relative_to(root)): p.read_text(encoding="utf-8")
                   for folder in ("correction", "prompts", "post_correction_review")
                   for p in sorted((root / folder).glob("*.py"))}
    files["run.py"] = (root / "run.py").read_text(encoding="utf-8")
    return digest(files)
