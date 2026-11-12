# VLM Review Standard

## 功能与边界

将原始任务 instruction 通过文本模型 API 忠实翻译成中文，再映射到每个
episode，生成现有 `annotatiuon_audit` 可读取的标准文件。

本模块不扩写指令，不推测场景，不读取视频、原始标注或人工审核标准。
输出是原始任务要求的中文版本，不是视觉判断，也不替代组内人工标准的细节。
仅翻译完全相同的原文一次，同任务下所有 episode 共享该译文。
不会使用桌面上人工翻译的 JSON 冒充 API 输出。

## 文件

- `prompts.py`：完整翻译提示词，修改后应使用新的输出目录。
- `config.yaml`：唯一配置入口，每个字段均有说明。
- `run.py`：输入校验、去重、并发 API 翻译、重试、缓存及映射。
- `test_pipeline.py`：离线模拟 API 测试，不产生模型费用。
- `requirements.txt`：依赖。

复用框架已有的 `annotatiuon_audit.client` 和 `data` 中的 API、凭据、
JSON 原子写入与签名函数；不调用异常审核流程，不修改其配置和提示词。
应保留完整框架目录运行，不要只复制本模块。

## 输入与匹配

`tasks_path` 为数据集的 `meta/tasks.parquet`，列为 `task_index` 和
`__index_level_0__`（原始 instruction）。

`episode_manifest` 为首尾帧缓存生成的 `selection.json`，顶层为列表：

```json
[
  {"episode_id": "robodojo_episode_000000", "tasks": ["原始完整 instruction"]}
]
```

其他视频字段不会发送给模型。必须通过完整原文精确匹配，不修改大小写、
标点，也不按 episode 数字范围猜测所属任务。缺失指令、多指令、重复 episode
或任务编号与原文不一致，全部在调用 API 前报错。
若多个 task ID 使用完全相同的原文，episode 行须提供明确的 `task_index`
或 `task_id`；它们仍共用一次翻译。

每次请求仅包含 system 提示词及 user JSON：

```json
{"instruction": "原始完整 instruction"}
```

模型只返回 `{"instruction_zh":"中文译文"}`。程序自行附加任务/episode 编号，
不让模型负责映射，避免编号错配。

## 配置与运行

从框架根目录执行：

```bash
cd '/mnt/workspace/Subtask_Annotation _Review'
python -m pip install -r review_standard/vlm_review_standard/requirements.txt
python -m review_standard.vlm_review_standard.run --dry-run
python -m review_standard.vlm_review_standard.run
```

可用 `--config /path/to/config.yaml` 指定配置。
默认 xb4v 凭据文件、关闭思考模式、8 个唯一指令并发；不是 3500 个 episode
逐条调用。RoboDojo 的 35 类/3500 个 episode 正常只需 35 次成功翻译请求。
重试会产生额外请求。

模型名、地址、密钥通过外部凭据或环境变量解析，规则与现有审核模块相同。
密钥不写入输出。默认路径独立于 `robodojo_review_standard.json`，不会覆盖人工标准。

## 输出与接入

输出目录包含：

- `plan.json`：选中 episode、原文、请求签名。
- `translations/<translation_id>/input.json`：提示词、原文及有效模型参数，不含密钥。
- `translations/<translation_id>/attempt_*/`：实际 messages、原始响应、结束原因或错误。
- `translations/<translation_id>/output.json`：通过结构校验的译文。
- `task_translations.json`：去重后的英中对照及任务 ID。
- `episode_review_standard.json`：全部成功后才发布的完整 episode 标准映射。
- `progress.json`、`failures.json`：当前进度与失败记录。

最终结构：

```json
{
  "schema_version": 1,
  "standard_type": "original_instruction_translation",
  "task_count": 35,
  "translation_count": 35,
  "episode_count": 3500,
  "episodes": {
    "robodojo_episode_000000": {
      "task_id": 0,
      "task": "原始英文指令",
      "instruction_zh": "中文译文",
      "standard_source": "instruction_translation",
      "review_standard": "中文译文",
      "translation_id": "原文的稳定哈希"
    }
  }
}
```

后续实验将筛查配置的 `standards_path` 指向这个新文件，并使用新的筛查
`output_root`。本模块不会自动切换现有审核任务或启动异常筛查。

## 续跑与失败

同一配置重跑时，校验已完成输出并跳过，仅补跑失败或无效的翻译。
API 错误、非法 JSON、缺少中文及响应截断均重试，耗尽次数后退出码为 1。
部分失败不会发布缺少 episode 的正式映射，失败任务可在下一次运行继续补跑。
输入映射、提示词或有效模型参数变化时拒绝复用旧输出目录。
同目录采用 `.run.lock` 防止重复启动；进程异常退出后须先确认没有运行中的
进程，才能移除残留锁。没有自动恢复挂载或终止其他进程的行为。

格式验证不能保证翻译语义绝对正确。正式用于审核前应抽查 `task_translations.json`，
尤其是数量、顺序、专有名称及原文歧义。

## 测试

```bash
python -m unittest review_standard.vlm_review_standard.test_pipeline -v
```

覆盖精确匹配、去重、歧义拒绝、错误映射、逐 episode 映射、缓存签名、失败重试、
断点续跑、输出锁和密钥不落盘。`--dry-run` 可验证真实 3500 条输入，不调用 API。
