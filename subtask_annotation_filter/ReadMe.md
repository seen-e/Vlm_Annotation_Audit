# subtask_annotation_filter 使用说明

更新：2026-11-11。部署位置：1015，`/mnt/workspace/Subtask_Annotation _Review/subtask_annotation_filter`。

## 1. 当前状态与用途

用于将每个episode的人工审核标准与最终subtask标注一起送入模型，按episode独立筛查异常。

**当前按用户要求启用首轮批量实验，审核效果仍需实测和人工复核。**

- 模块根目录的 `prompts.py` 已写入纯文本审核初稿及识别偏差/步骤变化容错规则；用户明确要求批量实验后设置 `APPROVED = True`。
- 将APPROVED改为False仍会阻止正式CLI调用；未审核的新提示词应先关闭此开关。
- `schema.py`中的normal/anomaly/uncertain和证据结构用于本轮实验，并不代表审核规则质量已经最终验收。
- 7项模拟测试已通过；本轮3500条已于2026-11-11启动真实API预测，不代表已全部完成或准确率验证完成。
- 当前只处理文本，不输入图像、不插入扩写、不改写源final.json、不触发重新标注。

## 2. 文件与职责

| 文件 | 用途 |
| --- | --- |
| config.yaml | 数据路径、episode选择、并发及模型设置 |
| data.py | 标准加载、episode精确匹配、完整final.json读取、原子JSON写入 |
| client.py | OpenAI兼容API、流式收集、凭据解析 |
| run.py | 预检、任务锁、并发调度、重试、续跑、进度与结果汇总 |
| schema.py | 待定稿的输出校验接口及原文证据检查 |
| prompts.py | 待共同审核的独立完整提示词初稿及启用开关，无需跨文件拼接 |
| test_pipeline.py | 不访问API的单元测试 |
| requirements.txt | PyYAML和OpenAI SDK依赖 |

依赖Python 3.10及以上（使用Path.is_relative_to），建议使用当前已验证的Python环境。本地无GPU要求。

## 3. 输入和匹配规则

当前标准：`/mnt/data/chachaxu/dataset/robodojo/robodojo_review_standard.json`，3500条已展开有效标准。
当前默认标注根目录：`/mnt/data/chachaxu/dataset/robodojo/robodojo_merged_stage1to4_20261106`。
通过 `annotation_pattern: "{episode_id}/final.json"` 精确寻找文件，不自动挑选其他版本。

支持标准格式：

```json
{
  "episodes": {
    "robodojo_episode_000000": {
      "task": "可选原始任务文本",
      "review_standard": "已展开的有效审核标准"
    }
  }
}
```

也支持直接episode映射（值为字符串或含review_standard的对象），以及包含episode_id、review_standard的对象列表。这里不再合并任务和组标准，优先级由manual_review_standard提前处理。

每个请求的基础payload为：

```json
{
  "episode_id": "robodojo_episode_000000",
  "review_standard": "该episode的有效标准原文",
  "task": "可选原始任务文本或null",
  "final_annotation": {
    "subtasks": [{"segment_summary": "原始标注内容"}]
  }
}
```

final_annotation当前保留完整JSON，不只传segment_summary；字段取舍待共同审核。不会自动裁切超长episode。
task优先取标准记录中的task，其次final中的task或instruction。输入资料并不因被传给模型就成为已验证的视频事实。

预检在任何API调用前加载全部选中episode，检查标准非空、文件存在、episode身份、非空subtasks列表与唯一subtask编号。源字段有subtask_index时保留；没有时草案校验器以列表0起下标定位，不改写原标注。
预检失败会停止整次启动，而不是用空标准审核或把缺数据算正常。
已完成本轮3500份真实final.json全量读取预检。input_workers控制并发只读预检，全部通过后才开始API调用；日志每100条更新一次预检进度。

## 4. 配置字段

相对路径以config文件目录为基准。输出目录不能是标注根目录或其父子目录，防止混淆源标注和审核结果。

| 字段 | 当前值/说明 |
| --- | --- |
| standards_path | 按episode展开的标准JSON |
| annotations_root | 最终标注根目录 |
| annotation_pattern | `{episode_id}/final.json`，不能越出根目录 |
| output_root | `.../robodojo/subtask_annotation_filter_runs/robodojo3500_thinking48_20261111_192924`，本轮独立输出 |
| episode_ids | []选全部标准记录；非空仅选明确编号，不重新编号 |
| episode_batchsize | 48，同时审核的episode数，不是一个请求放48条 |
| input_workers | 32，并发只读预检，不是API并发 |
| model.credentials_file | `../.credentials/xb4v.yaml`，本轮使用既有xb4v凭据 |
| model.base_url | null，可显式覆盖API端点 |
| model.base_url_env | ANOMALY_BASE_URL |
| model.api_key_env | ANOMALY_API_KEY |
| model.name | null，显式模型名优先 |
| model.model_env | ANOMALY_MODEL |
| model.thinking | true；Qwen扩展，不支持时设null省略 |
| model.temperature | 0 |
| model.max_tokens | 8192，截断结果不会视为成功 |
| model.stream | true |
| model.timeout_sec | 600 |
| model.attempts | 3，每条每轮最多总尝试次数 |
| model.retry_delay_sec | 5，两次尝试间隔 |

本轮已绑定xb4v凭据文件。凭据文件支持顶层base_url/api_key/model或嵌套model对象；API key环境变量优先于凭据文件，端点/模型优先显式配置，其次凭据文件，最后环境变量。
密钥不写入配置快照或正常请求文件，错误日志对已知密钥脱敏。完整输入和响应仍是业务数据，应控制目录访问权限。

## 5. 离线验证

```bash
cd '/mnt/workspace/Subtask_Annotation _Review'
python -m unittest subtask_annotation_filter.test_pipeline -v
python -m subtask_annotation_filter.run --help
python -m subtask_annotation_filter.run --config subtask_annotation_filter/config.yaml --dry-run
```

dry-run只读取和校验输入，**不读API凭据、不发请求、不写预测结果**。共享盘读取大量小JSON可能较慢，不要误认为已经在模型预测。
小样本预检可复制配置为外部实验配置，将episode_ids设为少量真实编号；不要将模拟测试文本写回正式标准。

测试覆盖：提示词禁用、成功缓存复用、输入变更拒绝、截断重试、无效缓存归档、证据/编号校验、缺标准拒绝、并发上限、失败续跑、密钥错误脱敏。

## 6. 后续变更与试验约定

1. 确定审核目标、哪些情况算异常/不确定，以及与真实任务失败的区别。
2. 确定输入字段，是否还需要图像，是否允许使用任务背景。
3. 确定输出JSON结构及subtask、原文证据的定位方式。
4. 同步更新prompt、schema.py、汇总字段和测试，不能只改提示词不改校验器。
5. 审核通过后由开发者更新PROMPT定稿并显式设置APPROVED=True。
6. 确定API、模型、输出目录，先少量验证，再启动批量。

仅设置APPROVED=True不是质量验证。内部run_jobs用于模拟测试，不应作为规避CLI审核开关的入口。本轮用户已要求启用实验；后续修改审核规则仍需遵循以上流程。

正式入口（当前已启用，运行会调用API，不要与后台同目录任务重复启动）：

```bash
python -m subtask_annotation_filter.run --config subtask_annotation_filter/config.yaml
```

## 7. 输出与进度

```text
output_root/
  .run.lock/                       # 调度期间存在的空锁目录
  plan.json                       # episode到输入签名映射
  config_snapshot.json / prompt_snapshot.json
  progress.json / run.log
  failures.json
  anomalies.json / uncertain.json
  robodojo_episode_000000/
    input.json                    # 原始标注路径、prompt、payload、模型参数、签名
    output.json                   # 通过当前结构校验才写入
    attempt_001/
      messages.json
      raw_response.txt
      meta.json / error.json
```

每完成或失败一条就更新进度；run.log逐行追加。completed包括本轮缓存复用成功数，cached是其中缓存数量；failed是API/解析/校验失败，不是模型判断的anomaly。
finished只表示本轮结束；all_succeeded为true才表示所有选中episode均成功。normal/anomaly/uncertain分别表示本轮提示词下的文本符合、明确问题和实质不确定，不代表视频已经核验。
程序遇到部分episode最终失败会继续其他条，全部结束后以非零退出码返回；failures.json含待重试编号和错误。

## 8. 续跑和异常处理

- 原命令重跑，input签名一致且output再次校验通过则直接复用，不调用API。
- 缺输出或前轮失败项重新尝试；已有但无效的output先改名为invalid_output_时间戳.json归档，再补跑。
- 提示词、标准、完整标注、模型、端点、生成参数、选择范围变更会拒绝复用；请使用新output_root。并发/超时/重试设置不改变生成语义签名，可用于恢复。
- 截断响应、非法JSON、证据不来自原文字段、错误episode/subtask编号等按失败处理，不自动补写“正常”结果。
- 正常调度退出清除.run.lock；强制杀进程或挂载中断可能留下锁。必须先确认旧调度器已停止，才删除这个空锁目录后续跑。不能同时启动两个写同一目录的调度器。
- 本轮通过 `/mnt/workspace/script/launch_subtask_annotation_filter.py` 启动独立后台进程，启动信息和冻结配置保存在模块logs目录。它不是后台守护，不定时轮询或自动补起退出的进程。
- 当前没有定时报告、挂载自恢复、视频重标注或异常查看器。模块不会自行pkill其他任务，也不会覆盖原始final.json。
- schema.py草案使用JSON Pointer定位相对于final_annotation的原文字段；证据中的数组下标与原始subtask_index可能不同，不能混用。

本轮启动日志：`logs/20261111_193058/stdout.log`，配置：同目录run_config.yaml，启动PID记录在launch.json。新实验须换输出目录，最终完成状态以结果目录progress.json为准。
后续完善以共同确定的审核方案为准，不根据当前实验结构推断最终产品规则。
