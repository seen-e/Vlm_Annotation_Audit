import math
from pathlib import Path

from .io import digest, save
from .schema import require


def subtask_boundaries(meta, rows):
    values = set()
    for row in rows:
        start, end = row["start_time_sec"], row["end_time_sec"]
        require(0 <= start < end <= meta["length"] / meta["fps"] + 1e-6, "Invalid subtask interval")
        # Half-open intervals: first frame at/after start, last frame strictly before end.
        first = math.ceil(start * meta["fps"] - 1e-7)
        last = min(meta["length"] - 1, math.ceil(end * meta["fps"] - 1e-7) - 1)
        require(first <= last, "Subtask interval contains no source frame")
        values.update((first, last))
    return values


def frame_indices(meta, start, end, fps, max_frames, boundaries=()):
    require(0 < fps <= meta["fps"], "Requested FPS must be within source FPS")
    require(0 <= start < end <= meta["length"] / meta["fps"] + 1e-6, "Invalid observation interval")
    count = math.ceil((end - start) * fps)
    require(count <= max_frames, "Frame budget exceeded; raise max_frames explicitly")
    # Episode-local indices are converted to shared-file indices only during decoding.
    values = {min(meta["length"] - 1, int(math.floor((start + i / fps) * meta["fps"] + .5)))
              for i in range(count) if start + i / fps < end}
    values.add(min(meta["length"] - 1, max(0, math.ceil(end * meta["fps"]) - 1)))
    lo, hi = math.ceil(start * meta["fps"] - 1e-7), math.ceil(end * meta["fps"] - 1e-7)
    values.update(index for index in boundaries if lo <= index < hi)
    require(len(values) <= max_frames, "Frame budget exceeded including final and subtask boundary frames")
    return sorted(values)


def extract(job, start, end, fps, cfg, cache):
    import av
    import cv2
    meta = job["meta"]
    if "requested_frame_indices" in job:
        indices = sorted(set(job["requested_frame_indices"]))
        require(indices and len(indices) <= cfg["max_frames"], "Explicit frame budget exceeded or empty")
        require(all(type(i) is int and 0 <= i < meta["length"] and
                    start - 1e-7 <= i / meta["fps"] < end for i in indices), "Invalid explicit frame index")
        boundaries = set()
    else:
        boundaries = subtask_boundaries(meta, job.get("sampling_subtasks", job.get("rows", []))) \
            if cfg.get("include_subtask_boundaries", True) else set()
        indices = frame_indices(meta, start, end, fps, cfg["max_frames"], boundaries)
    key = digest({"source": meta, "identity": job["video_identity"], "images": cfg})[:20]
    folder = Path(cache) / key
    folder.mkdir(parents=True, exist_ok=True)
    frames = []
    missing = []
    for index in indices:
        path = folder / f"{index:08d}.jpg"
        if not path.is_file() or cv2.imread(str(path)) is None:
            missing.append(index)
        frames.append({"frame_index": index, "source_frame_index": meta["first_source_frame"] + index,
                       "timestamp_sec": index / meta["fps"], "path": str(path),
                       "is_subtask_boundary": index in boundaries})
    if missing:
        pending = {meta["first_source_frame"] + index: index for index in missing}
        with av.open(meta["source_video"]) as container:
            stream = container.streams.video[0]
            require(abs(float(stream.average_rate) - meta["fps"]) < .01, "Decoded FPS disagrees with metadata")
            origin = stream.start_time or 0
            seek_pts = origin + int(min(pending) / meta["fps"] / stream.time_base)
            container.seek(seek_pts, stream=stream, backward=True, any_frame=False)
            for decoded in container.decode(stream):
                require(decoded.pts is not None, "Source frame has no timestamp")
                position = float((decoded.pts - origin) * stream.time_base) * meta["fps"]
                source = round(position)
                require(abs(source - position) < .1, "Variable-frame-rate video needs an explicit frame map")
                if source < min(pending):
                    continue
                require(source <= min(pending), "Requested source frame is missing from decoder")
                index = pending.pop(source)
                image = decoded.to_ndarray(format="bgr24")
                height, width = image.shape[:2]
                scale = min(1.0, cfg["max_side"] / max(height, width))
                if scale < 1:
                    image = cv2.resize(image, (round(width * scale), round(height * scale)))
                encoded, raw = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, cfg["jpeg_quality"]])
                require(encoded, "JPEG encoding failed")
                path = folder / f"{index:08d}.jpg"
                temporary = path.with_suffix(".tmp")
                temporary.write_bytes(raw.tobytes())
                temporary.replace(path)
                if not pending:
                    break
            require(not pending, "Video ended before requested episode frames")
    save(folder / (digest(indices)[:16] + ".json"), frames)
    return frames
