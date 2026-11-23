import copy
import json
import unittest

from ..correction.client import messages
from ..correction.io import read
from ..post_correction_review.runner import prepare, run, validate
from ..post_correction_review.sampling import sampling_plan
from . import test_framework as fixtures
from .test_framework import FakeClient, row, assessment


class SamplingTests(unittest.TestCase):
    def test_long_subtask_is_capped(self):
        plan = sampling_plan({"fps": 25, "length": 2500}, [row(0., 100., 7)])
        self.assertEqual(len(plan["frame_indices"]), 6)
        self.assertEqual(plan["frame_indices"][0], 0)
        self.assertEqual(plan["frame_indices"][-1], 2499)
        self.assertEqual(sum("interior" in f["roles"] for f in plan["subtasks"][0]["frames"]), 4)

    def test_short_subtasks_deduplicate_boundary_roles(self):
        plan = sampling_plan({"fps": 25, "length": 4}, [row(0., .04, 2), row(.04, .16, 9)])
        self.assertEqual(plan["frame_indices"], [0, 1, 3])
        self.assertEqual(plan["frame_refs"][0], [{"subtask_index": 2, "roles": ["start_boundary", "end_boundary"]}])
        self.assertEqual(plan["subtasks"][1]["frames"][1]["roles"], ["end_boundary"])

    def test_one_fps_before_capping_without_padding(self):
        for duration, expected in [(1, [0, 24]), (2, [0, 25, 49]),
                                   (3, [0, 25, 50, 74]), (5, [0, 25, 50, 75, 100, 124])]:
            with self.subTest(duration=duration):
                plan = sampling_plan({"fps": 25, "length": duration * 25}, [row(0., float(duration), 0)])
                self.assertEqual(plan["frame_indices"], expected)
        plan = sampling_plan({"fps": 25, "length": 250}, [row(0., 10., 0)])
        self.assertEqual(plan["frame_indices"], [0, 25, 75, 150, 225, 249])

    def test_sampling_grid_is_relative_to_subtask_start(self):
        plan = sampling_plan({"fps": 25, "length": 100}, [row(0., .52, 0), row(.52, 4., 1)])
        inner = [f["frame_index"] for f in plan["subtasks"][1]["frames"] if "interior" in f["roles"]]
        self.assertEqual(inner, [38, 63, 88])

    def test_single_interior_cap_and_invalid_fps(self):
        meta, rows = {"fps": 25, "length": 250}, [row(0., 10., 0)]
        self.assertEqual(sampling_plan(meta, rows, 1)["frame_indices"], [0, 125, 249])
        for fps in (0, -1, float("inf"), float("nan"), True, 30):
            with self.assertRaises(ValueError):
                sampling_plan(meta, rows, interior_fps=fps)

    def test_fractional_boundaries_and_disabled_interiors(self):
        plan = sampling_plan({"fps": 25, "length": 50}, [row(0., 1.01, 0), row(1.01, 2., 1)], 0)
        self.assertEqual(plan["frame_indices"], [0, 25, 26, 49])

    def test_rejects_missing_frames_ids_and_budget(self):
        meta = {"fps": 25, "length": 50}
        for count in (-1, 5, True, .5):
            with self.assertRaises(ValueError):
                sampling_plan(meta, [row(0., 2., 0)], count)
        with self.assertRaises(ValueError):
            sampling_plan(meta, [row(0., 2., 0)], 4, 2)
        with self.assertRaises(ValueError):
            sampling_plan(meta, [row(0., 1., 0), row(1., 2., 0)])


class PostReviewTests(unittest.TestCase):
    setUp = fixtures.FrameworkTests.setUp
    fake_extract = fixtures.FrameworkTests.fake_extract

    def test_payload_uses_only_corrected_text_and_frame_tags(self):
        payload, images, plan = prepare(self.job, self.rows, self.cfg, self.root / "cache", self.fake_extract)
        self.assertEqual(len(images), 4)
        self.assertEqual(payload["stage3_annotation"]["subtasks"], self.rows)
        for field in ("audit_feedback", "confirmed_problem", "task_not_met_diagnosis", "previous_candidate"):
            self.assertNotIn(field, payload)
        content = messages("prompt", payload, images)[1]["content"]
        label = json.loads(content[1]["text"].removeprefix("FRAME "))
        self.assertEqual(label["subtask_refs"], [{"subtask_index": 0, "roles": ["start_boundary"]}])
        self.assertEqual(content[2]["type"], "image_url")

    def test_dry_run_and_cached_review(self):
        client = FakeClient()
        folder = self.root / "review"
        preview = run(self.job, self.rows, self.cfg, client, folder, self.root / "cache", self.fake_extract, True)
        self.assertFalse(preview["api_called"])
        self.assertFalse(client.calls)
        checked = run(self.job, self.rows, self.cfg, client, folder, self.root / "cache", self.fake_extract)
        self.assertEqual(checked["verification"], "pass")
        run(self.job, self.rows, self.cfg, client, folder, self.root / "cache", self.fake_extract)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(read(folder / "sampling_plan.json")["frame_indices"],
                         [f["frame_index"] for f in preview["images"]])

    def test_evidence_request_is_not_error_and_has_exact_intervals(self):
        class NeedsEvidence(FakeClient):
            def complete(self, prompt, payload, images, stage):
                raw, stop = super().complete(prompt, payload, images, stage)
                value = json.loads(raw)
                value.update(verification="manual_review", failed_subtasks=[], issues=[],
                             evidence_requests=[{"subtask_indices": [1], "question": "核实中间是否发生交接"}])
                return json.dumps(value, ensure_ascii=False), stop
        folder = self.root / "review"
        checked = run(self.job, self.rows, self.cfg, NeedsEvidence(), folder, self.root / "cache", self.fake_extract)
        self.assertEqual(checked["failed_subtasks"], [])
        followup = read(folder / "followup_plan.json")
        self.assertEqual(followup["evidence_requests"][0]["intervals"],
                         [{"subtask_index": 1, "start_time_sec": 1., "end_time_sec": 2.}])

    def test_invalid_followups_are_rejected(self):
        payload, images, plan = prepare(self.job, self.rows, self.cfg, self.root / "cache", self.fake_extract)
        result = {"visual_evidence": [{"frame_index": 0, "observation": "可见物体"}], "reason": "核实",
                  "verification": "pass", "failed_subtasks": [], "issues": [], "evidence_requests": [],
                  "replacement_ranges": [], "task_assessment": assessment("met", self.job["task"])}
        validate(result, self.rows, images, payload)
        bad = copy.deepcopy(result)
        bad["evidence_requests"] = [{"subtask_indices": [0], "question": "核实"}]
        with self.assertRaises(ValueError):
            validate(bad, self.rows, images, payload)
        bad["verification"] = "manual_review"
        bad["evidence_requests"][0]["subtask_indices"] = [99]
        with self.assertRaises(ValueError):
            validate(bad, self.rows, images, payload)
