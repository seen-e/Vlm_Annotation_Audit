# Subtask Annotation Review 模块导航

更新：2026-11-11。当前部署于1015：`/mnt/workspace/Subtask_Annotation _Review`。
目录名称在Annotation与_Review之间有一个空格，命令中务必引用完整路径。

## 模块入口

| 模块 | 用途 | 使用说明 |
| --- | --- | --- |
| embedding_extraction | 本地DINOv3首尾帧特征提取 | [ReadMe.md](embedding_extraction/ReadMe.md) |
| scene_clustering_dino | 基于向量的任务内HDBSCAN及可选噪声VLM分组 | [ReadMe.md](scene_clustering_dino/ReadMe.md) |
| scene_clustering_vlm | 首/尾帧两轮API聚类 | [ReadMe.md](scene_clustering_vlm/ReadMe.md) |
| manual_review_standard | 人工填写任务/组标准、导出并展开到episode | [ReadMe.md](manual_review_standard/ReadMe.md) |
| subtask_annotation_filter | 标准加最终标注的并发审核基础框架 | [ReadMe.md](subtask_annotation_filter/ReadMe.md) |

## 数据流

已有首尾帧缓存 → embedding_extraction.fast → scene_clustering_dino。
已有首尾帧缓存 → scene_clustering_vlm → manual_review_standard → 每episode审核标准。
每episode审核标准 + 最终subtask标注 → subtask_annotation_filter（首轮实验已获用户授权，结果需人工复核）。

DINO聚类与VLM聚类是可选分支，不要求先跑DINO再跑全量VLM。DINO中的refine_noise只对其未归类成员调用VLM。
人工标准模块当前接受VLM两轮输出；不能把DINO clusters.json直接当成它的final.json输入。

## 当前数据与边界

- RoboDojo缓存3500个episode，对应7000张首尾图像。
- 当前全量VLM结果35任务、199组；人工模块保存完整可视化包。
- 正式标准文件：`/mnt/data/chachaxu/dataset/robodojo/robodojo_review_standard.json`，3500条有效标准。
- subtask_annotation_filter已按用户要求启动3500条首轮实验，思考模式开启、episode并发48；不是已完成预测或已通过质量验收。
- 聚类分组不是异常标签，人工标准也不是模型审核结果。
- 各模块并发含义不同：特征batch按图像，VLM task_workers按任务，异常episode_batchsize按episode。

## 通用运行约定

```bash
cd '/mnt/workspace/Subtask_Annotation _Review'
```

Python模块命令从根目录执行；Node人工模块命令可切入对应模块目录。具体参数以模块ReadMe和--help为准。
不同实验使用独立输出目录。缓存复用条件、是否可断点续跑、是否写dry-run计划并不相同，不要跨模块套用。
模型凭据不写进文档；重命名前的外部配置可能仍含EmbeddingCluster_Exp旧路径，使用前检查。
本框架不统一管理挂载恢复或后台进程，严禁把所有Python进程作为默认故障清理目标。
生成的可视化是静态快照，浏览器保存标准使用localStorage；通过导出/导入JSON实现跨电脑迁移。

本次文档更新只修改使用说明，不修改模型、提示词、运行配置、实验结果或浏览器数据。
