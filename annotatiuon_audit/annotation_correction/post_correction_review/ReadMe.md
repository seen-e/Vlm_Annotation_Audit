# 校正后整集图文审核

部署位置：`/mnt/workspace/Subtask_Annotation _Review/annotatiuon_audit/annotation_correction/post_correction_review`（1015）。
本模块审核完整的校正后Stage3，用每个subtask的少量图像辅助检查文本、衔接、遗漏及任务结果。
它替换原pipeline中按固定FPS对整集输入的verification，不额外叠加一次相同审核。
独立提示词在`../prompts/verification.py`，不拼接其他阶段提示词。

## 输入与抽帧

- 原始任务、审核标准，以及校正后完整subtask列表，按episode绝对秒给出起止时间。
- 每段取起点及之后第一张原始帧、终点之前最后一张原始帧（半开区间）。
- 内部从段起点每隔1秒采样（默认1 fps），排除首尾边界帧；超过4张才从候选帧均匀选4张。
- 不足4张保留实际数量，不加密补齐；首尾边界另加，每段最多6张。
- 一帧同时作为首尾边界时只输入一次，但保留两个角色。所有帧按绝对帧号排序并去重。
- `subtask_frames`说明每段对应哪些帧。每张图前FRAME标签带`frame_index`、`timestamp_sec`、
  `source_frame_index`、`subtask_refs`；refs内是当前subtask编号与start_boundary/end_boundary/interior角色。
- 不传入旧审核理由、旧标注全文、重标解释或Stage4文本。旧sample映射仅用于解释新文本中保留下来的引用。

边界帧是当前标注的边界，不是已经验证的真实动作边界。内部采样帧不是保证捕获动作的语义关键帧。
未在这几张图中出现的动作不能直接判为未发生。帧预算超限直接报错，不静默丢帧。

## 输出

输出保留verification的独立判断：

- `pass`：在输入证据支持范围内未发现实质错误，没有待补证请求。
- `retry`：存在具体视觉证据支持的错误；`failed_subtasks`、`issues`和`replacement_ranges`定位问题与建议重标范围。
- `manual_review`：无法完成裁决；`evidence_requests`写需要补查的subtask及具体问题，不把怀疑当成确认错误。

`task_assessment`另行输出met/not_met/uncertain，区分标注准确性与任务满足情况。
任务未满足但Stage3准确时允许pass，不要求把真实失败改写成成功。

新增字段示例（示例不代表实际存在错误）：

```json
{
  "replacement_ranges": [{"first_subtask_index": 2, "last_subtask_index": 3}],
  "evidence_requests": []
}
```

证据不足时的字段示例：

```json
{
  "replacement_ranges": [],
  "evidence_requests": [{"subtask_indices": [2, 3], "question": "核实两段交界附近是否发生物体交接"}]
}
```

补查段号由程序映射到真实时间范围，保存`followup_plan.json`。**本模块目前生成补查清单，不自动调用额外密集视频复核。**
pipeline收到manual_review停止自动重标，不发布未通过验收的Stage3。
retry接入配置中的重试逻辑，当前max_correction_retries=2，共最多三轮重标与审核；仍受最初裁决的可写范围限制。
目前不会按新建议范围重新构建局部执行任务。
上游“逐个subtask重标整集”的执行器尚未在这次新增模块中改写，不能把本模块上线理解为整个新方案已全部实现。

## 配置

共用`../config/config.yaml`：

```yaml
post_correction_review:
  interior_fps: 1  # 先按1 fps生成内部候选帧
  max_inner_frames: 4  # 允许0至4，首尾边界另算
stages:
  verification:
    thinking: true
```

此审核使用独立的interior_fps，不使用stages.verification.fps。图片大小、JPEG质量、总帧上限仍使用`frames`，模型参数仍使用`model`。
在主pipeline中无需单独启动：合并出完整候选Stage3后自动调用。

## 单独审核已有校正结果

在框架根目录运行，输入必须是含`start_time_sec/end_time_sec/subtask_index`的Stage3 JSON：

```bash
cd '/mnt/workspace/Subtask_Annotation _Review'
python -m annotatiuon_audit.annotation_correction.post_correction_review \
  --episode robodojo_episode_000500 \
  --annotation /path/to/final_corrected_stage3.json \
  --output-root /mnt/data/chachaxu/dataset/robodojo/stage3_correction_runs/post_review_pilot \
  --dry-run
```

去掉`--dry-run`才调用API。输入、代码或配置改变必须换output-root，避免混用结果。
主入口`python -m annotatiuon_audit.annotation_correction ... --dry-run`也会检查此模块的抽帧和图文对应关系，但使用原Stage3作为输入形状样例，不代表已经完成真实重标。

## 保存文件

主pipeline保存于`correction_NN/post_correction_review/`；独立运行保存于指定输出目录下的episode目录。

- `sampling_plan.json`：每段帧数、帧号、边界类型及总帧列表。
- `input.json`：实际提示词、完整文本、帧路径与哈希，不保存密钥。
- `attempt_*/`及`result.json`：原始模型返回、重试记录、请求指纹缓存。
- `output.json`：通过结构和证据引用校验的审核结果。
- `followup_plan.json`：建议重标范围、待补查段及其原始时间范围。
- `preflight.json`：dry-run图文输入，`api_called=false`。

不覆盖原标注、不修改Stage4；只有通过主pipeline验收的结果才成为其正式Stage3输出。
