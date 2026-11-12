# scene_clustering_dino 使用说明

特征提取子模块：[`embedding_extraction`](embedding_extraction/ReadMe.md)。
先提取首尾帧特征，再将分片目录传入本模块的 `--features` 参数。

更新：2026-11-11。部署位置：1015，`/mnt/workspace/Subtask_Annotation _Review/episode_clustering/scene_clustering_dino`。

## 1. 用途

读取已有 DINOv3 特征，在每个任务内部用 HDBSCAN 聚类。不跨任务合并，不重新运行 DINO，不从视频解码图像。首尾模式表达初始场景加最终状态的候选相似性，不等同于初始场景真值或 subtask 正确性。

可选 `refine_noise.py` 仅把未归类成员交给 VLM，再将 VLM 新组与 DINO 原组并列展示。该步骤需显式启动，会产生 API 费用；普通 `run.py` 不调用 API。

## 2. 文件与依赖

| 文件 | 用途 |
| --- | --- |
| run.py | 读取特征、任务映射、HDBSCAN、统计和查看器 |
| viewer.html | DINO及混合结果页面模板 |
| refine_noise.py | 未归类成员的两轮VLM补充分组 |
| test_clustering.py | 向量组合、聚类和小样本测试 |
| test_refine_noise.py | DINO/VLM并列合并和覆盖测试 |
| NOISE_VLM.md | 历史补充分组设计说明，运行路径以本文件为准 |

依赖：Python、NumPy、Pillow、PyArrow、提供 `sklearn.cluster.HDBSCAN` 的 scikit-learn（1.3及以上）。补充分组还依赖 `vlm_clustering/image_base_clustering`、PyYAML、OpenAI SDK。普通聚类无需GPU。

## 3. 输入约束

`--features` 必须指向 **episode_clustering.scene_clustering_dino.embedding_extraction.fast 的分片输出目录**，含 `config.json`、`manifest.json`、分片NPZ及records。当前不能直接读取逐图入口输出。

每个episode必须同时具有first/last记录，即便选择last_cls，当前代码仍要求首尾配对，用于身份、任务核对及预览图。图片来源和分片路径从记录中读取并核查SHA256，源图不可丢失。

任务映射来自数据集 `meta/tasks.parquet`，需要 `task_index` 和 `__index_level_0__`（原始任务文本）字段。每个episode当前只支持一个任务文本，必须精确匹配，不按episode编号猜任务。

## 4. 参数与算法

本模块普通入口目前使用 CLI，**没有独立config.yaml**。

| 参数 | 默认/说明 |
| --- | --- |
| --features | `.../robodojo_embeddings_fast/b82d9eb5438dcf56` |
| --dataset | `/mnt/data/chengyezhao/RoboDojo_ee_lerobot_v30_video` |
| --output | 必填且必须不存在，避免覆盖实验 |
| --feature-mode | dual_cls 或 last_cls，默认dual_cls |
| --min-cluster-size | 5 |
| --min-samples | 2 |

dual_cls：首尾CLS分别L2归一化，分别乘sqrt(0.5)，拼接后整体归一化，当前2048维。last_cls：只用L2归一化尾帧CLS，当前1024维，首帧仅展示。
不加入空间特征，不做PCA/UMAP。HDBSCAN固定euclidean、eom、allow_single_cluster=True。
1个样本生成临时单组并标记insufficient_samples；2到7个样本全部未分配；8到29个样本实际min_cluster_size使用min(3,配置值)，30个及以上才直接使用配置值。

## 5. 运行

```bash
cd '/mnt/workspace/Subtask_Annotation _Review'
python -m episode_clustering.scene_clustering_dino.run --help
python -m unittest episode_clustering.scene_clustering_dino.test_clustering

# 必须换成未存在的新输出目录。
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -m episode_clustering.scene_clustering_dino.run \
  --features /mnt/data/chachaxu/embedding_cluster/robodojo_embeddings_fast/b82d9eb5438dcf56 \
  --feature-mode dual_cls --min-cluster-size 5 --min-samples 2 \
  --output /mnt/data/chachaxu/embedding_cluster/robodojo_experiments/NEW_DUAL_RUN
```

尾帧实验将feature-mode换成last_cls，并更换输出目录。任务聚类在主循环逐任务执行；预览图生成使用16个线程，此处没有episode_batchsize或任务并发配置。

## 6. 输出与查看

```text
output/
  run_config.json / feature_config.json
  summary.json / failures.json / episode_clusters.csv
  task_000/
    clusters.json
    metrics.json
    episode_embeddings.npz
  viewer/index.html / data.js / images/
```

NPZ含episode_ids、embedding、labels。查看器首帧在左、尾帧在右，每张缩略图最多320×240，完整画面等比缩放。整个viewer目录可复制到本地离线打开；它不是公开网站。

标签 `-1` 为密度聚类未归类成员，**不是任务失败、异常标注或坏图**。membership_strength是密度归属指标，不是语义正确概率。
Medoid为簇内平均余弦相似度最高成员；中心最多8条，边缘最多5条；小簇两组可重叠。跨簇近邻最多10对。多簇/单簇/uncertain均为候选状态，需要人工判断。

当前已有实验：

| 模式 | 结果目录尾名 | 覆盖 | 簇数 | 未归类 |
| --- | --- | --- | --- | --- |
| dual_cls | dual_cls_hdbscan_20261111_v1 | 35任务/3500条 | 37 | 843 |
| last_cls | last_cls_hdbscan_20261111_v1 | 35任务/3500条 | 37 | 978 |

两者均在 `/mnt/data/chachaxu/embedding_cluster/robodojo_experiments/` 下。这是已完成实验记录，不是新运行的保证。

## 7. 对未归类成员做VLM补充分组

复用 `episode_clustering/vlm_clustering/image_base_clustering/config.yaml` 的图像模式、每批数量、代表数、task_workers和API配置；输出由此入口的 `--output` 决定。

```bash
cd '/mnt/workspace/Subtask_Annotation _Review'
python -m episode_clustering.scene_clustering_dino.refine_noise \
  --dino-result /mnt/data/chachaxu/embedding_cluster/robodojo_experiments/last_cls_hdbscan_20261111_v1 \
  --output /mnt/data/chachaxu/embedding_cluster/robodojo_experiments/NEW_HYBRID_RUN \
  --vlm-config episode_clustering/vlm_clustering/image_base_clustering/config.yaml --dry-run
```

检查dry_run_plan后去掉dry-run才调用API。没有未归类成员时不会读取凭据或发请求。输出不能等于原DINO目录或与之构成父子目录。

只处理cluster_id=-1，不把噪声就近分给旧簇，不修改已归类成员。每个任务第一轮分批、第二轮合并完整候选组；模型仍可能返回uncertain，不强制宣称全部归类正确。失败时保留unresolved成员。

结果增加 `vlm/task_*/` 原始请求、`task_*/combined.json` 及混合查看器；组ID以dino:或vlm:区分，source为dino/vlm/unresolved，原dino_cluster_id保留。该后处理尚不应被当成已全量跑过的结果。

## 8. 进度、重试与限制

普通入口从终端逐任务日志、最终summary.json和failures.json判断进度。它不提供任务级断点续跑，目录存在就拒绝启动；失败后保留现场，改用新输出目录重跑。缺首尾对记录在failures中，严重映射/哈希/图像错误可直接中止。
补充分组有progress.json和签名校验；重跑同配置复用成功API请求，继续失败项。更换输入、提示词或模型应使用新目录。两个写进程不能共享输出目录。
全部特征相似度矩阵按任务计算，当前每任务100条规模；超大任务需要评估内存及平方级开销。挂载恢复、自动参数搜索和聚类语义准确率评估不在此模块内。
