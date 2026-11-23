# Stage3 矫正框架

集成部署目录：`/mnt/workspace/Subtask_Annotation _Review/annotatiuon_audit/annotation_correction`（1015）。
原独立框架保留作为历史版本。本模块复用其提示词、矫正算法及独立复核逻辑，改为包内相对导入。
只用原始视频核验并修正 Stage3 的 subtask 标注。原始文件保持只读，结果写入独立输出目录。

## 与异常筛查衔接

统一入口 `workflow.py` 支持三个模式：`audit` 只筛查、`correction` 使用已有筛查结果、
`all` 完成整批筛查后再矫正。本次仅对接生产使用的 `keyframe_grounded_filter/two_round`；
纯文本及单轮图文模块仍可独立运行，其输出不能未经适配直接送入当前矫正流程。

`config/workflow.yaml` 指定审核配置、矫正配置和独立调度日志目录。只有 `all`/`audit`
模式会启动审核API；`correction` 模式不重复筛查。原审核提示词审批及pilot限制保持有效。
统一入口默认不发布覆盖原始final.json，也不启动Stage4、Planner或周期监控。

交接以审核 `plan.json` 中的episode清单为准，不扫描其他实验的结果。全部审核结果必须存在，
且最终结论与逐轮结果一致；出现漏项、失败或未完成的视觉复核时阻止交接，不静默跳过。
随后比较审核时的Stage3语义字段、task和审核标准与当前输入，防止旧审核作用于新标注。
仅选择 `annotation_anomaly/task_not_met/uncertain`，正常数据跳过。没有候选则结束，不调用矫正API。
`handoff.json` 保存候选、统计及来源文件哈希，每条矫正开始时再次校验输入没有变化。

```bash
cd '/mnt/workspace/Subtask_Annotation _Review'
# 已有审核结果交接检查，无API、无抽帧；旧结果与新Stage3不符会报错。
python -m annotatiuon_audit.annotation_correction.workflow --mode correction --dry-run
# 只审核；使用config/workflow.yaml中指定的审核配置。
python -m annotatiuon_audit.annotation_correction.workflow --mode audit
# 使用已有审核结果继续矫正。
python -m annotatiuon_audit.annotation_correction.workflow --mode correction
# 整批审核结束后，自动选择非正常结果并矫正。
python -m annotatiuon_audit.annotation_correction.workflow --mode all
```

运行前应修改配置中的数据及输出路径。当前默认审核配置指向历史3500条实验，不能用其旧结论
矫正已经更新的1965条Stage3；应待新Stage4齐备并使用新的审核输出目录。
`--mode all --dry-run`只展示调用计划，不要求尚未生成的审核结果已存在。
流程 `status.json` 记录所处模块，并链接矫正 `progress.json`。complete表示处理结束，
不代表所有episode通过；需查看 `counts`、`needs_manual_review` 和每条 `verification.json`。

## 流程

1. **视频裁决**：整集按配置 FPS 取帧，输入完整 Stage3、任务、审核标准和旧审核关注的 Stage3 原文。
   允许判定旧审核误报。三类非正常结果均重新审核、生成标注并验收，不因裁决未发现错误跳过生成。
2. **定向重标**：对每个局部范围或整集依次执行 `plan → refine → finalize`。
   候选分段、相邻边界联合精修、融合终审各调用一次模型，各有独立完整提示词。
   当前whole_episode是将整集作为一个区域，不是逐个原subtask调用；本次整合不改变该行为。
3. **程序合并**：替换指定连续范围，支持拆分、合并、增加和删除；检查覆盖、重叠和空缺。
   范围外语义字段逐项保持一致，仅统一重编号，并记录按时间重叠计算的旧新编号映射。
4. **校正后整集图文审核**：输入修正后的完整Stage3及每段首尾边界帧、内部最多4帧，不按FPS输入整集图像。
   独立模块post_correction_review不接收重标理由或旧审核结论，补证需求另存清单，不直接认定错误。
   通过后才产生 `final_corrected_stage3.json`。本次实验最多再矫正两次，共不超过三轮，之后转人工。

本框架不读取 Stage4 标注、成功判断或边界判断，也不判断 Stage4 是否正确。
旧审核输出只提取 `final_annotation` 对应 Stage3 原文作为待核实线索；其引用必须匹配当前原文。
跨阶段冲突理由不会转发。重新标注仍如实记录实际任务失败，不能为了符合标准改写成成功。

当前纳入annotation_anomaly、task_not_met、uncertain，旧3500条结果对应1420+372+411=2203条。
required_reannotation_statuses包含全部三类：annotation_anomaly、task_not_met、uncertain。
whole_episode_statuses指定task_not_met和uncertain始终整集重标，即使裁决仅确认局部错误也不缩小范围。
执行范围另存execution_plan.json，原adjudication.json保留真实裁决，不伪造incorrect。
对于annotation_anomaly，存在明确局部问题时按裁决范围重标；需整集修正时整集重标。
若裁决认为旧审核误判或证据不足、无法定位修正范围，也重新生成整集Stage3，不直接no_change。
重新生成不等于必须改变正确语义；未通过独立验收的结果仍转人工，不发布为已验收版本。

对于task_not_met，裁决额外输出task_not_met_diagnosis，区分stage3_error（Stage3漏写或写错）、
instruction_video_mismatch（task或审核标准与实际视频不同）、audit_false_positive（旧审核误判）。
证据不能区分时使用unresolved。确定归因必须引用输入视频帧；指令差异或误判必须引用原要求。
dataset_expected_success表示这批数据理论上完成任务，仅作为检查前提，不强制模型判成功。
归因结果传给plan/refine/finalize，并单独保存。独立验收不接收该诊断，重新判断新Stage3与视频、视频与要求的关系。
即使诊断为指令差异，也不自动改写task或审核标准，更不能补写画面未支持的动作。

## 目录

```text
config/config.yaml           唯一运行配置
prompts/adjudication.py       视频裁决完整提示词
prompts/plan.py               候选重分段完整提示词
prompts/refine.py             内容与边界精修完整提示词
prompts/finalize.py           最终融合完整提示词
prompts/verification.py       独立验收完整提示词
post_correction_review/      校正后图文审核、按段抽帧与独立运行入口（详见目录内ReadMe.md）
correction/data.py           RoboDojo数据适配与旧审核筛选
correction/media.py          从原视频提取和缓存帧
correction/schema.py         结构、证据、范围及时间校验
correction/review.py         审核结论汇总及执行范围分流
correction/merge.py          局部替换及编号映射
correction/client.py         API、请求记录、重试和缓存
correction/pipeline.py       每个episode的完整流程
run.py                       并发入口
tests/test_framework.py      单测及模拟端到端验证
```

## 输入适配

`annotations_root/{episode_id}/final.json`：旧 Stage3；只取subtasks的动作描述、状态、摘要、
边界说明和不确定性，不取顶层review、episode_success或terminal_summary，也不取旧派生指令。
`sample_manifest.json`：将旧sample边界精确映射为episode绝对秒数，不用采样FPS猜测。
输入同时携带original_sampled_frames映射，便于模型理解旧描述中的sample引用；
这些旧sample编号与当前图片的episode绝对frame_index明确区分。
`standards_path`：`episodes[episode_id]` 中的task和review_standard。
`audit_root/{episode_id}/final.json`：已有两轮审核结果，用于选集和定位候选声明。
`video_metadata_root/{episode_id}/metadata.json`：`source` 中包含原视频路径、FPS、长度、
`first_source_frame`、`last_source_frame`。多个episode共用视频文件时，正确应用起始帧偏移。

当前适配器针对现存 RoboDojo 文件结构；其他数据集需提供同结构元数据或修改data.py。
使用PyAV/libdav1d从原视频解码（支持RoboDojo的AV1），按PTS验证共享文件帧位置，
不读取Stage4的缓存图片。每张图前给出绝对episode帧号、时间及原文件帧号。
支持每阶段独立FPS（可设0.5等小数）；要求不超过源视频FPS。
整集裁决adjudication默认1 FPS，重标plan/refine/finalize暂保留2 FPS。
最终验收verification每段保留边界2帧，内部先按1 FPS采样，超过4张才均匀选4张，不足不补齐。
默认额外加入每个subtask的首帧及末帧，按原视频帧号排序、去重，并保留episode最后一帧。
首帧取半开区间起点及之后第一张原始帧，末帧取终点之前最后一张原始帧；这些是标注边界，不是已确认的动作边界。
裁决使用原Stage3边界，精修同时参考候选边界，最终验收使用合并后新Stage3边界。
局部重标只加入观察区间内的边界帧。补充帧后图像间隔不均匀，模型以每张FRAME时间戳为准。
include_subtask_boundaries可关闭边界补帧；max_frames检查去重后总数，超限报错，不静默丢弃边界或降采样。

## 裁决与范围

`stage3_verdict`：`correct / incorrect / insufficient_evidence`。
`scope`：`none / partial / whole_episode`。
错误类型仅为：`action_error / object_error / actor_error / result_error / segmentation_error / omission_error`。

裁决和最终验收都单独输出`task_assessment`：包含`visual_evidence`、`requirement_evidence`、
`reason`和`status`（`met / not_met / uncertain`）。确定满足或未满足均需引用实际输入帧及任务/标准原文。
该项判断视频与要求是否一致，不把任务未满足等同于Stage3错误，不把数据集成功前提当作证据。

程序组合两条独立判断，避免模型重复输出互相冲突的状态：

| Stage3与视频 | 视频与要求 | review_status |
|---|---|---|
| incorrect | 任意 | annotation_anomaly |
| correct | met | completed |
| correct | not_met | task_not_met |
| correct | uncertain | null，需人工裁决 |
| insufficient_evidence | 任意 | null，需人工裁决 |

`initial_review.json`描述旧Stage3，`final_review.json`描述最后一次验收的Stage3，
各自`annotation_path`明确指向被审核的文件。业务类别由程序生成，不额外调用模型。
不确定输入经审核可以落入上述三个类别；证据仍不足则保留null，不强行归类。
业务类别不代替执行策略：旧task_not_met/uncertain仍整集重标，旧annotation_anomaly按确认范围重标。
最终验收pass只表示新Stage3可接受；若视频不满足任务，仍可保存准确标注并报告task_not_met。
若Stage3准确但任务完成情况仍不确定，也保存已验收标注，但`needs_manual_review=true`。
若Stage3本身无法验收或重试仍有错误，不发布最终标注，流程状态manual_review。
流程状态（corrected/reannotated/manual_review等）与业务类别是两个维度，不要只看“已重标”就认定任务完成。

partial可包含多个不相交连续范围。模型列target_subtasks及replacement_ranges，程序补充相邻
段和padding作为观察上下文；实际可写边界固定。若要调整两段之间的边界，必须将两段纳入替换范围。
模型裁决只在全局顺序、对象身份或切分整体混乱时建议whole_episode，不能仅因错误多或任务失败就判错。
程序实际执行范围仍遵循上文用户指定的强制整集重标规则。
验收发现范围外问题时转人工，不自动扩大先前裁决的范围。

## 配置与运行

```bash
cd '/mnt/workspace/Subtask_Annotation _Review'
python -m pip install -r annotatiuon_audit/annotation_correction/requirements.txt
python -m unittest discover -s annotatiuon_audit/annotation_correction/tests -t . -v

# 只读取真实输入、检查映射并提取视频帧，不调用API。
python -m annotatiuon_audit.annotation_correction --episode robodojo_episode_000500 --dry-run \
  --output-root /mnt/data/chachaxu/dataset/robodojo/stage3_correction_runs/preflight_v1

# 单条完整矫正。三类旧非正常输入均重标；视觉裁决与配置共同决定执行范围。
python -m annotatiuon_audit.annotation_correction --episode robodojo_episode_000500 \
  --output-root /mnt/data/chachaxu/dataset/robodojo/stage3_correction_runs/pilot_v1

# 按config选取annotation_anomaly、task_not_met和uncertain。
python -m annotatiuon_audit.annotation_correction --config annotatiuon_audit/annotation_correction/config/config.yaml
```

集成示例配置episode并发16，可在episode_batchsize中调整；每个episode的阶段顺序执行。
不再默认绑定88条历史样本，统一入口根据所选审核输出生成明确的episode_ids。
max_correction_retries=2表示首轮加两次重试，共最多三轮重标与统一审核；通过后提前结束。
证据不足、需要补查或范围外问题仍提前转人工，不强行重复到三轮；网络/结构错误重试不算完整重标轮次。
默认所有VLM阶段thinking=true，示例配置使用当前0z34凭据路径，不修改采样率。
目前局部范围依次处理，无独立subtask并发。密钥不写入请求记录。
thinking按阶段配置。部署和dry-run都不会自动启动全量API标注。

## 输出与续跑

每条输出保存：

```text
original_stage3.json           完整旧final快照，仅用于追溯
stage3_input.json              规范化后的Stage3输入
audit_feedback.json            只保留Stage3待核实声明
source.json                    视频映射及审核标准
adjudication/input.json        完整提示词、文本、帧元信息
adjudication.json              视频裁决
initial_review.json            旧Stage3审核类别和任务满足情况
task_not_met_diagnosis.json    旧任务未满足结果的原因诊断（仅该类）
execution_plan.json            实际重标范围及触发原因
correction_01/region_000/      plan/refine/finalize逐阶段输入输出
correction_01/replacement.json 局部生成结果
correction_01/merge_record.json 替换范围及编号映射
correction_01/candidate_stage3.json 待验收结果
correction_01/post_correction_review/ 独立整集审核输入输出、抽帧计划与补查清单
correction_01/review.json      本次候选标注的独立审核结论
verification.json             最后验收结论
final_review.json              最后审核类别、证据、人工需求及被审核文件路径
final_corrected_stage3.json    仅通过验收才生成
status.json                   no_change/manual_review/corrected/reannotated或运行状态
frame_cache/                  原视频抽帧缓存
```

根目录progress.json统计完成数、failed数、各结果状态、耗时；failures.json列出失败episode。
initial_review_counts与final_review_counts分别统计重标前后业务类别，null汇总为unresolved；
needs_manual_review统计需人工处理数。统计只包含已有终态的episode，API执行失败不冒充审核结论。
重启同一命令时，复用输入指纹完全一致且再次通过结构校验的成功请求；失败请求继续重试。
网络/格式失败属于failed，重跑会继续；manual_review是已完成但需人工处理，不自动无限重跑。
同目录使用进程锁避免重复运行。配置、代码、输入或视频元信息变化时要求新output_root。
本次默认结果目录为stage3_correction_runs/robodojo_test88_thinking_b24_max3_20261119，旧结果不覆盖、不混用。

## 输出契约与限制

最终输出是**Stage3独立版本**，包含schema_version、episode_id、time_basis、subtasks。
时间采用`start_time_sec/end_time_sec`的episode绝对秒半开区间。新段不含旧sample索引，
不自动覆盖原final.json，不继续沿用基于旧分段生成的subtask_instruction或关键帧结果。
后续消费者必须使用此时间契约，不能把新subtask编号直接与旧Stage4编号关联。
未修改段的语义字段与规范化旧Stage3保持一致；编号改变记录在merge_record中。

整集重标目前对整集图像执行新实现的plan/refine/finalize三阶段，并非调用旧递进切片框架。
本版不自动切长视频。超过max_frames必须显式增大预算或后续接入递进窗口，不能截断后当成整集。
验收仍受采样密度与模型误判限制，pass仅表示采样证据下未发现实质错误。
本版本搭建时只做单元/模拟流程与真实数据dry-run，尚无真实API矫正效果结论。
新增整集审核模块不等于上游逐个subtask重标方案已经实现；上游仍为既有按裁决范围的三步重标流程。
审核发现证据不足时保存evidence_requests及时间范围，当前不自动执行密集补查；重试仍沿用已有可写范围。
