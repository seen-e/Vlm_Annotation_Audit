import base64
import hashlib
import json
import os
import time
from pathlib import Path

import yaml

from .io import digest, read, save
from .schema import require


def credentials(cfg):
    raw = yaml.safe_load(Path(cfg["credentials_file"]).read_text(encoding="utf-8"))
    if isinstance(raw.get("model"), dict):
        raw = raw["model"]
    auth = {"base_url": os.environ.get(cfg["base_url_env"]) or raw.get("base_url"),
            "api_key": os.environ.get(cfg["api_key_env"]) or raw.get("api_key"),
            "model": os.environ.get(cfg["model_env"]) or raw.get("name") or raw.get("model")}
    require(all(isinstance(x, str) and x.strip() for x in auth.values()), "Incomplete model credentials")
    return auth


def messages(prompt, payload, images):
    content = [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]
    for frame in images:
        label = {k: frame[k] for k in ("frame_index", "source_frame_index", "timestamp_sec")}
        if "is_subtask_boundary" in frame:
            label["is_subtask_boundary"] = frame["is_subtask_boundary"]
        if "subtask_refs" in frame:
            label["subtask_refs"] = frame["subtask_refs"]
        content.append({"type": "text", "text": "FRAME " + json.dumps(label)})
        content.append({"type": "image_url", "image_url": {
            "url": "data:image/jpeg;base64," + base64.b64encode(Path(frame["path"]).read_bytes()).decode(),
            "detail": "high"}})
    return [{"role": "system", "content": prompt}, {"role": "user", "content": content}]


class ModelClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.auth = credentials(cfg)

    def complete(self, prompt, payload, images, stage):
        from openai import OpenAI
        kwargs = {"model": self.auth["model"], "messages": messages(prompt, payload, images),
                  "temperature": self.cfg["temperature"], "max_tokens": self.cfg["max_tokens"],
                  "stream": self.cfg["stream"],
                  "extra_body": {"chat_template_kwargs": {"enable_thinking": stage["thinking"]}}}
        with OpenAI(base_url=self.auth["base_url"], api_key=self.auth["api_key"],
                    timeout=self.cfg["timeout_sec"], max_retries=0) as client:
            response = client.chat.completions.create(**kwargs)
            if not self.cfg["stream"]:
                return response.choices[0].message.content or "", response.choices[0].finish_reason
            parts, finish = [], None
            with response:
                for chunk in response:
                    if chunk.choices:
                        choice = chunk.choices[0]
                        if choice.delta.content:
                            parts.append(choice.delta.content)
                        if choice.finish_reason:
                            finish = choice.finish_reason
            return "".join(parts), finish


def parse_json_response(raw):
    text = raw.strip()
    lines = text.splitlines()
    if len(lines) >= 3 and lines[0].strip().lower() in {"```json", "```"} and lines[-1].strip() == "```":
        text = "\n".join(lines[1:-1])
    return json.loads(text)


def recover_fenced_response(folder, stamp, validate):
    # Legacy attempts use the enclosing input fingerprint; never reuse changed inputs.
    input_file = folder / "input.json"
    if not input_file.exists():
        return None
    require(read(input_file).get("request_fingerprint") == stamp,
            "Saved request changed; use a new output root")
    attempts = sorted(folder.glob("attempt_*"), key=lambda p: int(p.name.split("_")[-1]), reverse=True)
    for attempt in attempts:
        if not (attempt / "error.json").exists() or not (attempt / "response.json").exists():
            continue
        if read(attempt / "error.json").get("error_type") != "JSONDecodeError":
            continue
        response = read(attempt / "response.json")
        raw = response.get("content", "")
        if (response.get("finish_reason") != "stop" or not raw.strip().startswith("```")
                or response.get("request_fingerprint", stamp) != stamp):
            continue
        try:
            output = parse_json_response(raw)
            validate(output)
        except (ValueError, KeyError, TypeError):
            continue
        return {"request_fingerprint": stamp, "output": output,
                "recovery": {"method": "strip_outer_json_fence_then_validate",
                             "source_response": str(attempt / "response.json"),
                             "raw_content_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                             "api_called": False}}
    return None


def request(client, prompt, payload, frames, stage, folder, validate):
    folder = Path(folder)
    images = [{**f, "sha256": hashlib.sha256(Path(f["path"]).read_bytes()).hexdigest()} for f in frames]
    stamp = digest({"prompt": prompt, "payload": payload, "images": images, "stage": stage,
                    "model": client.auth["model"], "base_url": client.auth["base_url"],
                    "parameters": {k: client.cfg[k] for k in ("temperature", "max_tokens", "stream")}})
    cached = folder / "result.json"
    if cached.exists():
        saved = read(cached)
        require(saved["request_fingerprint"] == stamp, "Cached request changed; use a new output root")
        validate(saved["output"])
        return saved["output"]
    recovered = recover_fenced_response(folder, stamp, validate)
    if recovered is not None:
        save(cached, recovered)
        return recovered["output"]
    save(folder / "input.json", {"prompt": prompt, "payload": payload, "images": images,
                                 "request_fingerprint": stamp, "stage": stage,
                                 "model": client.auth["model"]})
    for attempt in range(client.cfg["attempts"]):
        attempt_dir = folder / f"attempt_{len(list(folder.glob('attempt_*'))) + 1:03d}"
        started = time.monotonic()
        try:
            raw, finish = client.complete(prompt, payload, images, stage)
            save(attempt_dir / "response.json", {"content": raw, "finish_reason": finish,
                                                 "request_fingerprint": stamp,
                                                 "elapsed_sec": time.monotonic() - started})
            require(finish == "stop", "Incomplete model response: " + str(finish))
            result = parse_json_response(raw)
            validate(result)
            save(cached, {"request_fingerprint": stamp, "output": result})
            return result
        except Exception as exc:
            # Avoid logging exception strings that could contain request URLs or credentials.
            message = str(exc) if isinstance(exc, (ValueError, KeyError, TypeError)) else type(exc).__name__
            save(attempt_dir / "error.json", {"error_type": type(exc).__name__, "message": message,
                                              "elapsed_sec": time.monotonic() - started})
            if attempt + 1 == client.cfg["attempts"]:
                raise RuntimeError(f"Stage request failed: {folder}; see attempt error files") from None
            time.sleep(client.cfg["retry_delay_sec"])
