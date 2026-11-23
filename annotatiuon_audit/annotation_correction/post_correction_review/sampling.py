import math

from ..correction.schema import require, timeline


def sampling_plan(meta, rows, max_inner_frames=4, max_frames=1000, interior_fps=1):
    require(type(max_inner_frames) is int and 0 <= max_inner_frames <= 4,
            "max_inner_frames must be an integer from zero to four")
    require(type(interior_fps) in (int, float) and math.isfinite(interior_fps)
            and 0 < interior_fps <= meta["fps"], "Invalid interior sampling FPS")
    timeline(rows, 0, meta["length"] / meta["fps"])
    ids = [r["subtask_index"] for r in rows]
    require(all(type(i) is int for i in ids) and len(set(ids)) == len(ids), "Invalid subtask IDs")
    groups, mapping = [], {}
    for row in rows:
        first = math.ceil(row["start_time_sec"] * meta["fps"] - 1e-7)
        last = min(meta["length"] - 1, math.ceil(row["end_time_sec"] * meta["fps"] - 1e-7) - 1)
        require(0 <= first <= last, "Subtask interval contains no source frame")
        # Sample relative to the subtask start, then cap without densifying short segments.
        count = math.ceil((row["end_time_sec"] - row["start_time_sec"]) * interior_fps)
        inner = sorted({int(math.floor((row["start_time_sec"] + k / interior_fps) * meta["fps"] + .5))
                        for k in range(1, count)}) if max_inner_frames else []
        inner = [i for i in inner if first < i < last]
        if len(inner) > max_inner_frames:
            inner = [inner[len(inner) // 2]] if max_inner_frames == 1 else [
                inner[k * (len(inner) - 1) // (max_inner_frames - 1)] for k in range(max_inner_frames)]
        samples = {}
        for index, role in [(first, "start_boundary"), *[(i, "interior") for i in inner], (last, "end_boundary")]:
            samples.setdefault(index, []).append(role)
        refs = []
        for index, roles in sorted(samples.items()):
            mapping.setdefault(index, []).append({"subtask_index": row["subtask_index"], "roles": roles})
            refs.append({"frame_index": index, "timestamp_sec": index / meta["fps"], "roles": roles})
        groups.append({"subtask_index": row["subtask_index"], "start_time_sec": row["start_time_sec"],
                       "end_time_sec": row["end_time_sec"], "frames": refs})
    require(len(mapping) <= max_frames, "Post-review frame budget exceeded; no silent frame dropping")
    return {"subtasks": groups, "frame_indices": sorted(mapping), "frame_refs": mapping}
