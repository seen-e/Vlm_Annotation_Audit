# VLM Episode 聚类

框架支持三种互相独立的运行方式：

| mode | 输入边界 | 处理流程 |
|---|---|---|
| `scene_only` | 同一任务 | 仅使用首帧、尾帧或首尾帧做两轮场景聚类 |
| `text_only` | 同一任务 | 不读取场景聚类结果，直接对该任务全部已选 episode 做两轮文本结果聚类 |
| `scene_then_text` | 同一任务内的场景组 | 先做图像场景聚类，再在每个场景组内部做文本结果聚类 |

三种方式都不跨任务。`scene_then_text` 还禁止跨父场景组合并。小于
`text_clustering_min_group_size` 的文本父组原样保留，不调用 API。

## 目录

```text
vlm_clustering/
  config.yaml                 统一入口及默认模式
  run.py                      三种模式的统一调度器
  image_base_clustering/      图像两轮聚类
  txt_base_clustering/        文本两轮聚类及两种输入适配器
```

## 运行

```bash
cd '/mnt/workspace/Subtask_Annotation _Review'

python -m episode_clustering.vlm_clustering.run --mode scene_only
python -m episode_clustering.vlm_clustering.run --mode text_only --dry-run
python -m episode_clustering.vlm_clustering.run --mode text_only
python -m episode_clustering.vlm_clustering.run --mode scene_then_text
```

旧的 `--stage image|text|all` 仍可使用：`image` 和 `all` 分别等价于
`scene_only` 和 `scene_then_text`；`text` 保留旧语义，只对配置中已有场景组执行
文本步骤，并不等价于新的 `text_only`。新任务建议统一使用 `--mode`。

## 配置与输出

`config.yaml` 的 `default_mode` 控制无命令行参数时的模式。图像和文本模型参数仍由
各自配置文件管理。文本配置的 `input_mode` 通常无需手工修改，统一入口会覆盖为：

- `text_only` -> `task_scope`
- `scene_then_text` -> `scene_groups`

`task_scope` 使用 `dataset_root/meta/tasks.parquet` 和 `episode_manifest` 建立任务内成员，
不读取图像聚类输出。`scene_groups` 使用 `image_results_root` 的完整结果。

文本输出统一包含 `clustering_mode`、`parent_groups`、`parent_group_id` 和
`parent_group_source`。递进模式额外保留旧字段 `parent_scene_group_id` 及
`scene_groups` 别名；独立文本模式对应字段为 `null`，并输出 `task_groups` 别名。
运行签名包含输入模式、父组来源、输入清单哈希、标注哈希及提示词，不能在同一
`output_root` 中误复用另一种模式的缓存。

图像、文本聚类都只是分组，不负责判断标注正误。异常审核由独立审核模块完成。
