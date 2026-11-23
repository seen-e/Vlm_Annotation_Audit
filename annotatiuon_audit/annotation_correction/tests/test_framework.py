import copy
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from ..correction import schema
from ..correction.client import request, messages
from ..correction.data import audit_hints, normalize, select_ids
from ..correction.io import read, save
from ..correction.media import frame_indices, extract, subtask_boundaries
from ..correction.merge import merge, regions
from ..correction.pipeline import process, PROMPTS
from ..correction.review import summarize

ROOT = Path(__file__).resolve().parents[1]


def row(start=0., end=1., sid=None):
    value = {"start_time_sec": start, "end_time_sec": end, "segment_type": "operation",
             "arm_1": {"description": "移动红色物体", "outcome": "完成"},
             "arm_2": {"description": "未参与", "outcome": "未参与"},
             "segment_summary": "移动红色物体", "boundary_evidence": "frame 0显示操作起始",
             "external_event_description": None, "uncertainty": None}
    if sid is not None:
        value["subtask_index"] = sid
    return value


def assessment(status="uncertain", quote=None):
    return {"visual_evidence": [{"frame_index": 0, "observation": "可见操作与目标状态"}],
            "requirement_evidence": [{"source": "task", "quote": quote}] if quote else [],
            "reason": "根据图像与明确要求独立判断", "status": status}


def decision(verdict="incorrect", scope="partial"):
    return {"visual_evidence": [{"frame_index": 0, "observation": "实际操作蓝色物体"}],
            "reason": "Stage3物体描述不符", "stage3_verdict": verdict,
            "error_types": ["object_error"] if verdict == "incorrect" else [],
            "target_subtasks": [0] if verdict == "incorrect" else [],
            "scope": scope if verdict == "incorrect" else "none", "scope_reason": "错误局限于首段",
            "replacement_ranges": [{"first_subtask_index": 0, "last_subtask_index": 0}]
              if verdict == "incorrect" and scope == "partial" else [],
            "reannotation_instruction": "核实蓝色物体" if verdict == "incorrect" else None,
            "task_not_met_diagnosis": None, "task_assessment": assessment()}


class FakeClient:
    def __init__(self, result=None, verify="pass", task_status="met", final_task_status=None):
        self.auth = {"model": "mock", "base_url": "mock", "api_key": "not-real"}
        self.cfg = {"temperature": 0, "max_tokens": 5000, "stream": False,
                    "attempts": 1, "retry_delay_sec": 0}
        self.result = result or decision()
        self.verify = verify
        self.task_status = task_status
        self.final_task_status = final_task_status or task_status
        self.calls = []

    def complete(self, prompt, payload, images, stage):
        name = next(k for k, v in PROMPTS.items() if v == prompt)
        self.calls.append((name, copy.deepcopy(payload)))
        if name == "adjudication":
            result = copy.deepcopy(self.result)
            result["task_assessment"] = assessment(self.task_status, payload["task"])
            if payload.get("review_context", {}).get("prior_audit_status") == "task_not_met":
                cause = {"incorrect": "stage3_error", "correct": "audit_false_positive",
                         "insufficient_evidence": "unresolved"}[result["stage3_verdict"]]
                result["task_not_met_diagnosis"] = {
                    "visual_evidence": result["visual_evidence"],
                    "requirement_evidence": [{"source": "review_standard", "quote": payload["review_standard"]}],
                    "reason": "根据视频重新核实旧结论", "causes": [cause]}
        elif name == "verification":
            result = {"visual_evidence": [{"frame_index": 0, "observation": "可见操作"}],
                      "reason": "独立验收", "verification": self.verify,
                      "failed_subtasks": [] if self.verify == "pass" else [0],
                      "issues": [] if self.verify == "pass" else [
                          {"subtask_index": 0, "problem": "对象仍不符", "frame_indices": [0]}],
                      "task_assessment": assessment(self.final_task_status, payload["task"]),
                      "evidence_requests": [],
                      "replacement_ranges": [{"first_subtask_index": 0, "last_subtask_index": 0}]
                          if self.verify == "retry" else []}
        else:
            result = {"subtasks": [row(*payload["write_interval"])]}
            result["subtasks"][0]["segment_summary"] = "移动蓝色物体"
            result["subtasks"][0]["arm_1"]["description"] = "移动蓝色物体"
        return json.dumps(result, ensure_ascii=False), "stop"


class FrameworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / "frame.jpg"
        self.image.write_bytes(b"test-image")
        self.frames = [{"frame_index": 0, "source_frame_index": 20,
                        "timestamp_sec": 0., "path": str(self.image)}]
        self.rows = [row(0., 1., 0), row(1., 2., 1)]
        self.cfg = yaml.safe_load((ROOT / "config/config.yaml").read_text(encoding="utf-8"))
        self.cfg["output_root"] = str(self.root / "out")
        self.job = {"episode_id": "test", "original": {"subtasks": self.rows}, "rows": self.rows,
                    "audit": {}, "feedback": [], "task": "移动物体", "review_standard": "如实描述",
                    "duration": 2., "meta": {"fps": 25, "length": 50, "first_source_frame": 20}, "video_identity": {}}

    def fake_extract(self, *args):
        job = args[0]
        if "requested_frame_indices" in job:
            return [{"frame_index": i, "timestamp_sec": i / job["meta"]["fps"],
                     "source_frame_index": job["meta"]["first_source_frame"] + i, "path": str(self.image)}
                    for i in job["requested_frame_indices"]]
        return self.frames

    def test_complete_partial_correction_and_resume(self):
        client = FakeClient()
        result = process(self.job, self.cfg, client, "code", extract_frames=self.fake_extract)
        self.assertEqual(result["status"], "corrected")
        folder = Path(self.cfg["output_root"]) / "test"
        final = read(folder / "final_corrected_stage3.json")
        self.assertEqual(final["subtasks"][1], self.rows[1])
        self.assertEqual(final["subtasks"][0]["segment_summary"], "移动蓝色物体")
        self.assertEqual([name for name, _ in client.calls], ["adjudication", "plan", "refine", "finalize", "verification"])
        verify_payload = client.calls[-1][1]
        self.assertNotIn("confirmed_problem", verify_payload)
        self.assertNotIn("audit_feedback", verify_payload)
        self.assertNotIn("task_not_met_diagnosis", verify_payload)
        self.assertNotIn("keyframe_annotation", json.dumps(client.calls))
        process(self.job, self.cfg, client, "code", extract_frames=self.fake_extract)
        self.assertEqual(len(client.calls), 5)

    def test_review_category_matrix(self):
        for verdict in ("correct", "incorrect", "insufficient_evidence"):
            for task_status in ("met", "not_met", "uncertain"):
                with self.subTest(verdict=verdict, task_status=task_status):
                    expected = "annotation_anomaly" if verdict == "incorrect" else (
                        {"met": "completed", "not_met": "task_not_met", "uncertain": None}[task_status]
                        if verdict == "correct" else None)
                    result = summarize(verdict, "证据判断", assessment(task_status))
                    self.assertEqual(result["review_status"], expected)
                    self.assertEqual(result["needs_manual_review"], expected is None)

    def test_uncertain_input_has_independent_initial_and_final_review(self):
        self.job["audit"] = {"final_status": "uncertain"}
        for verdict, task_status, expected in [
            ("correct", "met", "completed"), ("correct", "not_met", "task_not_met"),
            ("incorrect", "met", "annotation_anomaly"), ("insufficient_evidence", "met", None),
            ("correct", "uncertain", None)]:
            with self.subTest(verdict=verdict, task_status=task_status):
                self.cfg["output_root"] = str(self.root / (verdict + task_status))
                client = FakeClient(decision(verdict), task_status=task_status, final_task_status="met")
                result = process(self.job, self.cfg, client, "code", extract_frames=self.fake_extract)
                folder = Path(self.cfg["output_root"]) / "test"
                before, after = read(folder / "initial_review.json"), read(folder / "final_review.json")
                self.assertEqual(before["review_status"], expected)
                self.assertEqual(after["review_status"], "completed")
                self.assertEqual(before["annotation_path"], "stage3_input.json")
                self.assertEqual(after["annotation_path"], "final_corrected_stage3.json")
                self.assertEqual(result["scope"], "whole_episode")
                self.assertEqual(len(client.calls), 5)
                self.assertNotIn("initial_review", client.calls[-1][1])

    def test_accurate_annotation_can_pass_when_task_unmet_or_unknown(self):
        self.job["audit"] = {"final_status": "uncertain"}
        for task_status, expected in [("not_met", "task_not_met"), ("uncertain", None)]:
            self.cfg["output_root"] = str(self.root / task_status)
            client = FakeClient(decision("correct"), task_status=task_status)
            result = process(self.job, self.cfg, client, "code", extract_frames=self.fake_extract)
            self.assertEqual(result["status"], "reannotated")
            self.assertEqual(result["review_status"], expected)
            self.assertEqual(result["needs_manual_review"], task_status == "uncertain")
            self.assertEqual(len(client.calls), 5)
            self.assertTrue((Path(self.cfg["output_root"]) / "test/final_corrected_stage3.json").exists())

    def test_remaining_annotation_error_not_hidden_by_completed_task(self):
        self.job["audit"] = {"final_status": "uncertain"}
        result = process(self.job, self.cfg, FakeClient(verify="retry", task_status="met"),
                         "code", extract_frames=self.fake_extract)
        self.assertEqual(result["status"], "manual_review")
        self.assertEqual(result["review_status"], "annotation_anomaly")
        final = read(Path(self.cfg["output_root"]) / "test/final_review.json")
        self.assertTrue(final["needs_manual_review"])
        self.assertIn("candidate_stage3.json", final["annotation_path"])
        self.assertFalse((Path(self.cfg["output_root"]) / "test/final_corrected_stage3.json").exists())

    def test_task_assessment_requires_grounded_evidence(self):
        payload = {"task": "移动物体", "review_standard": "如实描述"}
        for status in ("met", "not_met"):
            valid = assessment(status, "移动物体")
            schema.task_assessment(valid, payload, self.frames)
            for field in ("visual_evidence", "requirement_evidence"):
                bad = copy.deepcopy(valid)
                bad[field] = []
                with self.assertRaises(ValueError):
                    schema.task_assessment(bad, payload, self.frames)
            bad = copy.deepcopy(valid)
            bad["requirement_evidence"][0]["quote"] = "未提供的要求"
            with self.assertRaisesRegex(ValueError, "quote"):
                schema.task_assessment(bad, payload, self.frames)
            bad = copy.deepcopy(valid)
            bad["visual_evidence"][0]["frame_index"] = 999
            with self.assertRaises(ValueError):
                schema.task_assessment(bad, payload, self.frames)

    def test_changed_final_review_rejected_on_resume(self):
        process(self.job, self.cfg, FakeClient(), "code", extract_frames=self.fake_extract)
        path = Path(self.cfg["output_root"]) / "test/final_review.json"
        review = read(path)
        review["review_status"] = "task_not_met"
        save(path, review)
        with self.assertRaisesRegex(ValueError, "Final review changed"):
            process(self.job, self.cfg, FakeClient(), "code", extract_frames=self.fake_extract)

    def test_whole_episode(self):
        result = process(self.job, self.cfg, FakeClient(decision(scope="whole_episode")),
                         "code", extract_frames=self.fake_extract)
        final = read(Path(self.cfg["output_root"]) / "test/final_corrected_stage3.json")
        self.assertEqual(result["scope"], "whole_episode")
        self.assertEqual(len(final["subtasks"]), 1)
        self.assertEqual(final["subtasks"][0]["end_time_sec"], 2.)

    def test_false_positive_and_unknown_do_not_reannotate(self):
        for status, expected in [("correct", "no_change"), ("insufficient_evidence", "manual_review")]:
            self.cfg["output_root"] = str(self.root / status)
            client = FakeClient(decision(status))
            result = process(self.job, self.cfg, client, "code", extract_frames=self.fake_extract)
            self.assertEqual(result["status"], expected)
            self.assertEqual(len(client.calls), 1)
            self.assertFalse((Path(self.cfg["output_root"]) / "test/final_corrected_stage3.json").exists())

    def test_task_not_met_and_uncertain_always_regenerate(self):
        for prior in ("task_not_met", "uncertain"):
            for verdict in ("correct", "insufficient_evidence", "incorrect"):
                self.cfg["output_root"] = str(self.root / (prior + verdict))
                self.job["audit"] = {"final_status": prior}
                client = FakeClient(decision(verdict))
                result = process(self.job, self.cfg, client, "code", extract_frames=self.fake_extract)
                self.assertEqual(result["status"], "corrected" if verdict == "incorrect" else "reannotated")
                self.assertEqual(result["scope"], "whole_episode")
                self.assertEqual(result["trigger"], "requested_reannotation")
                self.assertEqual(len(client.calls), 5)
                self.assertEqual(client.calls[1][1]["write_interval"], [0., 2.])
                self.assertEqual("confirmed_problem" in client.calls[1][1], verdict == "incorrect")
                saved = read(Path(self.cfg["output_root"]) / "test/adjudication.json")
                self.assertEqual(saved["stage3_verdict"], verdict)
                if prior == "task_not_met":
                    self.assertIsNotNone(saved["task_not_met_diagnosis"])
                    self.assertIn("task_not_met_diagnosis", client.calls[1][1])
                    self.assertNotIn("task_not_met_diagnosis", client.calls[-1][1])
                process(self.job, self.cfg, client, "code", extract_frames=self.fake_extract)
                self.assertEqual(len(client.calls), 5)

    def test_requested_reannotation_still_requires_verification(self):
        self.job["audit"] = {"final_status": "uncertain"}
        client = FakeClient(decision("insufficient_evidence"), verify="manual_review")
        result = process(self.job, self.cfg, client, "code", extract_frames=self.fake_extract)
        self.assertEqual(result["status"], "manual_review")
        self.assertEqual(len(client.calls), 5)
        self.assertFalse((Path(self.cfg["output_root"]) / "test/final_corrected_stage3.json").exists())

    def test_anomaly_always_regenerates_with_local_or_whole_scope(self):
        self.job["audit"] = {"final_status": "annotation_anomaly"}
        for verdict in ("correct", "incorrect", "insufficient_evidence"):
            self.cfg["output_root"] = str(self.root / ("anomaly_" + verdict))
            client = FakeClient(decision(verdict))
            result = process(self.job, self.cfg, client, "code", extract_frames=self.fake_extract)
            self.assertIn(result["status"], {"corrected", "reannotated"})
            self.assertEqual(result["scope"], "partial" if verdict == "incorrect" else "whole_episode")
            self.assertEqual([name for name, _ in client.calls],
                             ["adjudication", "plan", "refine", "finalize", "verification"])
            final = read(Path(self.cfg["output_root"]) / "test/final_corrected_stage3.json")
            self.assertEqual(len(final["subtasks"]), 2 if verdict == "incorrect" else 1)

    def test_bounded_retry_never_publishes_failed_output(self):
        self.cfg["max_correction_retries"] = 1
        client = FakeClient(verify="retry")
        result = process(self.job, self.cfg, client, "code", extract_frames=self.fake_extract)
        self.assertEqual(result["status"], "manual_review")
        self.assertEqual(sum(name == "verification" for name, _ in client.calls), 2)
        self.assertFalse((Path(self.cfg["output_root"]) / "test/final_corrected_stage3.json").exists())

    def test_three_iteration_limit_and_early_pass(self):
        self.cfg["max_correction_retries"] = 2
        client = FakeClient(verify="retry")
        result = process(self.job, self.cfg, client, "code", extract_frames=self.fake_extract)
        self.assertEqual(result["correction_attempts"], 3)
        self.assertEqual(sum(name == "verification" for name, _ in client.calls), 3)
        self.assertEqual(sum(name == "adjudication" for name, _ in client.calls), 1)
        self.assertEqual(result["status"], "manual_review")
        self.cfg["output_root"] = str(self.root / "early_pass")
        client = FakeClient()
        result = process(self.job, self.cfg, client, "code", extract_frames=self.fake_extract)
        self.assertEqual(result["attempt"], 1)
        self.assertEqual(sum(name == "verification" for name, _ in client.calls), 1)

    def test_episode_ids_file_and_explicit_override(self):
        path = self.root / "selected.json"
        save(path, ["robodojo_episode_000001", "robodojo_episode_000002"])
        self.cfg.update(episode_ids=[], episode_ids_file=str(path))
        self.assertEqual(select_ids(self.cfg), read(path))
        self.cfg["episode_ids"] = ["explicit"]
        self.assertEqual(select_ids(self.cfg), ["explicit"])
        self.cfg["episode_ids"] = []
        save(path, ["duplicate", "duplicate"])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            select_ids(self.cfg)

    def test_changed_input_cannot_reuse_results(self):
        process(self.job, self.cfg, FakeClient(), "code", extract_frames=self.fake_extract)
        self.job["task"] = "另一个任务"
        with self.assertRaisesRegex(ValueError, "changed"):
            process(self.job, self.cfg, FakeClient(), "code", extract_frames=self.fake_extract)

    def test_tampered_final_rejected(self):
        process(self.job, self.cfg, FakeClient(), "code", extract_frames=self.fake_extract)
        path = Path(self.cfg["output_root"]) / "test/final_corrected_stage3.json"
        final = read(path)
        final["subtasks"][0]["segment_summary"] = "tampered"
        save(path, final)
        with self.assertRaisesRegex(ValueError, "changed"):
            process(self.job, self.cfg, FakeClient(), "code", extract_frames=self.fake_extract)

    def test_split_merge_mapping_and_unchanged_neighbors(self):
        selected = regions(self.rows, decision(), 2., 1, 2.)
        out, record = merge(self.rows, [(selected[0], [row(0., .5), row(.5, 1.)])], 2.)
        self.assertEqual(record["old_to_new_mapping"], {"0": [0, 1], "1": [2]})
        self.assertEqual(out[2]["segment_summary"], self.rows[1]["segment_summary"])
        self.assertEqual(selected[0]["write_interval"], [0., 1.])
        self.assertEqual(selected[0]["observe_interval"], [0, 2.])

    def test_gap_overlap_and_nonfinite_rejected(self):
        for start, end in [(1.1, 2.), (.9, 2.), (1., float("nan"))]:
            with self.assertRaises(ValueError):
                schema.timeline([row(0., 1.), row(start, end)], 0., 2.)

    def test_invalid_evidence_and_scope_rejected(self):
        d = decision()
        d["visual_evidence"][0]["frame_index"] = 999
        with self.assertRaises(ValueError):
            schema.adjudication(d, self.rows, self.frames)

    def test_unmet_requires_diagnosis_and_exact_requirement_quote(self):
        payload = {"review_context": {"prior_audit_status": "task_not_met"},
                   "task": "排列字母", "review_standard": "按要求排列字母"}
        d = decision("correct")
        with self.assertRaisesRegex(ValueError, "Missing completion diagnosis"):
            schema.adjudication(d, self.rows, self.frames, payload)
        d["task_not_met_diagnosis"] = {
            "visual_evidence": [{"frame_index": 0, "observation": "视频中实际为其他排列"}],
            "requirement_evidence": [{"source": "task", "quote": "排列字母"}],
            "reason": "原要求与视频不同，Stage3如实描述", "causes": ["instruction_video_mismatch"]}
        schema.adjudication(d, self.rows, self.frames, payload)
        d["task_not_met_diagnosis"]["requirement_evidence"][0]["quote"] = "不存在的要求"
        with self.assertRaisesRegex(ValueError, "quote"):
            schema.adjudication(d, self.rows, self.frames, payload)

    def test_completion_diagnosis_cannot_invent_evidence_or_contradict_verdict(self):
        payload = {"review_context": {"prior_audit_status": "task_not_met"}}
        d = decision("correct")
        d["task_not_met_diagnosis"] = {"visual_evidence": [], "requirement_evidence": [],
                                        "reason": "猜测", "causes": ["stage3_error"]}
        with self.assertRaises(ValueError):
            schema.adjudication(d, self.rows, self.frames, payload)
        d["task_not_met_diagnosis"]["visual_evidence"] = d["visual_evidence"]
        with self.assertRaisesRegex(ValueError, "conflicts"):
            schema.adjudication(d, self.rows, self.frames, payload)
        d = decision()
        d["replacement_ranges"][0]["last_subtask_index"] = 999
        with self.assertRaises(ValueError):
            schema.adjudication(d, self.rows, self.frames)

    def test_stage4_feedback_is_removed(self):
        audit = {"round1": {"subtask_reviews": [{"subtask_index": 0, "text_status": "inconsistent",
                  "reason": "stage4 says otherwise", "text_evidence": [
                  {"source": "keyframe_annotation", "path": "/subtasks/0/result", "quote": "stage4-secret"},
                  {"source": "final_annotation", "path": "/subtasks/0/segment_summary", "quote": "移动红色物体"}]}]}}
        hints = audit_hints(audit, {"subtasks": self.rows})
        self.assertEqual(len(hints), 1)
        self.assertNotIn("stage4", json.dumps(hints))
        self.rows[0]["segment_summary"] = "changed"
        with self.assertRaises(ValueError):
            audit_hints(audit, {"subtasks": self.rows})

    def test_manifest_not_fps_guess_for_old_boundaries(self):
        raw = [row(), row()]
        for r, a, b in zip(raw, [0, 1], [1, 3]):
            r.pop("start_time_sec"); r.pop("end_time_sec")
            r.update(start_sample_index=a, end_sample_index_exclusive=b)
        manifest = {"samples": [{"timestamp_sec": 0.}, {"timestamp_sec": .52}, {"timestamp_sec": 1.}]}
        normalized = normalize({"subtasks": raw}, manifest, 1.24)
        self.assertEqual(normalized[0]["end_time_sec"], .52)
        self.assertEqual(normalized[-1]["end_time_sec"], 1.24)

    def test_sampling_has_episode_last_frame_and_rejects_budget(self):
        meta = {"fps": 25, "length": 657}
        sampled = frame_indices(meta, 0, 657/25, 2, 1000)
        self.assertEqual(sampled[0], 0)
        self.assertEqual(sampled[-1], 656)
        with self.assertRaisesRegex(ValueError, "budget"):
            frame_indices(meta, 0, 657/25, 2, 20)

    def test_sparse_sampling_preserves_both_sides_of_subtask_boundaries(self):
        meta = {"fps": 25, "length": 100}
        rows = [row(0., 1.52), row(1.52, 2.8), row(2.8, 4.)]
        boundaries = subtask_boundaries(meta, rows)
        self.assertEqual(boundaries, {0, 37, 38, 69, 70, 99})
        for fps in (1, .5):
            indices = frame_indices(meta, 0, 4, fps, 100, boundaries)
            self.assertTrue(boundaries <= set(indices))
            self.assertEqual(indices, sorted(set(indices)))
        cropped = frame_indices(meta, 1.52, 2.8, 1, 100, boundaries)
        self.assertIn(38, cropped)
        self.assertIn(69, cropped)
        self.assertNotIn(37, cropped)
        self.assertNotIn(70, cropped)
        with self.assertRaisesRegex(ValueError, "budget"):
            frame_indices(meta, 0, 4, .5, 4, boundaries)

    def test_boundary_frames_use_actual_frame_grid(self):
        meta = {"fps": 25, "length": 100}
        self.assertEqual(subtask_boundaries(meta, [row(.01, .19)]), {1, 4})
        self.assertEqual(subtask_boundaries(meta, [row(.28, .32)]), {7})
        with self.assertRaisesRegex(ValueError, "no source frame"):
            subtask_boundaries(meta, [row(.01, .02)])

    def test_verification_sampling_uses_corrected_subtasks(self):
        seen = []
        def capture(job, start, end, fps, cfg, cache):
            seen.append((fps, copy.deepcopy(job["sampling_subtasks"])))
            return self.fake_extract(job, start, end, fps, cfg, cache)
        process(self.job, self.cfg, FakeClient(decision(scope="whole_episode")),
                "code", extract_frames=capture)
        self.assertEqual(seen[0][0], 1)
        self.assertEqual(seen[0][1], self.rows)
        self.assertEqual(seen[-1][0], 1)
        self.assertEqual(len(seen[-1][1]), 1)
        self.assertEqual(seen[-1][1][0]["end_time_sec"], 2.)
        self.assertEqual(seen[1][0], 2)
        self.assertTrue(any(r["end_time_sec"] == 2. for r in seen[2][1]))

    def test_video_offset_is_applied(self):
        import cv2
        import numpy as np
        video = self.root / "shared.avi"
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 48))
        self.assertTrue(writer.isOpened())
        for n in range(30):
            writer.write(np.full((48, 64, 3), n*7, dtype=np.uint8))
        writer.release()
        job = {"meta": {"source_video": str(video), "fps": 10, "length": 10,
                        "first_source_frame": 10, "last_source_frame": 19}, "video_identity": {}}
        images = extract(job, 0, 1, 2, self.cfg["frames"], self.root / "cache")
        self.assertEqual(images[0]["source_frame_index"], 10)
        self.assertAlmostEqual(float(cv2.imread(images[0]["path"]).mean()), 70., delta=3)
        self.assertEqual(images[-1]["source_frame_index"], 19)

    def test_frame_tag_precedes_every_image(self):
        content = messages("prompt", {}, self.frames)[1]["content"]
        self.assertEqual(content[1]["type"], "text")
        self.assertIn('"source_frame_index": 20', content[1]["text"])
        self.assertEqual(content[2]["type"], "image_url")

    def test_disjoint_ranges_preserve_middle_segment(self):
        rows = [row(float(i), float(i+1), i) for i in range(3)]
        d = decision()
        d["target_subtasks"] = [0, 2]
        d["replacement_ranges"].append({"first_subtask_index": 2, "last_subtask_index": 2})
        schema.adjudication(d, rows, self.frames)
        selected = regions(rows, d, 3., 1, 2.)
        output, record = merge(rows, [(r, [row(*r["write_interval"])]) for r in selected], 3.)
        self.assertEqual(output[1], rows[1])
        self.assertEqual(record["unchanged_count"], 1)

    def test_verifier_cannot_expand_local_scope(self):
        class OutsideClient(FakeClient):
            def complete(self, prompt, payload, images, stage):
                raw, finish = super().complete(prompt, payload, images, stage)
                if prompt == PROMPTS["verification"]:
                    value = json.loads(raw)
                    value["failed_subtasks"] = [1]
                    value["issues"][0]["subtask_index"] = 1
                    value["replacement_ranges"] = [{"first_subtask_index": 1, "last_subtask_index": 1}]
                    raw = json.dumps(value)
                return raw, finish
        client = OutsideClient(verify="retry")
        result = process(self.job, self.cfg, client, "code", extract_frames=self.fake_extract)
        self.assertEqual(result["status"], "manual_review")
        self.assertEqual(len(client.calls), 5)

    def test_partial_failure_resumes_successful_stage_requests(self):
        class FailingClient(FakeClient):
            def complete(self, prompt, payload, images, stage):
                if prompt == PROMPTS["refine"]:
                    raise RuntimeError("simulated interruption")
                return super().complete(prompt, payload, images, stage)
        with self.assertRaises(RuntimeError):
            process(self.job, self.cfg, FailingClient(), "code", extract_frames=self.fake_extract)
        resumed = FakeClient()
        result = process(self.job, self.cfg, resumed, "code", extract_frames=self.fake_extract)
        self.assertEqual(result["status"], "corrected")
        self.assertEqual([name for name, _ in resumed.calls], ["refine", "finalize", "verification"])

    def test_dry_run_never_calls_model(self):
        client = FakeClient()
        result = process(self.job, self.cfg, client, "code", True, self.fake_extract)
        self.assertEqual(result["status"], "preflight_ok")
        self.assertFalse(client.calls)


if __name__ == "__main__":
    unittest.main()
