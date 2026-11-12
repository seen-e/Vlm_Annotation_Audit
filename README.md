# Vlm_Annotation_Audit
基于图文进行episode级的异常标注结果筛查

## 框架模块

本分支同步自 1015 的 `/mnt/workspace/Subtask_Annotation _Review`，保留业务代码、提示词和配置。

| 模块 | 功能 | 使用说明 |
| --- | --- | --- |
| `embedding_extraction` | DINOv3 首尾帧 embedding 批量提取 | [ReadMe](embedding_extraction/ReadMe.md) |
| `scene_clustering_dino` | HDBSCAN 场景聚类及未归类结果的 VLM 补充聚类 | [ReadMe](scene_clustering_dino/ReadMe.md) |
| `scene_clustering_vlm` | 基于首帧、尾帧或首尾帧的两轮 API 聚类 | [ReadMe](scene_clustering_vlm/ReadMe.md) |
| `manual_review_standard` | 人工填写任务/组标准并展开到 episode | [ReadMe](manual_review_standard/ReadMe.md) |
| `subtask_annotation_filter` | 审核标准与最终标注的并发异常筛查 | [ReadMe](subtask_annotation_filter/ReadMe.md) |

附属模块：`subtask_annotation_filter/vlm_subtask_reasoning` 为纯文本推理描述补充；
`subtask_annotation_filter/visualization` 为异常候选查看器导出工具。

## 配置与运行

从仓库根目录执行 Python 模块命令。各配置中的 `/mnt/data` 路径是现有实验环境路径，
换机器时需配置可访问的数据目录。安装依赖前请核对 CUDA 环境，恢复时验证过的版本见
[requirements_verified.txt](recovery_20261112/requirements_verified.txt)。人工标准构建需要 Node.js；恢复时验证版本为 24.22.1。

API 密钥不进入 Git。异常筛查默认读取 `.credentials/xb4v.yaml`，可参照
[凭据格式示例](.credentials.example.yaml) 在本机配置。VLM 聚类默认使用配置中指定的环境变量，
也可配置 `api.credentials_file`。附属推理模块的 `config.ymal` 保留历史路径，使用前检查
`model.credential_config`，或将其设为空并使用该模块的环境变量。

```bash
python -m embedding_extraction.fast --help
python -m scene_clustering_dino.run --help
python -m scene_clustering_vlm.run --help
python -m subtask_annotation_filter.run --dry-run

# 离线单元测试，不调用模型 API。
python -m unittest embedding_extraction.test_features scene_clustering_dino.test_clustering scene_clustering_dino.test_refine_noise scene_clustering_vlm.test_pipeline subtask_annotation_filter.test_pipeline
```

## 同步范围

仓库包含源码、提示词、配置、页面模板、测试与说明；不包含 API 密钥、模型权重、
图像/视频、已生成查看器数据包、批量实验输入输出或运行日志。
这些实验资产仍保存在共享盘和本地备份中，不能仅凭本仓库打开历史数据可视化。

未重新整理或修改模块内部业务逻辑；保留原文件换行，避免改变 embedding 的源码签名。
远程源文件 SHA256 清单见 [source_manifest.json](recovery_20261112/source_manifest.json)，
其中根目录 `ReadMe.md` 在仓库内对应 `FRAMEWORK.md`。

## 恢复与核验

- [框架详细导航](FRAMEWORK.md)
- [恢复记录](RECOVERY_20261112.md)
- [历史输入输出一致性核验](docs/equivalence_20261112/ReadMe.md)

核验采用历史实验配置，并固定已保存的 API 原始响应，不代表重新请求 API 会得到相同生成结果。
默认 VLM 配置的任务并发为 4；历史全量实验使用 16，运行时应显式核对配置。
