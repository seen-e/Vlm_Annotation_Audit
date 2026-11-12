# DINO 未归类样本的 VLM 补充分组

入口：`python -m episode_clustering.scene_clustering_dino.refine_noise`。
只新增后处理，不改变 DINO/HDBSCAN 算法，也不重复提取 embedding。

1. 读取已有 DINO 结果中各任务的 `clusters.json`。
2. 只挑出 `cluster_id=-1` 的 episode；按原任务独立处理。
3. 复用 `scene_clustering_vlm` 的两轮流程及原有两个提示词：默认每批50条，代表成员参与第二轮合并。最后不足50条也正常处理。
4. 不把 DINO 已归类成员传给 VLM；不向已有 DINO 簇归并、不做最近邻分配。
5. 将 DINO 原组和 VLM 新组并列展示，使用不同前缀避免组编号冲突。

仍然无法确认的 VLM 组保留 `uncertain`，单独显示“待确认”，不能宣传为已正确归类。VLM请求失败的任务保留原来的未归类成员，并记录失败；重跑同一命令复用已成功请求，不重新调用 DINO。

## 配置与执行

复用 `episode_clustering/scene_clustering_vlm/config.yaml`，无需另一份配置：

- `frame_mode`：first / last / first_last，可独立于原DINO特征模式选择。
- `episodes_per_batch`：默认50，为第一轮请求批大小，不是最终簇大小。
- `representatives_per_cluster`：默认3。
- `task_ids`：为空处理DINO结果中的全部任务；非空只处理指定任务。
- `task_workers`、`api`：沿用VLM的并发、模型、凭据和重试设置。
- 输出位置以命令行 `--output` 为准，不使用VLM配置里的 `output_root`。

必须使用新的独立输出目录。原DINO结果目录及其父子目录均不能作为输出，原文件不写回。

```bash
cd '/mnt/workspace/Subtask_Annotation _Review'
python -m episode_clustering.scene_clustering_dino.refine_noise \
  --dino-result /path/to/last_cls_hdbscan_result \
  --output /path/to/new_hybrid_result \
  --vlm-config /path/to/config.yaml \
  --dry-run

# 检查清单后，去掉 --dry-run 才调用 API。
```

没有未归类成员时，不读取API凭据、不发送请求，直接生成保留DINO原组的结果。
任务不足一个完整批次时仍进行第一轮及第二轮；超出第二轮图片容量时按现有VLM规则报错保留结果，不增加第三轮。
修改输入、提示词或模型后须使用新输出目录。一个输出目录只允许一个写入进程。

## 输出

```text
new_hybrid_result/
  plan.json / config_snapshot.json / prompt_snapshot.json
  progress.json / summary.json / failures.json
  vlm/task_000/round1/...      # 原始请求、图像及输出
  vlm/task_000/round2/...
  vlm/task_000/final.json      # 仅原未归类成员的VLM结果
  task_000/combined.json      # 全部成员，DINO与VLM并列
  viewer/index.html          # 使用相同查看页，新增来源筛选
```

`combined.json`中保留`dino_cluster_id`；新增`source`为`dino`、`vlm`或`unresolved`。最终组ID分别以`dino:`、`vlm:`开头。待确认数量与请求失败后尚未处理的数量分别统计。原DINO置信指标不会被伪造为VLM置信度。

实现已提供离线测试；新增后处理本身不会自动启动预测。
