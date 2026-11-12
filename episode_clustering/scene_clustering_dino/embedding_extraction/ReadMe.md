# embedding_extraction 使用说明

本模块现位于 `episode_clustering/scene_clustering_dino/embedding_extraction`。
Python 文件保持字节不变，避免仅因移动目录或修改注释而改变历史缓存的源码签名。
`run.py` 顶部保留了旧命令说明；实际运行请使用本文的新模块命令。
配置、模型与特征输出路径不变，目录移动本身不影响已有缓存签名。

更新：2026-11-11。部署位置：1015，`/mnt/workspace/Subtask_Annotation _Review/episode_clustering/scene_clustering_dino/embedding_extraction`。

## 1. 用途与边界

使用本地 DINOv3 ViT 权重，提取已有首帧、尾帧图像的 embedding。本模块不抽取视频帧、不调用模型 API、不聚类、不判断 subtask 正误。

有两个入口：`run.py` 逐图保存，适合小规模验证；`fast.py` 并行读图、批量推理、异步保存分片，适合全量提取。下游 DINO 聚类当前读取 **fast 分片格式**，不能直接把逐图输出目录传给它。

## 2. 文件与依赖

| 文件 | 用途 |
| --- | --- |
| config.yaml | 模型、图像缓存、层、分辨率、设备与保存设置 |
| model.py | 本地模型加载、选择 Transformer block、前向推理 |
| preprocess.py | letterbox、有效区域权重、池化与归一化 |
| run.py | 逐图输出、缓存检查、失败批次拆小 |
| fast.py | 预取、GPU 批量池化、NPZ 分片 |
| test_features.py | 预处理和特征逻辑离线测试 |

依赖：Python、PyTorch、支持 DINOv3 ViT 的 Transformers、Pillow、NumPy、PyYAML。优先使用现有已验证环境，不随意升级正在运行任务的环境。CUDA 用于 GPU 推理；权重仅从本地加载，不自动下载。

## 3. 输入

默认权重：`/mnt/data/chachaxu/model/dinov3`。
默认图像缓存：`/mnt/data/chachaxu/embedding_cluster/robodojo`。

```text
cache_root/
  selection.json
  robodojo_episode_000000/
    first.png
    last.png
    metadata.json
```

`selection.json` 为记录列表，每条包含 `episode_id`、`tasks`。每个 metadata 含 `source.episode_id` 及 `frames`；帧记录提供 `file`、`sha256`、帧号和时间等来源信息。读取时检查 episode 身份和图片 SHA256，不能用任意图片直接替换缓存而不更新来源记录。

## 4. 配置

| 字段 | 当前默认值及含义 |
| --- | --- |
| model_path | 本地 Hugging Face 模型目录 |
| cache_root | 已生成的首尾 PNG 缓存 |
| output_root | 逐图入口输出根目录；fast 使用自身 CLI 输出参数 |
| layer_from_end | 1：最后一个 block；2：倒数第二个，以此类推 |
| image_size | 224，完整画面缩放补边后的正方形边长 |
| batch_size | 16，仅逐图入口默认值，单位是图像 |
| device | cuda；CUDA 不可用时实际回退 CPU |
| dtype | bf16；CPU 或 BF16 不支持时回退 fp32 |
| save_patches | false；true 额外保存逐 patch 特征及有效权重 |
| limit_episodes | 0：全部；正数取 selection 前若干条 |
| force | false：优先复用校验通过的输出 |

**入口差异：** `fast.py` 的 `--batch-size` 默认512、`--io-workers` 默认16、`--limit-episodes` 默认0、`--output-root` 默认 `robodojo_embeddings_fast`、`--force` 默认关闭。这些直接使用 CLI 值，不继承 YAML 中同名值。fast 的分辨率、权重、设备等仍来自 YAML；修改这些值需要指定修改后的配置。

模型当前24个 block，`layer_from_end` 合法范围为1到24；代码以实际模型深度为准。最后一层取官方 `last_hidden_state`；其他层取指定 block 输出再施加最终 LayerNorm。仍执行完整模型，不代表提前截断计算。

图像不中心裁剪，使用 letterbox；尺寸必须整除 patch 大小。patch mean 和空间池化按有效区域覆盖面积加权，排除 CLS/register tokens。空间四象限按左上、右上、左下、右下拼接，再整体 L2 归一化。

## 5. 运行命令

路径中有空格，所有 shell 路径需加引号。Python 模块命令从框架根目录运行。

```bash
cd '/mnt/workspace/Subtask_Annotation _Review'
python -m episode_clustering.scene_clustering_dino.embedding_extraction.run --help
python -m episode_clustering.scene_clustering_dino.embedding_extraction.fast --help
python -m unittest episode_clustering.scene_clustering_dino.embedding_extraction.test_features

# 16个episode，共32张图；不等于batch=32。
python -m episode_clustering.scene_clustering_dino.embedding_extraction.run --limit-episodes 16 --batch-size 32

# 中间层、较大输入的小规模实验。
python -m episode_clustering.scene_clustering_dino.embedding_extraction.run --layer-from-end 4 --image-size 448 --batch-size 8 --limit-episodes 2

# 全量分片提取，512张/GPU批次，16个读图线程。
python -m episode_clustering.scene_clustering_dino.embedding_extraction.fast --batch-size 512 --io-workers 16
```

`run.py` 还支持 `--model-path`、`--cache-root`、`--output-root`、`--device`、`--dtype`、`--force` 等覆盖；以 `--help` 为准。fast 没有 `--image-size`，需改 YAML。CPU 大批量可能很慢，不应照搬 GPU batch512。

## 6. 输出与后续使用

逐图入口：`output_root/<配置签名前16位>/`，内含 `run_config.json`、`manifest.json`、`progress.json`、`failures.json` 以及每个 episode 的 `first.npz/json`、`last.npz/json`。

fast 入口：`robodojo_embeddings_fast/<签名前16位>/`，内含 `config.json`、`manifest.json`、`progress.json`、`failures.json`、`shards/00000.npz` 和对应 JSON。每片默认512张，最后一片可能不足。NPZ 行顺序与分片 JSON 的 `records` 一一对应。

| 特征 | 当前ViT-L维度 | 说明 |
| --- | --- | --- |
| cls | 1024 | CLS，FP32、L2归一化 |
| patch_mean | 1024 | 有效区域加权均值，FP32、L2归一化 |
| spatial_2x2 | 4096 | 四象限拼接，FP32、整体L2归一化 |
| patches / patch_valid_weights | 随网格变化 | 仅save_patches开启时保存 |

当前已有全量结果：`/mnt/data/chachaxu/embedding_cluster/robodojo_embeddings_fast/b82d9eb5438dcf56`，7000张、14片。该哈希是已有实验，不保证新环境或代码生成相同目录。
将包含 `config.json` 和分片清单的目录传给 `episode_clustering.scene_clustering_dino.run --features`。

## 7. 续跑、进度与故障

- 逐图入口检查模型/代码/设置签名、来源记录和输出哈希；符合则跳过，缺失或损坏时重算。改变层/尺寸通常创建新签名目录。
- fast 检查分片记录和哈希；命中仍会读取并核查源图，但跳过 GPU 推理和写入。batch 大小参与 fast 签名，修改后生成新目录。
- 逐图推理失败递归缩小 batch，直到定位单图；fast 的 GPU 或写分片错误会终止，**不会自动降低 batch**。降低 batch 重试会建立新签名目录。
- 完成看 `progress.json.status`：`complete` 或 `complete_with_failures`，同时检查 `failures.json`。中断时已有分片保留，manifest可能尚未最终写完，需续跑后再交下游。
- `completed_images` 包含本轮复用成功项；`skipped_images` 是其中缓存命中数。读图、推理、写入有重叠，worker耗时不能相加当作总耗时。
- 不允许两个进程同时写同一签名目录；当前无进程锁或挂载自动恢复。遇到缺文件、I/O异常先检查挂载，不要删除源图或盲目清空所有Python进程。
- 特征和来源记录存在绝对路径；迁移数据后须核对清单路径，不能只移动manifest而保留失效路径。

本模块保证来源可追溯和数值格式检查，不保证 embedding 能区分所有物体或正确判断任务结果。
