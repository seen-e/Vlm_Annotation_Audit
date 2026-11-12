# Episode 聚类

统一管理两种场景聚类实现：

```text
episode_clustering/
  scene_clustering_dino/  DINO embedding + HDBSCAN，支持噪声样本 VLM 补充分组
    embedding_extraction/ DINOv3 首尾帧特征批量提取
  vlm_clustering/
    image_base_clustering/ 首帧、尾帧或首尾帧，API 两轮场景分组
    txt_base_clustering/   在场景组内按最终结果做两轮文本分组
```

- [DINO 使用说明](scene_clustering_dino/ReadMe.md)
- [DINO 特征提取](scene_clustering_dino/embedding_extraction/ReadMe.md)
- [VLM 递进流程](vlm_clustering/ReadMe.md)
- [图像分组](vlm_clustering/image_base_clustering/ReadMe.md)
- [文本分组](vlm_clustering/txt_base_clustering/ReadMe.md)

从框架根目录运行：

```bash
cd '/mnt/workspace/Subtask_Annotation _Review'
python -m episode_clustering.scene_clustering_dino.run --help
python -m episode_clustering.scene_clustering_dino.embedding_extraction.fast --help
python -m episode_clustering.scene_clustering_dino.refine_noise --help
python -m episode_clustering.vlm_clustering.image_base_clustering.run --help
python -m episode_clustering.vlm_clustering.image_base_clustering.build_viewer --help
python -m episode_clustering.vlm_clustering.run --stage text --dry-run
python -m unittest episode_clustering.scene_clustering_dino.test_clustering episode_clustering.scene_clustering_dino.test_refine_noise episode_clustering.vlm_clustering.image_base_clustering.test_pipeline
```

VLM 配置位于 `vlm_clustering/image_base_clustering/config.yaml`；DINO 沿用命令行参数配置。
DINO 噪声补充分组默认从同级的 VLM 模块读取配置。
若自定义配置使用相对路径，路径仍相对于该配置文件解析；移动自定义配置时
须检查数据、输出和凭据的相对路径。默认配置使用的绝对数据路径保持不变。

原图像聚类算法和提示词保持不变。新增文本聚类仅在父场景组内细分；
默认小于3个成员的组跳过文本步骤，阈值可配置。分组不等于审核通过。
`embedding_extraction` 位于 DINO 聚类目录内；审核标准生成位于 `review_standard`。
共享盘的历史聚类结果、embedding、图像及 API 输入输出不移动、不覆盖。
历史恢复清单和核验报告保留原路径作为记录，旧顶层模块命令需改用上面的新入口。
