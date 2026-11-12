# 递进式 VLM 聚类

```text
vlm_clustering/
  config.yaml                两个子模块配置文件的位置
  run.py                     image / text / all 统一入口
  image_base_clustering/     原图像两轮聚类，首帧/尾帧/首尾帧可选
  txt_base_clustering/       场景组内部的两轮文本结果聚类
```

先在每个任务内基于图像分出场景组，再在各场景组内部比较文本描述的最终实现结果。
文本步骤不跨越图像组边界。小于配置阈值的组原样保留，默认小于3条跳过。
这是聚类，不是异常审核；抽样审核由独立的 episode_level_filter 等模块处理。

## 配置与运行

```bash
cd '/mnt/workspace/Subtask_Annotation _Review'
# 使用已有图像分组进行文本预检，不调用API。
python -m episode_clustering.vlm_clustering.run --stage text --dry-run
# 单独执行文本两轮聚类。
python -m episode_clustering.vlm_clustering.run --stage text
# 依次执行图像两轮和文本两轮。
python -m episode_clustering.vlm_clustering.run --stage all
```

config.yaml 指定 image_config 和 text_config，路径相对该文件。各子模块模型、并发、
每批成员、代表数及输出目录分别配置。all 模式将本次图像配置的 output_root 自动传给
文本步骤，不误用其他历史结果。图像失败会中止。text 模式使用文本配置的 image_results_root。

all 的 dry-run 不调用API。图像模块沿用原行为，保存 dry_run_plan.json；如果图像结果
尚不存在，则打印文本预检待完成图像分组。存在但不完整的图像结果会报错。

## 子模块说明

- [图像分组说明](image_base_clustering/ReadMe.md)
- [文本分组说明](txt_base_clustering/ReadMe.md)

旧 episode_clustering.scene_clustering_vlm 入口改为
episode_clustering.vlm_clustering.image_base_clustering，图像提示词及请求内容未更改。
历史结果保留原路径，冻结配置中的旧代码路径不重写。
凭据路径相对配置文件，外部自定义配置使用相对路径时需核对位置。
