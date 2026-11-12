"""Export completed anomaly candidates with an optional per-task limit."""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--standards", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--max-per-task", type=int, default=2, help="0 exports all anomalies")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.max_per_task < 0 or args.workers < 1:
        parser.error("max-per-task must be nonnegative and workers must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    standards = read(args.standards)["episodes"]
    anomalies = read(args.run / "anomalies.json")
    grouped = defaultdict(list)
    for review in sorted(anomalies, key=lambda x: x["episode_id"]):
        if review["review_status"] == "anomaly":
            grouped[standards[review["episode_id"]]["task_id"]].append(review)
    selected = [r for task in sorted(grouped, key=lambda x: int(x))
                for r in grouped[task][:args.max_per_task or None]]
    progress = read(args.run / "progress.json")

    def export(review):
        eid = review["episode_id"]
        folder = args.output / "episodes" / eid
        folder.mkdir(parents=True, exist_ok=True)
        request = read(args.run / eid / "input.json")
        payload = request["payload"]
        annotation = payload["final_annotation"]
        metadata = read(args.cache / eid / "metadata.json")
        source = metadata["source"]
        manifest_path = Path(request["annotation_path"]).parent / "sample_manifest.json"
        manifest = read(manifest_path)
        samples = manifest["samples"]
        duration = source["length"] / source["fps"]
        bounds = []
        for i, subtask in enumerate(annotation["subtasks"]):
            start = subtask.get("start_sample_index")
            end = subtask.get("end_sample_index_exclusive")
            valid = isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(samples)
            bounds.append({"subtask_index": subtask.get("subtask_index", i),
                           "start": samples[start]["timestamp_sec"] if valid else None,
                           "end": (samples[end]["timestamp_sec"] if end < len(samples) else duration) if valid else None})
        write(folder / "review.json", review)
        write(folder / "final.json", annotation)
        write(folder / "review_input.json", payload)
        write(folder / "sample_manifest.json", manifest)
        write(folder / "video_source.json", metadata)
        for name in ("first.png", "last.png"):
            shutil.copyfile(args.cache / eid / name, folder / name)
        video = folder / "video.mp4"
        if not video.exists():
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                            "-ss", str(source["first_source_frame"] / source["fps"]),
                            "-i", source["source_video"], "-frames:v", str(source["length"]),
                            "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                            "-threads", "2", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video)],
                           check=True, timeout=240)
        info = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-count_frames", "-show_entries", "stream=width,height,nb_read_frames,duration",
                          "-of", "json", str(video)]))["streams"][0]
        assert int(info["nb_read_frames"]) == source["length"], (eid, info, source["length"])
        print(f"Exported {eid}: {source['length']} frames", flush=True)
        return {"episode_id": eid, **standards[eid], "review_standard": payload["review_standard"],
                "annotation": annotation, "review": review, "bounds": bounds,
                "duration": duration, "video_info": info}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        episodes = list(pool.map(export, selected))
    data = {"generated_at": datetime.now(timezone.utc).isoformat(), "source_run": str(args.run),
            "progress": progress, "anomaly_snapshot_count": len(anomalies),
            "max_per_task": args.max_per_task,
            "selection": (f"episode_id ascending; first {args.max_per_task} anomalies per task"
                          if args.max_per_task else "all completed anomaly candidates"),
            "task_count": len(grouped), "episodes": episodes}
    write(args.output / "selection.json", data)
    (args.output / "data.js").write_text("window.REVIEW_DATA=" + json.dumps(data, ensure_ascii=False) + ";\n", encoding="utf-8")
    shutil.copyfile(Path(__file__).with_name("index.html"), args.output / "index.html")
    print(json.dumps({"selected": len(episodes), "tasks": len(grouped), "snapshot_anomalies": len(anomalies)}), flush=True)


if __name__ == "__main__":
    main()
