# 文本结果聚类

在图像场景组内部，按每个 episode 标注所描述的最终实现结果继续分组。
只分组，不判断正确或错误，不修改原始标注，不调用视频或图像。

## 两轮流程

1. 验证图像聚类已完整结束，成员与 assignments、summary 数量一致。
2. 每个父场景组独立处理。人数小于 `text_clustering_min_group_size` 时保留原组。
3. 其余组按固定随机种子打乱，每批最多 `episodes_per_batch` 条。第一轮按终态分组并选代表。
4. 第二轮输入本父组所有第一轮类别和代表成员的原始文本，合并相同终态的类别。
5. 展开成员，输出父场景组到文本子组的映射，以及后续抽样审核的代表列表。

不跨任务或父场景组。一个成员只能属于一个最终子组。第一轮 uncertain 成员不得在
第二轮被合并或升级为确定。超出文本上限会明确失败，不截断、不新增第三轮。

名称、颜色、形状可能是模型识别偏差，不能单凭外观词差异拆组；结合角色、关系和
完整过程理解。不能以“识别偏差”为由抹除明确的数量、排列、关键身份或终态差异。
两轮完整提示词分别位于 `round1_prompt.py`、`round2_prompt.py`，可独立审核修改。

## 输入与配置

`config.yaml` 是本模块配置，路径相对配置文件解析：

| 配置 | 用途 |
| --- | --- |
| image_results_root | 图像聚类完整输出，含 summary.json 和 task_*/final.json |
| annotations_root / annotation_pattern | 原始 episode final.json，按 episode_id 精确匹配 |
| output_root | 独立新目录，禁止覆盖或嵌套输入目录 |
| text_clustering_min_group_size | 默认3，严格小于3才跳过；等于3正常执行 |
| episodes_per_batch | 第一轮每请求最多20个成员，不是并发数；不限制最终类别大小 |
| representatives_per_cluster | 默认每组最多3个代表 |
| group_workers | 默认16个父场景组并发；同一组内两轮依次执行 |
| input_workers | 默认32线程读取并校验输入 |
| max_request_chars | 序列化输入正文字符上限，不是token上限 |
| shuffle_seed / task_ids | 可复现打乱顺序；空任务列表表示全部 |
| api | 凭据、thinking、温度、输出上限、超时及重试等 |

模型输入包括 task、父组ID、episode_id、terminal_summary、按顺序排列的
segment_summary、arm_1/arm_2 的 description/outcome 及 uncertainty。
不传旧审核结论、episode_success、boundary_evidence 或另外生成的操作指令。
所有字段仍是模型标注文本，不代表独立验证的事实。
小组跳过时不读取对应标注；其余成员缺失或格式错误，预检会在API调用前停止。

## 使用

```bash
cd '/mnt/workspace/Subtask_Annotation _Review'
python -m episode_clustering.vlm_clustering.txt_base_clustering.run --dry-run
python -m episode_clustering.vlm_clustering.txt_base_clustering.run --config episode_clustering/vlm_clustering/txt_base_clustering/config.yaml
python -m unittest episode_clustering.vlm_clustering.txt_base_clustering.test_pipeline -v
```

本模块 dry-run 只读取输入、验证和打印预计请求数，不调用API、不写结果。
可使用 `--image-results-root` 和 `--output-root` 覆盖配置。

## 输出与续跑

- identity.json、plan.json：来源文件哈希、成员、配置和提示词签名，不含API key。
- config_snapshot.json、prompt_snapshot.json：模型参数和提示词。
- task_NNN/scene_groups/父组ID/round1/batch_NNN/：输入、每次响应、错误及校验后结果。
- 同父组下的 round2/、round1_candidates.json、final.json：第二轮及完整组结果。
- 根目录 final.json、assignments.json：汇总及每个 episode 的父组/最终组映射。
- representative_samples.json：后续审核候选样本，不自动启动审核。
- progress.json、failures.json、summary.json：进度、失败组和最终计数。

小组保留原 cluster_id，状态 skipped_small_group / not_evaluated，并非审核通过。
正常文本组有新子组ID并保留 parent_scene_group_id。所有输出的 audit_status 均为
not_reviewed。代表样本从第一轮代表中轮流选择，不保证其结论代表整组，需另定抽样策略。

相同命令复用通过校验且签名一致的请求；失败请求继续重试。更换提示词、输入或配置须
换输出目录。失败组不会被静默丢弃或冒充成功，批次状态为 complete_with_failures，
程序返回非零。锁目录防止并发写同一输出；异常退出后先确认没有活动进程，再人工移除
陈旧 `.run.lock`。
