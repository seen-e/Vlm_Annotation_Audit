import argparse
from pathlib import Path

import yaml

from ..correction.client import ModelClient
from ..correction.data import load_episode
from ..correction.io import digest, read, save, source_digest
from ..correction.schema import require
from .runner import run


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Review an already corrected Stage3 episode")
    parser.add_argument("--config", default=str(root / "config/config.yaml"))
    parser.add_argument("--episode", required=True)
    parser.add_argument("--annotation", required=True, help="Corrected Stage3 JSON with absolute second boundaries")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    annotation = Path(args.annotation).resolve()
    output = (Path(args.output_root) / args.episode).resolve()
    require(not annotation.is_relative_to(output), "Review output must not contain the source annotation")
    for key in ("annotations_root", "audit_root", "video_metadata_root"):
        source = Path(cfg[key]).resolve()
        require(not output.is_relative_to(source) and not source.is_relative_to(output), "Unsafe review output path")
    job = load_episode(cfg, args.episode)
    document = read(annotation)
    require(document.get("episode_id", args.episode) == args.episode, "Annotation episode mismatch")
    rows = document["subtasks"]
    output.mkdir(parents=True, exist_ok=True)
    import fcntl
    with (output / ".run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        identity = {"fingerprint": digest({"job": job, "annotation": document, "config": cfg,
                                          "code": source_digest(root)})}
        if (output / "identity.json").exists():
            require(read(output / "identity.json") == identity, "Review inputs changed; use a new output root")
        save(output / "identity.json", identity)
        result = run(job, rows, cfg, None if args.dry_run else ModelClient(cfg["model"]),
                     output, output / "frame_cache", dry_run=args.dry_run)
        print({"episode_id": args.episode, "status": "preflight_ok" if args.dry_run else result["verification"],
               "output": str(output)}, flush=True)


if __name__ == "__main__":
    main()
