# Two-Round Keyframe Grounded Filter

该入口与父目录的一轮全证据审核并列，不覆盖原方案。

## 流程

1. Round 1 为每个 subtask 选择起始帧、结束帧和最多一张代表性过程帧；全 episode
   首尾帧始终保留，重复绝对帧自动去重。模型只定位有具体图像线索的疑点。
2. `pass` 不再调用模型。`needs_detail` 和 `uncertain` 进入 Round 2。
3. Round 2 保留全 episode 的紧凑帧，追加目标 subtask 的全部 Stage4 证据帧，并可追加
   相邻 subtask 的起止边界帧。第一轮反馈作为待验证疑点传入，不能当成事实。
4. Round 2 使用父模块的完整审核输出，区分标注异常和真实任务未满足。

每张图片前的 `VISUAL_FRAME` 标签直接包含 `frame_ref`、绝对 `frame_index`、
`timestamp_sec`、所属 subtask 和证据角色。待审核的帧描述仍在结构化 payload 中，
不会紧贴图片重复，以免模型照抄候选文本。

## 输出

```text
<output_root>/<episode_id>/
  round1/input.json
  round1/output.json
  round1/attempt_*/...
  round2/input.json       # 仅需要精查时出现
  round2/output.json
  final.json
```

`final.json` 中 `audit_route` 为 `round1_only` 或 `round2_completed`。第一轮通过使用
`final_status=screened_pass`，表示紧凑筛查未发现具体疑点，不冒充完整视觉审核的
`normal`。进入第二轮后，`final_status` 使用完整审核的四种状态。

## 运行

提示词当前仍是草案，只允许显式小样本使用 `--pilot`。先执行 dry-run：

```bash
cd '/mnt/workspace/Subtask_Annotation _Review'
python -m annotatiuon_audit.keyframe_grounded_filter.two_round.run \
  --config /path/to/two_round_pilot.yaml --dry-run

python -m annotatiuon_audit.keyframe_grounded_filter.two_round.run \
  --config /path/to/two_round_pilot.yaml --pilot
```

测试：

```bash
python -m unittest \
  annotatiuon_audit.keyframe_grounded_filter.test_pipeline \
  annotatiuon_audit.keyframe_grounded_filter.two_round.test_two_round -v
```
