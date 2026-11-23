import math

ERROR_TYPES = {"action_error", "object_error", "actor_error", "result_error",
               "segmentation_error", "omission_error"}
ROW_KEYS = {"start_time_sec", "end_time_sec", "segment_type", "arm_1", "arm_2",
            "segment_summary", "boundary_evidence", "external_event_description", "uncertainty"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def ids(values, allowed):
    require(isinstance(values, list) and all(type(x) is int and x in allowed for x in values),
            "Invalid subtask IDs")
    require(len(values) == len(set(values)), "Duplicate subtask IDs")


def evidence(values, frames):
    available = {f["frame_index"] for f in frames}
    require(isinstance(values, list), "visual_evidence must be a list")
    for row in values:
        require(isinstance(row, dict) and set(row) == {"frame_index", "observation"}, "Bad evidence fields")
        require(type(row["frame_index"]) is int and row["frame_index"] in available,
                "Evidence references a frame not supplied")
        require(text(row["observation"]), "Missing visible observation")


def adjudication(result, rows, frames, payload=None):
    require(isinstance(result, dict) and set(result) == {
        "visual_evidence", "reason", "stage3_verdict", "error_types", "target_subtasks",
        "scope", "scope_reason", "replacement_ranges", "reannotation_instruction",
        "task_not_met_diagnosis", "task_assessment"}, "Bad adjudication fields")
    evidence(result["visual_evidence"], frames)
    require(text(result["reason"]) and text(result["scope_reason"]), "Missing adjudication reason")
    verdict, scope = result["stage3_verdict"], result["scope"]
    require(verdict in {"correct", "incorrect", "insufficient_evidence"}, "Unknown verdict")
    require(scope in {"none", "partial", "whole_episode"}, "Unknown scope")
    completion_diagnosis(result["task_not_met_diagnosis"], payload or {}, frames, verdict)
    task_assessment(result["task_assessment"], payload or {}, frames)
    allowed = [r["subtask_index"] for r in rows]
    ids(result["target_subtasks"], allowed)
    errors = result["error_types"]
    require(isinstance(errors, list) and all(x in ERROR_TYPES for x in errors), "Unknown Stage3 error type")
    ranges = result["replacement_ranges"]
    require(isinstance(ranges, list), "replacement_ranges must be list")
    if verdict != "incorrect":
        require(scope == "none" and not ranges and not errors and not result["target_subtasks"]
                and result["reannotation_instruction"] is None, "Non-errors cannot trigger correction")
        return
    require(scope != "none" and errors and result["visual_evidence"]
            and text(result["reannotation_instruction"]), "Correction requires error and visual evidence")
    if scope == "whole_episode":
        require(not ranges, "Whole episode has no local ranges")
        return
    require(ranges and result["target_subtasks"], "Partial correction requires ranges and targets")
    covered, previous = set(), -1
    for interval in ranges:
        require(isinstance(interval, dict) and set(interval) == {
            "first_subtask_index", "last_subtask_index"}, "Bad replacement range")
        first, last = interval["first_subtask_index"], interval["last_subtask_index"]
        require(type(first) is int and type(last) is int and first in allowed and last in allowed,
                "Unknown replacement boundary ID")
        a, b = allowed.index(first), allowed.index(last)
        require(previous < a <= b, "Replacement ranges must be sorted, disjoint and forward")
        covered.update(allowed[a:b + 1])
        previous = b
    require(set(result["target_subtasks"]) <= covered, "Replacement ranges miss targets")


def completion_diagnosis(value, payload, frames, verdict):
    needed = payload.get("review_context", {}).get("prior_audit_status") == "task_not_met"
    if not needed:
        require(value is None, "Completion diagnosis is only for task_not_met inputs")
        return
    require(isinstance(value, dict) and set(value) == {
        "visual_evidence", "requirement_evidence", "reason", "causes"}, "Missing completion diagnosis")
    evidence(value["visual_evidence"], frames)
    require(text(value["reason"]), "Missing completion diagnosis reason")
    causes = value["causes"]
    require(isinstance(causes, list) and causes and all(c in {
        "stage3_error", "instruction_video_mismatch", "audit_false_positive", "unresolved"} for c in causes),
        "Invalid completion cause")
    require(len(causes) == len(set(causes)), "Duplicate causes")
    if "unresolved" in causes:
        require(causes == ["unresolved"], "Unresolved cannot be mixed with confirmed causes")
    else:
        require(value["visual_evidence"], "Diagnosed cause requires supplied visual evidence")
    if "stage3_error" in causes:
        require(verdict == "incorrect", "Stage3 error cause conflicts with verdict")
    quotes = value["requirement_evidence"]
    require(isinstance(quotes, list), "Requirement evidence must be a list")
    for item in quotes:
        require(isinstance(item, dict) and set(item) == {"source", "quote"}, "Invalid requirement evidence")
        require(item["source"] in {"task", "review_standard"}, "Invalid requirement source")
        source = payload.get(item["source"])
        require(isinstance(source, str) and text(item["quote"]) and item["quote"] in source,
                "Requirement quote must match original task or standard")
    if any(c in causes for c in ("instruction_video_mismatch", "audit_false_positive")):
        require(quotes, "Task mismatch or false positive requires original requirement evidence")


def task_assessment(value, payload, frames):
    require(isinstance(value, dict) and set(value) == {
        "visual_evidence", "requirement_evidence", "reason", "status"}, "Bad task assessment fields")
    evidence(value["visual_evidence"], frames)
    require(text(value["reason"]), "Missing task assessment reason")
    require(value["status"] in {"met", "not_met", "uncertain"}, "Invalid task assessment status")
    quotes = value["requirement_evidence"]
    require(isinstance(quotes, list), "Task requirement evidence must be a list")
    for item in quotes:
        require(isinstance(item, dict) and set(item) == {"source", "quote"}, "Invalid task requirement evidence")
        require(item["source"] in {"task", "review_standard"}, "Invalid task requirement source")
        source = payload.get(item["source"])
        require(isinstance(source, str) and text(item["quote"]) and item["quote"] in source,
                "Task requirement quote must match original task or standard")
    if value["status"] != "uncertain":
        require(value["visual_evidence"] and quotes, "Task conclusion requires images and requirement quotes")


def timeline(rows, start, end, frames=None, strict=False):
    require(isinstance(rows, list) and rows, "Empty timeline")
    cursor = start
    sample_times = [f["timestamp_sec"] for f in frames] if frames else None
    for index, row in enumerate(rows):
        require(isinstance(row, dict), "Invalid subtask")
        if strict:
            require(set(row) == ROW_KEYS, "Generated subtask contains missing or stale fields")
        a, b = row.get("start_time_sec"), row.get("end_time_sec")
        require(number(a) and number(b) and b > a and abs(a - cursor) < 1e-6,
                "Timeline has invalid time, gap or overlap")
        require(b <= end + 1e-6, "Timeline exceeds writable end")
        if sample_times is not None and index > 0:
            require(any(abs(a - t) < 1e-6 for t in sample_times), "Internal boundary lacks supplied frame")
        require(row.get("segment_type") in {"operation", "waiting"}, "Bad segment type")
        for arm in ("arm_1", "arm_2"):
            value = row.get(arm)
            require(isinstance(value, dict) and set(value) == {"description", "outcome"}
                    and text(value["description"]), "Missing arm description")
            require(value["outcome"] in {"完成", "进行中", "未确认", "未参与"}, "Bad arm outcome")
        require(text(row.get("segment_summary")), "Missing segment summary")
        if strict:
            require(text(row["boundary_evidence"]), "Missing boundary evidence")
            require(row["uncertainty"] is None or text(row["uncertainty"]), "Invalid uncertainty")
            if row["segment_type"] == "waiting":
                require(text(row["external_event_description"]), "Waiting requires external event")
            else:
                require(row["external_event_description"] is None, "Non-waiting external event must be null")
        cursor = b
    require(abs(cursor - end) < 1e-6, "Timeline does not cover writable interval")


def generated(result, start, end, frames):
    require(isinstance(result, dict) and set(result) == {"subtasks"}, "Expected subtasks only")
    timeline(result["subtasks"], start, end, frames, strict=True)


def verification(result, rows, frames, payload=None):
    require(isinstance(result, dict) and set(result) == {
        "visual_evidence", "reason", "verification", "failed_subtasks", "issues",
        "task_assessment"}, "Bad verification fields")
    evidence(result["visual_evidence"], frames)
    task_assessment(result["task_assessment"], payload or {}, frames)
    require(text(result["reason"]), "Missing verification reason")
    status = result["verification"]
    require(status in {"pass", "retry", "manual_review"}, "Invalid verification verdict")
    allowed = [r["subtask_index"] for r in rows]
    ids(result["failed_subtasks"], allowed)
    require(isinstance(result["issues"], list), "issues must be list")
    if status == "pass":
        require(not result["failed_subtasks"] and not result["issues"] and result["visual_evidence"],
                "Pass cannot include unresolved issues and must cite frames")
    if status == "retry":
        require(result["failed_subtasks"] and result["issues"] and result["visual_evidence"],
                "Retry requires concrete errors and evidence")
    available = {f["frame_index"] for f in frames}
    issue_ids = []
    for item in result["issues"]:
        require(isinstance(item, dict) and set(item) == {"subtask_index", "problem", "frame_indices"},
                "Invalid verification issue")
        ids([item["subtask_index"]], result["failed_subtasks"])
        require(text(item["problem"]), "Missing verification problem")
        require(isinstance(item["frame_indices"], list) and item["frame_indices"]
                and all(type(f) is int and f in available for f in item["frame_indices"]),
                "Verification issue needs actual frame references")
        issue_ids.append(item["subtask_index"])
    if status == "retry":
        require(set(issue_ids) == set(result["failed_subtasks"]), "Failed subtasks lack issues")
