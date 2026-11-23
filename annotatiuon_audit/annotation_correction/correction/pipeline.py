import copy
import shutil
from pathlib import Path

from ..prompts import adjudication, plan, refine, finalize, verification
from . import schema
from .client import request
from .io import digest, read, save
from .media import extract
from .merge import regions, merge
from .review import summarize, execution_plan
from ..post_correction_review.runner import run as post_review

PROMPTS = {"adjudication": adjudication.PROMPT, "plan": plan.PROMPT, "refine": refine.PROMPT,
           "finalize": finalize.PROMPT, "verification": verification.PROMPT}


def base_payload(job, rows):
    return {"episode_id": job["episode_id"], "task": job["task"],
            "review_standard": job["review_standard"], "stage3_annotation": {"subtasks": rows},
            "original_sampled_frames": job.get("original_sampled_frames", []),
            "sampling_convention": "图像按配置FPS采样，并可额外补充subtask首尾帧。图像间隔可能不均匀，"
                                   "以每张FRAME的frame_index和timestamp_sec为准，不按图片序号推算时间。"
                                   "边界帧只标记待核验的分段位置，不证明实际动作在此开始或结束。",
            "index_convention": "旧文本sample N对应original_sampled_frames中的sample_index；"
                                "图片FRAME的frame_index是episode绝对帧号，二者不能混用。"}


def process(job, cfg, client, code_hash, dry_run=False, extract_frames=extract):
    folder = Path(cfg["output_root"]) / job["episode_id"]
    identity = digest({"job": job, "config": cfg, "code": code_hash})
    identity_file = folder / "identity.json"
    if identity_file.exists():
        schema.require(read(identity_file)["fingerprint"] == identity,
                       "Inputs/config/code changed; choose a new output_root")
    else:
        save(identity_file, {"fingerprint": identity})
    status_file = folder / "status.json"
    if not dry_run and status_file.exists():
        previous = read(status_file)
        if previous["status"] in {"no_change", "manual_review", "corrected", "reannotated"}:
            if previous["status"] in {"corrected", "reannotated"}:
                final = read(folder / "final_corrected_stage3.json")
                schema.timeline(final["subtasks"], 0, job["duration"])
                schema.require(read(folder / "verification.json")["verification"] == "pass",
                               "Corrected output has no passed verification")
                schema.require(previous.get("final_sha256") == digest(final), "Final result changed")
                schema.require(previous.get("review_sha256") == digest(read(folder / "final_review.json")),
                               "Final review changed")
            return previous
    save(folder / "original_stage3.json", job["original"])
    save(folder / "stage3_input.json", {"subtasks": job["rows"]})
    save(folder / "audit_feedback.json", job["feedback"])
    save(folder / "source.json", {"video": job["meta"], "duration_sec": job["duration"],
                                  "task": job["task"], "review_standard": job["review_standard"]})
    cache = folder / "frame_cache"

    def frames(stage, interval, body=None):
        rows = job["rows"] if body is None else body["stage3_annotation"]["subtasks"]
        if body and body.get("candidate_annotation"):
            rows = rows + body["candidate_annotation"]["subtasks"]
        sampling_job = {**job, "sampling_subtasks": rows}
        return extract_frames(sampling_job, *interval, cfg["stages"][stage]["fps"], cfg["frames"], cache)

    def call(stage, payload, interval, directory, validator):
        save(status_file, {"status": "running", "stage": stage})
        images = frames(stage, interval, payload)
        return request(client, PROMPTS[stage], payload, images, cfg["stages"][stage],
                       directory, lambda value: validator(value, images))

    whole = [0.0, job["duration"]]
    payload = base_payload(job, job["rows"])
    payload["audit_feedback"] = job["feedback"]
    payload["observe_interval"] = whole
    payload["review_context"] = {"prior_audit_status": job["audit"].get("final_status"),
                                 "dataset_expected_success": cfg.get("dataset_expected_success", False)}
    if dry_run:
        images = frames("adjudication", whole)
        save(folder / "preflight.json", {"payload": payload, "images": images,
                                         "frame_count": len(images), "api_called": False})
        preview = post_review(job, job["rows"], cfg, None, folder / "post_review_preflight", cache,
                              extract_frames, dry_run=True)
        return {"status": "preflight_ok", "frame_count": len(images), "post_review_frames": preview["frame_count"]}
    decision = call("adjudication", payload, whole, folder / "adjudication",
                    lambda value, images: schema.adjudication(value, job["rows"], images, payload))
    save(folder / "adjudication.json", decision)
    initial_review = summarize(decision["stage3_verdict"], decision["reason"], decision["task_assessment"])
    initial_review["annotation_path"] = "stage3_input.json"
    save(folder / "initial_review.json", initial_review)
    if decision["task_not_met_diagnosis"] is not None:
        save(folder / "task_not_met_diagnosis.json", decision["task_not_met_diagnosis"])
    prior_status = job["audit"].get("final_status")
    confirmed = decision["stage3_verdict"] == "incorrect"
    execution = execution_plan(prior_status, decision, cfg)
    if execution is None:
        state = "manual_review" if initial_review["needs_manual_review"] else "no_change"
        save(folder / "final_review.json", initial_review)
        result = {"status": state, "stage3_verdict": decision["stage3_verdict"], "reason": decision["reason"],
                  "review_status": initial_review["review_status"],
                  "initial_review_status": initial_review["review_status"],
                  "needs_manual_review": initial_review["needs_manual_review"]}
        save(status_file, result)
        return result

    save(folder / "execution_plan.json", execution)
    selected = regions(job["rows"], execution, job["duration"], cfg["context_subtasks"], cfg["padding_sec"])
    last_verification = None
    for attempt in range(cfg["max_correction_retries"] + 1):
        work = folder / f"correction_{attempt + 1:02d}"
        replacements = []
        for index, region in enumerate(selected):
            body = base_payload(job, job["rows"])
            body.update(write_interval=region["write_interval"], observe_interval=region["observe_interval"],
                        replace_subtasks=region["replace_subtasks"], reannotation_request=execution)
            body["dataset_context"] = {"expected_success": cfg.get("dataset_expected_success", False)}
            if decision["task_not_met_diagnosis"] is not None:
                body["task_not_met_diagnosis"] = decision["task_not_met_diagnosis"]
            if confirmed:
                body["confirmed_problem"] = decision
            if last_verification:
                body["previous_verification"] = last_verification
                body["previous_candidate"] = previous_candidate
            candidate = None
            for stage in ("plan", "refine", "finalize"):
                stage_body = copy.deepcopy(body)
                if candidate:
                    stage_body["candidate_annotation"] = candidate
                candidate = call(stage, stage_body, region["observe_interval"],
                                 work / f"region_{index:03d}" / stage,
                                 lambda value, images: schema.generated(value, *region["write_interval"], images))
            replacements.append((region, candidate["subtasks"]))
        merged, record = merge(job["rows"], replacements, job["duration"])
        candidate_document = {"schema_version": "stage3-correction-v1", "episode_id": job["episode_id"],
                              "time_basis": "episode_absolute_seconds_half_open", "subtasks": merged}
        save(work / "replacement.json", [{"region": r, "subtasks": g} for r, g in replacements])
        save(work / "merge_record.json", record)
        save(work / "candidate_stage3.json", candidate_document)
        # Independent verification sees neither audit feedback nor repair reasoning.
        save(status_file, {"status": "running", "stage": "post_correction_review"})
        checked = post_review(job, merged, cfg, client, work / "post_correction_review", cache, extract_frames)
        save(folder / "verification.json", checked)
        verdict = {"pass": "correct", "retry": "incorrect", "manual_review": "insufficient_evidence"}[
            checked["verification"]]
        final_review = summarize(verdict, checked["reason"], checked["task_assessment"])
        final_review["annotation_path"] = str((work / "candidate_stage3.json").relative_to(folder)).replace("\\", "/")
        save(work / "review.json", final_review)
        save(folder / "final_review.json", final_review)
        if checked["verification"] == "pass":
            save(folder / "merge_record.json", record)
            save(folder / "final_corrected_stage3.json", candidate_document)
            final_review["annotation_path"] = "final_corrected_stage3.json"
            save(folder / "final_review.json", final_review)
            result = {"status": "corrected" if confirmed else "reannotated", "scope": execution["scope"],
                      "trigger": execution["trigger"], "attempt": attempt + 1,
                      "final_sha256": digest(candidate_document),
                      "review_sha256": digest(final_review),
                      "initial_review_status": initial_review["review_status"],
                      "review_status": final_review["review_status"],
                      "needs_manual_review": final_review["needs_manual_review"]}
            save(status_file, result)
            if not cfg["frames"]["keep_cache"]:
                # Only remove the explicitly created per-episode cache.
                schema.require(cache.resolve().parent == folder.resolve(), "Unexpected cache path")
                shutil.rmtree(cache)
            return result
        if checked["verification"] == "manual_review":
            break
        replaced_new = {sid for r in record["replacements"] for sid in r["new_ids"]}
        if not set(checked["failed_subtasks"]) <= replaced_new:
            # A local repair cannot silently expand its previously adjudicated write scope.
            checked = {**checked, "verification": "manual_review",
                       "reason": "验收发现替换范围以外的问题，需要重新裁决范围。"}
            save(folder / "verification.json", checked)
            break
        last_verification = checked
        previous_candidate = candidate_document
    final_review["needs_manual_review"] = True
    save(folder / "final_review.json", final_review)
    result = {"status": "manual_review", "reason": checked["reason"],
              "correction_attempts": attempt + 1, "initial_review_status": initial_review["review_status"],
              "review_status": final_review["review_status"], "needs_manual_review": True}
    save(status_file, result)
    return result
