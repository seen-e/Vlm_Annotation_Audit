import copy
import hashlib
import re
from pathlib import Path

from .io import read
from .schema import require, timeline


def safe_id(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]+", value), "Unsafe episode ID")
    return value


def audit_hints(audit, original):
    # Copy only exact Stage3 quotations. Cross-stage claims and reasons are not forwarded.
    result = []
    reviews = audit.get("round1", {}).get("subtask_reviews", [])
    for review in reviews:
        if review.get("text_status") not in {"inconsistent", "needs_visual"}:
            continue
        for ref in review.get("text_evidence", []):
            if ref.get("source") != "final_annotation":
                continue
            match = re.fullmatch(r"/subtasks/(\d+)/(segment_summary|arm_[12]/(?:description|outcome))",
                                 ref.get("path", ""))
            if not match:
                continue
            pos, field = int(match[1]), match[2]
            require(pos < len(original["subtasks"]), "Audit reference outside Stage3")
            row = original["subtasks"][pos]
            value = row
            for key in field.split("/"):
                value = value[key]
            quote = ref.get("quote")
            require(isinstance(value, str) and isinstance(quote, str) and quote and quote in value,
                    "Audit quote does not match current Stage3; stale audit input")
            hint = {"subtask_index": row.get("subtask_index", pos), "field": field,
                    "stage3_claim": quote, "status": "unverified_audit_candidate"}
            if hint not in result:
                result.append(hint)
    return result


def normalize(original, manifest, duration):
    require(isinstance(original.get("subtasks"), list) and original["subtasks"], "Missing original subtasks")
    samples = manifest.get("samples", [])
    rows = []
    for pos, raw in enumerate(original["subtasks"]):
        sid = raw.get("subtask_index", pos)
        require(type(sid) is int, "Invalid original subtask ID")
        if "start_time_sec" in raw and "end_time_sec" in raw:
            start, end = raw["start_time_sec"], raw["end_time_sec"]
        else:
            a, b = raw.get("start_sample_index"), raw.get("end_sample_index_exclusive")
            require(type(a) is int and type(b) is int and 0 <= a < b <= len(samples),
                    "Original sample interval is invalid; cannot guess timestamp mapping")
            start = samples[a]["timestamp_sec"] if a else 0.0
            end = samples[b]["timestamp_sec"] if b < len(samples) else duration
        row = {k: copy.deepcopy(raw[k]) for k in (
            "segment_type", "arm_1", "arm_2", "segment_summary", "boundary_evidence",
            "external_event_description", "uncertainty") if k in raw}
        row.update(subtask_index=sid, start_time_sec=start, end_time_sec=end)
        rows.append(row)
    require(len({r["subtask_index"] for r in rows}) == len(rows), "Duplicate original subtask IDs")
    timeline(rows, 0.0, duration)
    return rows


def load_episode(cfg, eid):
    safe_id(eid)
    root = Path(cfg["annotations_root"]) / eid
    if cfg.get("handoff_manifest"):
        handoff = read(cfg["handoff_manifest"])
        record = handoff["episodes"][eid]
        for path, expected in (
            (root / "final.json", record["annotation_sha256"]),
            (Path(cfg["audit_root"]) / eid / "final.json", record["audit_sha256"]),
            (Path(cfg["standards_path"]), handoff["standards_sha256"]),
        ):
            require(hashlib.sha256(path.read_bytes()).hexdigest() == expected,
                    "Audit handoff input changed; rerun handoff validation")
    original = read(root / "final.json")
    require(original.get("episode_id", eid) == eid, "Stage3 episode mismatch")
    manifest = read(root / "sample_manifest.json")
    meta = read(Path(cfg["video_metadata_root"]) / eid / "metadata.json")["source"]
    require(meta.get("episode_id") == eid, "Video episode mismatch")
    fps, length, offset = meta["fps"], meta["length"], meta["first_source_frame"]
    require(isinstance(fps, (int, float)) and fps > 0 and type(length) is int and length > 0
            and type(offset) is int and offset >= 0, "Bad video offset or frame count")
    require(meta["last_source_frame"] == offset + length - 1, "Video interval length mismatch")
    require(manifest["frame_count"] == length and abs(manifest["fps"] - fps) < 1e-6,
            "Stage3 manifest and source video metadata disagree")
    video = Path(meta["source_video"])
    stat = video.stat()
    require(stat.st_size == meta["source_size"], "Source video size changed")
    standard = read(cfg["standards_path"])["episodes"][eid]
    audit = read(Path(cfg["audit_root"]) / eid / "final.json")
    require(audit.get("episode_id") == eid, "Audit episode mismatch")
    duration = length / fps
    rows = normalize(original, manifest, duration)
    return {"episode_id": eid, "original": original, "rows": rows, "audit": audit,
            "feedback": audit_hints(audit, original), "meta": meta, "duration": duration,
            "original_sampled_frames": [{"sample_index": i, "frame_index": f["frame_index"],
                                          "timestamp_sec": f["timestamp_sec"]}
                                         for i, f in enumerate(manifest["samples"])],
            "task": standard.get("task"), "review_standard": standard["review_standard"],
            "video_identity": {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}}


def select_ids(cfg):
    if cfg["episode_ids"]:
        selected = cfg["episode_ids"]
    elif cfg.get("episode_ids_file"):
        selected = read(cfg["episode_ids_file"])
        require(isinstance(selected, list), "episode_ids_file must contain a JSON list")
    else:
        selected = []
        for path in sorted(Path(cfg["audit_root"]).glob("*/final.json")):
            row = read(path)
            if row.get("final_status") in cfg["audit_statuses"]:
                selected.append(path.parent.name)
    require(len(selected) == len(set(selected)), "Duplicate episodes")
    return [safe_id(eid) for eid in selected]
