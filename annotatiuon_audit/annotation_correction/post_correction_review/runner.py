from pathlib import Path

from ..correction import schema
from ..correction.client import request
from ..correction.io import save
from ..correction.media import extract
from ..prompts.verification import PROMPT
from .sampling import sampling_plan


def prepare(job, rows, cfg, cache, extract_frames=extract):
    plan = sampling_plan(job["meta"], rows, cfg["post_correction_review"]["max_inner_frames"],
                         cfg["frames"]["max_frames"], cfg["post_correction_review"]["interior_fps"])
    images = extract_frames({**job, "sampling_subtasks": rows, "requested_frame_indices": plan["frame_indices"]},
                            0., job["duration"], 1, cfg["frames"], cache)
    schema.require([f["frame_index"] for f in images] == plan["frame_indices"],
                   "Decoded post-review frames do not match sampling plan")
    tagged = []
    for frame in images:
        refs = plan["frame_refs"][frame["frame_index"]]
        tagged.append({**frame, "subtask_refs": refs,
                       "is_subtask_boundary": any("start_boundary" in r["roles"] or "end_boundary" in r["roles"]
                                                   for r in refs)})
    payload = {"episode_id": job["episode_id"], "task": job["task"], "review_standard": job["review_standard"],
               "stage3_annotation": {"subtasks": rows}, "subtask_frames": plan["subtasks"],
               "original_sampled_frames": job.get("original_sampled_frames", []),
               "index_convention": "FRAME使用episode绝对帧号和时间。旧文本sample引用只可按original_sampled_frames解释。",
               "sampling_convention": {
                   "interior_fps": cfg["post_correction_review"]["interior_fps"],
                   "max_inner_frames": cfg["post_correction_review"]["max_inner_frames"],
                   "rule": "首尾边界帧另加；内部从段起点按配置FPS采样，超过上限才均匀选取；不足不补齐，重复帧去重。"},
               "dataset_context": {"expected_success": cfg.get("dataset_expected_success", False)}}
    return payload, tagged, plan


def validate(result, rows, images, payload):
    schema.require(isinstance(result, dict) and {"evidence_requests", "replacement_ranges"} <= set(result),
                   "Missing post-review follow-up fields")
    schema.verification({k: v for k, v in result.items() if k not in {"evidence_requests", "replacement_ranges"}},
                        rows, images, payload)
    allowed = [r["subtask_index"] for r in rows]
    requests = result["evidence_requests"]
    schema.require(isinstance(requests, list), "evidence_requests must be a list")
    for item in requests:
        schema.require(isinstance(item, dict) and set(item) == {"subtask_indices", "question"}, "Bad evidence request")
        schema.ids(item["subtask_indices"], allowed)
        schema.require(item["subtask_indices"] and schema.text(item["question"]), "Empty evidence request")
    ranges, covered, previous = result["replacement_ranges"], set(), -1
    schema.require(isinstance(ranges, list), "replacement_ranges must be list")
    for span in ranges:
        schema.require(isinstance(span, dict) and set(span) == {"first_subtask_index", "last_subtask_index"},
                       "Bad post-review replacement range")
        a, b = span["first_subtask_index"], span["last_subtask_index"]
        schema.require(type(a) is int and type(b) is int and a in allowed and b in allowed, "Unknown range IDs")
        lo, hi = allowed.index(a), allowed.index(b)
        schema.require(previous < lo <= hi, "Review ranges must be ordered and disjoint")
        covered.update(allowed[lo:hi + 1])
        previous = hi
    if result["verification"] == "pass":
        schema.require(not requests and not ranges, "Pass cannot have pending follow-ups")
    elif result["verification"] == "retry":
        schema.require(ranges and set(result["failed_subtasks"]) <= covered, "Correction ranges miss failed subtasks")
        schema.require(not requests, "Pending evidence must be reviewed before automatic correction")
    else:
        schema.require(not ranges, "Unresolved review cannot prescribe automatic replacement")


def run(job, rows, cfg, client, directory, cache, extract_frames=extract, dry_run=False):
    directory = Path(directory)
    payload, images, plan = prepare(job, rows, cfg, cache, extract_frames)
    save(directory / "sampling_plan.json", plan)
    if dry_run:
        result = {"payload": payload, "images": images, "frame_count": len(images), "api_called": False}
        save(directory / "preflight.json", result)
        return result
    result = request(client, PROMPT, payload, images, cfg["stages"]["verification"], directory,
                     lambda value: validate(value, rows, images, payload))
    save(directory / "output.json", result)
    by_id = {r["subtask_index"]: r for r in rows}
    followups = [{**item, "intervals": [{"subtask_index": sid,
                 "start_time_sec": by_id[sid]["start_time_sec"], "end_time_sec": by_id[sid]["end_time_sec"]}
                 for sid in item["subtask_indices"]]} for item in result["evidence_requests"]]
    save(directory / "followup_plan.json", {"evidence_requests": followups,
                                            "replacement_ranges": result["replacement_ranges"]})
    return result
