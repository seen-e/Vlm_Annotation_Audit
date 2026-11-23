"""Keep annotation accuracy, task satisfaction and execution policy independent."""


def summarize(verdict, annotation_reason, assessment):
    if verdict == "incorrect":
        status = "annotation_anomaly"
    elif verdict == "correct" and assessment["status"] == "met":
        status = "completed"
    elif verdict == "correct" and assessment["status"] == "not_met":
        status = "task_not_met"
    else:
        status = None
    return {"review_status": status, "needs_manual_review": status is None,
            "stage3_verdict": verdict, "stage3_reason": annotation_reason,
            "task_assessment": assessment}


def execution_plan(prior_status, decision, cfg):
    requested = prior_status in cfg.get("required_reannotation_statuses", [])
    confirmed = decision["stage3_verdict"] == "incorrect"
    if not confirmed and not requested:
        return None
    whole = prior_status in cfg.get("whole_episode_statuses", []) or not confirmed
    return {"scope": "whole_episode" if whole else decision["scope"],
            "replacement_ranges": [] if whole else decision["replacement_ranges"],
            "trigger": "requested_reannotation" if requested else "confirmed_stage3_error",
            "prior_audit_status": prior_status,
            "instruction": "根据视频重新生成Stage3，如实保留失败、重试和无法确认的状态；"
                           "旧审核状态不是视频事实，也不要求将结果改为成功。"}
