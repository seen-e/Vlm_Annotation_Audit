# Episode 聚类

统一管理两种场景聚类实现：

```text
episode_clustering/
  scene_clustering_dino/  DINO embedding + HDBSCAN，支持噪声样本 VLM 补充分组
    embedding_extraction/ DINOv3 首尾帧特征批量提取
  scene_clustering_vlm/   使用首帧、尾帧或首尾帧，通过 API 两轮分组
```

- [DINO 使用说明](scene_clustering_dino/ReadMe.md)
- [DINO 特征提取](scene_clustering_dino/embedding_extraction/ReadMe.md)
- [VLM 使用说明](scene_clustering_vlm/ReadMe.md)

从框架根目录运行：

```bash
cd '/mnt/workspace/Subtask_Annotation _Review'
python -m episode_clustering.scene_clustering_dino.run --help
python -m episode_clustering.scene_clustering_dino.embedding_extraction.fast --help
python -m episode_clustering.scene_clustering_dino.refine_noise --help
python -m episode_clustering.scene_clustering_vlm.run --help
python -m episode_clustering.scene_clustering_vlm.build_viewer --help
python -m unittest episode_clustering.scene_clustering_dino.test_clustering episode_clustering.scene_clustering_dino.test_refine_noise episode_clustering.scene_clustering_vlm.test_pipeline
```

VLM 配置位于 `scene_clustering_vlm/config.yaml`；DINO 沿用命令行参数配置。
DINO 噪声补充分组默认从同级的 VLM 模块读取配置。
若自定义配置使用相对路径，路径仍相对于该配置文件解析；移动自定义配置时
须检查数据、输出和凭据的相对路径。默认配置使用的绝对数据路径保持不变。

本次仅组织代码目录并适配入口、导入及文档，不改变聚类参数、提示词或业务算法。
`embedding_extraction` 位于 DINO 聚类目录内；审核标准生成位于 `review_standard`。
共享盘的历史聚类结果、embedding、图像及 API 输入输出不移动、不覆盖。
历史恢复清单和核验报告保留原路径作为记录，旧顶层模块命令需改用上面的新入口。
