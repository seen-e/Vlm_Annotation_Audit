# image_base_clustering 使用说明

更新：2026-11-11。部署位置：1015，`/mnt/workspace/Subtask_Annotation _Review/episode_clustering/vlm_clustering/image_base_clustering`。

## 1. 用途与流程

使用API模型观察每个episode的首帧、尾帧或首尾帧，在同一任务内部进行两轮场景分组。无需DINO向量，不读取subtask标注，不解码视频。它是聚类模块，不是任务完成度或异常审核模块。

1. 按任务精确分组，episode排序后按固定随机种子打乱。
2. 第一轮按episodes_per_batch分批，默认每批50条；各批产生候选组及代表成员。
3. 第二轮把本任务所有候选组的代表图一次输入模型，只合并完整候选组。
4. 程序将合并组展开回episode，校验全覆盖，无遗漏、无重复、无越界。

严格只有两轮。第二轮不拆分第一轮组，也不逐episode重新归属；怀疑内部混杂时保留uncertain。模型的场景相似解释与DINO的向量距离不是同一指标。

## 2. 文件与依赖

| 文件 | 用途 |
| --- | --- |
| config.yaml | 输入、帧模式、批大小、并发、API参数 |
| round1_prompt.py | 第一轮完整提示词 |
| round2_prompt.py | 第二轮完整提示词 |
| run.py | 任务映射、分批、任务并发、两轮流程 |
| api.py | 图像预处理、API调用、请求签名、重试和保存 |
| schema.py | 输出状态、成员、代表、合并范围校验 |
| build_viewer.py / viewer.html | 原始发送图像对应的查看器 |
| retry_failed.py | 本批实验专用的提高输出预算补漏入口 |
| test_pipeline.py | 模拟API测试 |

历史quick_viewer.html、review_standards.js、build_quick_local.cjs仍在本目录兼容旧页面。新的人工标准功能请使用 `review_standard/manual_review_standard` 模块，避免把聚类和人工审核混为一体。

依赖：Python、PyYAML、Pillow、PyArrow、OpenAI SDK。无需本地GPU，推理由配置的API完成。

## 3. 输入

- cache_root：已有 `selection.json`、各episode的metadata.json、first.png和last.png。
- dataset_root：LeRobot数据集，含meta/tasks.parquet中的task_index和原始任务文本字段__index_level_0__。
- 每个episode必须唯一，当前只支持一个任务，任务文本必须精确匹配；不根据episode数字范围推算任务。
- 图像文件、episode身份及任务元信息必须一致，实际发送前检查SHA256。

第一轮输入顺序为结构化任务/成员信息，然后每张图前的episode_id、frame_role、episode_frame_index、timestamp_sec标签和对应图片。第二轮另带group_id，只发送代表图片及候选组描述；候选描述不是独立视觉事实。

## 4. 配置字段

相对路径以config文件所在目录为基准；建议生产路径用绝对路径。CLI可以覆盖帧模式、批大小、代表数量、task-id、output-root。

| 字段 | 当前默认值/含义 |
| --- | --- |
| cache_root | `/mnt/data/chachaxu/embedding_cluster/robodojo` |
| dataset_root | `/mnt/data/chengyezhao/RoboDojo_ee_lerobot_v30_video` |
| output_root | 实验独立输出目录，换实验需换目录 |
| frame_mode | first_last；可选first、last、first_last |
| episodes_per_batch | 50，第一轮每次请求的episode数，不是最终组大小 |
| representatives_per_cluster | 3，实际必须是min(3,该组成员数) |
| task_ids | []表示全部，非空为任务ID列表 |
| task_workers | 4，任务级并发；此前全量实验使用16，不是默认配置 |
| shuffle_seed | 42，按seed+task_id确定分批 |
| max_images_per_request | 200，单次请求图片硬上限 |
| max_image_edge | 640，保持比例缩放，不中心裁剪 |
| jpeg_quality | 90，实际发送JPEG会保存 |
| api.thinking | true；Qwen扩展参数，不支持的接口可设null以省略 |
| api.temperature | 0 |
| api.max_tokens | 16384，输出截断按失败处理 |
| api.timeout_sec | 600 |
| api.attempts | 3，每个请求最多尝试次数，含格式校验失败 |
| api.retry_delay_sec | 5 |
| api.stream | true |

任务内部的第一轮批次顺序执行，完成后才执行第二轮；任务之间并发。100条/任务、batch50意味着两次第一轮加一次第二轮，而不是100个并发请求。
first_last下50条对应100张图。第二轮实际代表图超过容量直接报错并保留第一轮结果，不自动减代表或增加第三轮。

## 5. API凭据

配置api.credentials_file可指向受保护YAML，格式为顶层base_url、api_key、model，或嵌套在model对象中。
不设置凭据文件时使用api.base_url_env/api.api_key_env/api.model_env指定的环境变量，默认名称为VLM_CLUSTER_BASE_URL、VLM_CLUSTER_API_KEY、VLM_CLUSTER_MODEL。
此模块选择凭据文件时不再用环境变量逐项补齐，必须三个字段均有效。不把真实密钥写进README或分享的配置。

目录已从EmbeddingCluster_Exp重命名；旧实验run_config.yaml或脚本可能仍引用旧绝对路径，启动前核对credentials_file及工作目录。不能仅因文件还在就认为旧路径仍有效。

## 6. 执行

```bash
cd '/mnt/workspace/Subtask_Annotation _Review'
python -m episode_clustering.vlm_clustering.image_base_clustering.run --help
python -m unittest episode_clustering.vlm_clustering.image_base_clustering.test_pipeline

# 仅校验输入并写dry_run_plan.json，无API请求。
python -m episode_clustering.vlm_clustering.image_base_clustering.run --config episode_clustering/vlm_clustering/image_base_clustering/config.yaml \
  --task-id 0 --output-root /mnt/data/chachaxu/embedding_cluster/robodojo_vlm_experiments/NEW_RUN --dry-run

# 配好API并审核提示词后，去掉dry-run执行。
python -m episode_clustering.vlm_clustering.image_base_clustering.run --config episode_clustering/vlm_clustering/image_base_clustering/config.yaml \
  --task-id 0 --frame-mode first_last --episodes-per-batch 50 \
  --representatives-per-cluster 3 \
  --output-root /mnt/data/chachaxu/embedding_cluster/robodojo_vlm_experiments/NEW_RUN
```

全量时移除task-id且确认config.task_ids为空。并发需要编辑task_workers，没有 `--task-workers` CLI。此入口是前台执行，不会自动创建后台任务或监控。

## 7. 输出与验收

```text
output_root/
  identity.json / config_snapshot.json / prompt_snapshot.json / plan.json
  progress.json / summary.json / failures.json / assignments.json
  task_000/
    round1/batch_000/
      input.json / images/ / attempt_001/ / output.json
    round1_candidates.json
    round2/input.json / images/ / attempt_001/ / output.json
    final.json
```

input.json保存完整提示词、payload、图片来源/发送哈希和参数；attempt目录保存原始响应、finish_reason、耗时或错误。密钥不写入快照。final.json是该任务的聚类结果，不是机器人动作标注。
第一轮coherent至少2条，singleton/uncertain单独成组；代表严格取min(配置,组大小)。第二轮每个候选组恰好出现一次，最终每个episode也恰好出现一次。

进度按**任务结束**更新，不是每个请求实时更新。查看progress.json以及任务目录内output.json。最终summary.status应为complete、failed_tasks为0；failures.json为空。summary.episodes只统计成功任务，不能以进程退出或文件数量代替覆盖验收。

## 8. 重跑与专用补漏

原命令原配置重跑会对成功请求重新校验并复用；失败或缺输出请求继续调用API。修改模型、提示词、输入、帧模式、分批或配置身份会拒绝复用，需新输出目录。不要两个进程同时写一个目录，当前无调度锁。

retry_failed.py是此前全量实验的**专用**补漏工具，不是普通run入口的通用必选项：

```bash
python -m episode_clustering.vlm_clustering.image_base_clustering.retry_failed /path/to/existing_run
```

它要求实验目录额外具备run_config.yaml、summary.json、failures.json、progress.json，且凭据路径仍有效。普通run.py不会自动生成run_config.yaml。默认后台启动，PID在recovery_launch.json，日志在recovery.log；--worker用于前台工作模式。
专用工具将缺output的请求输出预算固定改为32768，在recovery_时间戳目录发新请求，并写recovery.json指向实际请求；已成功请求复用。旧失败目录没有output.json不等于仍缺结果，应同时看恢复链接与最终final.json。
API服务可能不支持该预算，使用前检查。此工具不能代替通用的配置迁移或任意输入变更。

## 9. 可视化与已有结果

```bash
python -m episode_clustering.vlm_clustering.image_base_clustering.build_viewer /path/to/run
# 运行未结束时，只展示已有final.json的任务快照：
python -m episode_clustering.vlm_clustering.image_base_clustering.build_viewer /path/to/run --completed-only
```

输出在run/viewer，优先复用已发送JPEG；查看器是生成时快照，不会后台自动更新。人工审核标准填写使用manual_review_standard构建器，不是这个高分辨率查看器。

已完成的全量实验：`/mnt/data/chachaxu/embedding_cluster/robodojo_vlm_experiments/first_last_batch50_xb4v_full3500_20261111_155511`，35任务、3500条、199组，包含一次有明确记录的补漏。聚类仍未经逐成员人工确认。

## 10. 常见故障

- 401/鉴权错误：检查当前凭据文件和端点，不自动切换到另一实例API。
- finish_reason=length：不是成功结果；评估提高预算并使用新实验或专用补漏，保留失败原文。
- 成员遗漏/重复：格式校验会重试；仍失败则保留错误，不编造归类。
- 图片超限/上下文超限：减少新实验批次或代表数，不能让当前运行静默改变输入。
- 挂载、源图哈希或文件路径异常：先恢复输入可读性；模块不实现挂载自动恢复或pkill守护。
