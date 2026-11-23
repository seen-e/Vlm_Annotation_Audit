# Text-First Two-Round Audit

该入口与父目录的一轮全证据审核并列，不覆盖原方案。核心原则是：文本是主要审核
对象，只有文本无法裁决具体核心事实时才看图。

## 流程

1. Round 1 发送 `review_standard`、任务、最终 Stage3 的精简 `subtasks[]` 和 Stage4
   候选文本。每个 Stage3 subtask 只保留原始编号、`segment_type`、两臂的
   `description/outcome` 和 `segment_summary`；不发送 sample 边界及 final.json 顶层的
   `review`、`arm_registry`、`episode_success`、`terminal_summary`。`images=[]`，payload
   中也不含 `visual_frames`。
2. 模型必须按输入顺序审核每个 subtask，并为每段引用至少一条可校验的原文。
3. 明确文本矛盾直接输出 `annotation_anomaly`；文本自洽地记录真实失败时输出
   `task_not_met`。两者都不为确认既有文本结论而额外看图。
4. 只有 `needs_visual` 段可以生成具体 `visual_requests`。Round 2 仅发送这些段的
   Stage4 证据帧；相邻边界帧默认关闭，可通过配置启用。
5. Round 2 逐条回答第一轮问题，程序合并文本结论与视觉裁决，生成最终状态。

## 输出

```text
<output_root>/<episode_id>/
  round1/input.json       # images 必须为空
  round1/output.json      # 完整逐段文本审核
  round1/attempt_*/...
  round2/input.json       # 仅 visual_requests 非空时出现
  round2/output.json      # 只回答定向视觉问题
  final.json
```

`final.json.audit_route` 为 `text_only` 或 `targeted_visual_resolution`；最终状态始终是
`normal`、`annotation_anomaly`、`task_not_met`、`uncertain` 之一。

## 运行

提示词仍是实验草案，只允许显式小样本使用 `--pilot`：

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
