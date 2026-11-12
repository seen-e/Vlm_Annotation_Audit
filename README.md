# Vlm_Annotation_Audit
基于图文进行episode级的异常标注结果筛查

## 框架模块

本分支同步自 1015 的 `/mnt/workspace/Subtask_Annotation _Review`，保留业务代码、提示词和配置。

| 模块 | 功能 | 使用说明 |
| --- | --- | --- |
| `episode_clustering` | Episode 场景聚类：DINO/HDBSCAN 与两轮 VLM API 聚类 | [ReadMe](episode_clustering/ReadMe.md) |
| `review_standard` | 审核标准生成：人工填写、原始指令 API 翻译并映射到 episode | [ReadMe](review_standard/ReadMe.md) |
| `annotatiuon_audit` | Episode 级并发审核及异常候选查看器 | [ReadMe](annotatiuon_audit/ReadMe.md) |

`annotatiuon_audit/episode_level_filter/visualization` 为异常候选查看器导出工具。
DINO 特征提取位于 `episode_clustering/scene_clustering_dino/embedding_extraction`，
使用说明见其 [ReadMe](episode_clustering/scene_clustering_dino/embedding_extraction/ReadMe.md)。

## 配置与运行

从仓库根目录执行 Python 模块命令。各配置中的 `/mnt/data` 路径是现有实验环境路径，
换机器时需配置可访问的数据目录。安装依赖前请核对 CUDA 环境，恢复时验证过的版本见
[requirements_verified.txt](recovery_20261112/requirements_verified.txt)。人工标准构建需要 Node.js；恢复时验证版本为 24.22.1。

API 密钥不进入 Git。异常筛查默认读取 `.credentials/xb4v.yaml`，可参照
[凭据格式示例](.credentials.example.yaml) 在本机配置。VLM 聚类默认使用配置中指定的环境变量，
也可配置 `api.credentials_file`。

```bash
python -m episode_clustering.scene_clustering_dino.embedding_extraction.fast --help
python -m episode_clustering.scene_clustering_dino.run --help
python -m episode_clustering.vlm_clustering.image_base_clustering.run --help
python -m episode_clustering.vlm_clustering.run --stage text --dry-run
python -m annotatiuon_audit.episode_level_filter.run --dry-run
python -m review_standard.vlm_review_standard.run --dry-run

# 离线单元测试，不调用模型 API。
python -m unittest episode_clustering.scene_clustering_dino.embedding_extraction.test_features episode_clustering.scene_clustering_dino.test_clustering episode_clustering.scene_clustering_dino.test_refine_noise episode_clustering.vlm_clustering.image_base_clustering.test_pipeline episode_clustering.vlm_clustering.txt_base_clustering.test_pipeline annotatiuon_audit.episode_level_filter.test_pipeline review_standard.vlm_review_standard.test_pipeline
```

## 同步范围

版本改动及迁移说明见 [CHANGELOG.md](CHANGELOG.md)。

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
