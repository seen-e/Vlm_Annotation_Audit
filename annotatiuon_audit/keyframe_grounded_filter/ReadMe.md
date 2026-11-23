# Keyframe Grounded Filter

该模块与 `episode_level_filter` 并列：前者使用最终标注文本和 Stage4 关键帧图像做
多模态审核，后者保持原来的纯文本审核。两者使用不同输出目录，不相互覆盖。

## 审核目标

模块分别判断：

1. `annotation_visual_status`：最终标注和 Stage4 关键帧文字是否符合对应画面。
2. `criterion_status`：画面支持的实际结果是否满足该 episode 的审核标准。

真实执行失败但标注准确时输出 `task_not_met`，不自动要求重标；只有标注或关键帧
与画面不一致时才使用 `annotation_anomaly` 和局部/整集重标范围。

## 输入

- `standards_path`：已经展开到每个 episode 的有效审核标准。
- `annotations_root`：待审核的最终 `final.json`。
- `keyframe_roots`：可以配置多个 Stage4 实例目录，但每个 episode 必须只匹配一个。
- 每个 Stage4 episode 目录需要 `keyframes.json`、`sample_manifest.json` 和
  `stage4_keyframes/frames/{frame_index:08d}.jpg`。

程序读取起始帧、结束帧、过程关键帧、各 evidence frame、首次成功帧、成功审核和
边界审核引用的帧，按绝对 frame_index 去重。每张图片发送前都有包含 frame_ref 和
frame_index 的文本标签。所有候选关键帧字段均作为待审核文本，不能当成视觉事实。

缺帧、重复 Stage4 来源、subtask 数量不一致、图片过多、正文过长或路径越界都会在
API 调用前失败，不静默裁切。

## 当前状态

`prompts.py` 是第一版完整提示词，`APPROVED=False`。因此目前只允许 dry-run，避免在
共同审核提示词和输出结构前误启动付费批量任务。用户明确授权的小样本可以使用
`--pilot`，但配置必须给出非空 `episode_ids` 且不超过 `pilot_max_episodes`；默认空列表
不能借此启动全量请求。

```bash
cd '/mnt/workspace/Subtask_Annotation _Review'
python -m annotatiuon_audit.keyframe_grounded_filter.run --dry-run
python -m annotatiuon_audit.keyframe_grounded_filter.run --config /path/to/pilot.yaml --pilot
python -m unittest annotatiuon_audit.keyframe_grounded_filter.test_pipeline -v
```

提示词审核通过后，将 `APPROVED=True`，为实验设置新的 `output_root`，先限制
`episode_ids` 跑小样本，再执行正式命令：

```bash
python -m annotatiuon_audit.keyframe_grounded_filter.run
```

## 输出

每个 episode 保存输入签名、文本 payload、图片 SHA256、每次请求清单、原始响应和
校验后的 `output.json`。根目录分别汇总：

- `annotation_anomaly.json`：标注或关键帧与图像不一致，需要重标。
- `task_not_met.json`：实际任务未满足，但标注没有被画面否定。
- `uncertain.json`：离散关键帧不足或存在无法排除的歧义。
- `progress.json`、`failures.json`：执行进度和请求/结构失败。

图片二进制不会写进 `input.json` 或 `messages_manifest.json`；输出保存来源路径、发送
顺序及 SHA256，API 请求时再读取并核验图片未发生变化。

## 自适应二轮方案

`two_round/` 提供文本优先的并列入口。第一轮不发送图像，最终 Stage3 只输入
`subtasks[]`，不输入 final.json 顶层的审核、机械臂注册、episode成功和终态字段；
Stage4 候选文本仍保留。只有无法从文本裁决的具体核心声明才进入第二轮，第二轮仅发送
目标 subtask 的证据帧。详细配置、输出目录和命令见 `two_round/ReadMe.md`。
