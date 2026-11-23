import argparse
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

from .correction.client import ModelClient
from .correction.data import load_episode, select_ids
from .correction.io import digest, read, save, source_digest
from .correction.pipeline import process, PROMPTS
from .correction.schema import require, number

ROOT = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stage3-only visual adjudication and correction")
    parser.add_argument("--config", default=str(ROOT / "config/config.yaml"))
    parser.add_argument("--episode", action="append", help="Explicit episode ID, repeatable")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output-root")
    parser.add_argument("--dry-run", action="store_true", help="Load real data and extract frames; no API")
    args = parser.parse_args(argv)
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    if args.output_root:
        cfg["output_root"] = args.output_root
    if args.episode:
        cfg["episode_ids"] = args.episode
    require(type(cfg["episode_batchsize"]) is int and cfg["episode_batchsize"] > 0, "Invalid episode_batchsize")
    require(type(cfg["max_correction_retries"]) is int and cfg["max_correction_retries"] in (0, 1, 2),
            "At most three correction + verification iterations are supported")
    require(type(cfg["context_subtasks"]) is int and cfg["context_subtasks"] >= 0 and cfg["padding_sec"] >= 0,
            "Invalid context settings")
    require(cfg["model"]["attempts"] >= 1, "At least one API attempt required")
    require(set(cfg.get("required_reannotation_statuses", [])) <= set(cfg["audit_statuses"]),
            "Required reannotation statuses must be included in audit_statuses")
    require(set(cfg.get("whole_episode_statuses", [])) <= set(cfg.get("required_reannotation_statuses", [])),
            "Whole-episode statuses must require reannotation")
    require(all(type(s["thinking"]) is bool and (name == "verification" or s["fps"] > 0)
                for name, s in cfg["stages"].items()), "Invalid stage config")
    inner = cfg["post_correction_review"]["max_inner_frames"]
    require(type(inner) is int and 0 <= inner <= 4, "max_inner_frames must be in 0..4")
    interior_fps = cfg["post_correction_review"]["interior_fps"]
    require(number(interior_fps) and interior_fps > 0, "Invalid post-review interior_fps")
    output = Path(cfg["output_root"]).resolve()
    for name in ("annotations_root", "audit_root", "video_metadata_root"):
        source = Path(cfg[name]).resolve()
        require(output != source and not output.is_relative_to(source) and not source.is_relative_to(output),
                "Output must be separate from source trees")
    output.mkdir(parents=True, exist_ok=True)
    # POSIX advisory lock is released even when the process exits unexpectedly.
    import fcntl
    with (output / ".run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        selected = select_ids(cfg)
        if args.limit is not None:
            require(args.limit > 0, "limit must be positive")
            selected = selected[:args.limit]
        require(selected, "No episodes selected")
        code_hash = source_digest(ROOT)
        run_identity = {"fingerprint": digest({"config": cfg, "code": code_hash})}
        identity_file = output / "run_identity.json"
        if identity_file.exists():
            require(read(identity_file) == run_identity, "Run config/code changed; use a new output_root")
        else:
            save(identity_file, run_identity)
        save(output / "config_snapshot.json", cfg)
        save(output / "prompt_snapshot.json", PROMPTS)
        save(output / "selected_episodes.json", selected)
        # Credentials are never serialized into config snapshots or request files.
        client = None if args.dry_run else ModelClient(cfg["model"])
        started = time.time()
        states, failures = {}, {}

        def worker(eid):
            job = load_episode(cfg, eid)
            return process(job, cfg, client, code_hash, args.dry_run)

        def report():
            counts = dict(Counter(row["status"] for row in states.values()))
            reviewed = [row for row in states.values() if "review_status" in row]
            progress = {"total": len(selected), "finished": len(states), "counts": counts,
                        "initial_review_counts": dict(Counter(row["initial_review_status"] or "unresolved"
                                                               for row in reviewed)),
                        "final_review_counts": dict(Counter(row["review_status"] or "unresolved" for row in reviewed)),
                        "needs_manual_review": sum(row.get("needs_manual_review", False) for row in reviewed),
                        "failed": len(failures), "elapsed_sec": round(time.time() - started, 2),
                        "started_at_unix": started, "updated_at_unix": time.time(),
                        "all_finished": len(states) == len(selected), "dry_run": args.dry_run}
            save(output / "progress.json", progress)
            save(output / "failures.json", failures)
            print(json.dumps(progress, ensure_ascii=False), flush=True)

        report()
        with ThreadPoolExecutor(max_workers=cfg["episode_batchsize"]) as pool:
            pending = {pool.submit(worker, eid): eid for eid in selected}
            for future in as_completed(pending):
                eid = pending[future]
                try:
                    states[eid] = future.result()
                except Exception as exc:
                    message = str(exc) if isinstance(exc, (ValueError, RuntimeError, KeyError)) else type(exc).__name__
                    failures[eid] = {"type": type(exc).__name__, "message": message}
                    states[eid] = {"status": "failed"}
                report()
        save(output / "results.json", states)
        return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
