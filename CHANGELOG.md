# 版本记录

## v0.0.2 - 2026-11-12

相对于 v0.0.1，本版新增图像分组后的文本结果细分，并整理审核与聚类模块目录。
这是实验框架版本，不表示文本聚类质量已经验收。

### 新增

- `episode_clustering/vlm_clustering/txt_base_clustering`：每个任务的每个图像场景组独立执行两轮文本聚类，不跨任务或父场景组。
- 第一轮分批读取 episode 最终标注，提出结果组并选择代表；第二轮使用代表的原始标注文本及候选组信息合并结果组，再展开全部成员。
- `round1_prompt.py` 和 `round2_prompt.py` 分别保存完整提示词。允许物体名称、颜色、形状的可解释识别偏差，结合角色、关系、完整过程及终态分组；不抹除明确的数量、排列或关键身份差异。
- 可配置小组跳过阈值 `text_clustering_min_group_size`，默认3，严格小于阈值的组原样保留，标为未评估而不是审核通过。
- 默认第一轮每批最多20个 episode、16个父组并发、每个候选组最多3个代表，两轮均开启思考模式。20仅限制第一轮请求成员数，不限制最终类别大小。
- 统一入口 `episode_clustering.vlm_clustering.run`，支持 `--stage image|text|all`；all 模式把本次图像结果传给文本步骤，图像失败时不继续文本阶段。
- 输入完整性预检、源文件哈希和配置/提示词签名、请求结果校验与缓存复用、失败请求重试、输出锁、进度及失败汇总、代表样本列表。
- 文本 dry-run 只读取并检查输入，不调用API、不写预测结果。文本分组状态不等于审核结论，不自动触发 episode 审核或视频重标注。

### 目录与入口变更

| v0.0.1 | v0.0.2 |
| --- | --- |
| `episode_clustering/scene_clustering_vlm/` | `episode_clustering/vlm_clustering/image_base_clustering/` |
| `annotatiuon_audit/` 下的审核代码与查看器 | `annotatiuon_audit/episode_level_filter/` |
| `annotatiuon_audit/group_level_filter/` 临时预留目录 | 以 `txt_base_clustering` 实现文本分组，不保留独立组级审核占位目录 |

- 更新 DINO 噪声补充分组、标准翻译模块及启动工具的导入与入口路径。
- 审核配置的相对凭据路径调整为 `../../.credentials/xb4v.yaml`。
- 图像聚类核心代码、两轮提示词及 DINO 特征提取代码保持不变；历史共享盘结果不移动、不覆盖。
- 保留此前查看器的每任务导出数量设置与全部异常导出能力：`--max-per-task 0`，以及 `--workers` 配置。
- 移除框架内的 `annotatiuon_audit/vlm_subtask_reasoning` 副本；不删除框架外的独立项目。
- 补充各子模块 ReadMe 与新的配置说明。保留用户指定目录拼写 `annotatiuon_audit`。

旧模块入口不再兼容，外部脚本需调整。例如：

```bash
python -m episode_clustering.vlm_clustering.image_base_clustering.run --help
python -m episode_clustering.vlm_clustering.run --stage text --dry-run
python -m annotatiuon_audit.episode_level_filter.run --dry-run
```

部署前检查配置中的数据路径、输出目录和凭据路径。默认配置含实验环境绝对路径，并非开箱即用的公共数据集路径。
本版本不包含API密钥、视频、embedding、实验输入输出或浏览器导出包。

### 验证与已知限制

- 52项离线单元测试通过，覆盖 DINO、图像聚类、文本聚类、episode审核和审核标准翻译；测试不调用真实API。
- 3500条真实输入预检通过：35个任务、199个图像组；106个小组共109条跳过，其余93组进入文本聚类。
- 2026-11-12 的 group20 实验结束：84/93组完成两轮，9组因长度截断耗尽重试；含跳过成员共覆盖2645/3500条，另855条尚无完整文本分组结果。该实验不是全量成功验收。
- 当前配置 `max_tokens=16384`，长度截断仍以相同参数重试，不会自动提高额度、拆分请求或增加聚类轮数。调整模型生成参数需使用新的输出目录。
- 第二轮输入长度随代表数量增加，存在长输入和输出截断风险。改变配置或输入会拒绝复用旧输出目录，防止混用实验结果。
- 结果有过度细分迹象：84个已处理父组形成582个文本子组，其中444个为单成员。识别偏差、遮挡和不确定性可能造成拆分；仍需人工评估提示词和分组粒度。
- 不承诺抽样成员能代表整组的审核结论；后续抽样策略与异常审核需独立验证。

## v0.0.1 - 2026-11-12

首次打标版本，包含 DINO/VLM 场景聚类、人工/API审核标准生成、episode标注审核及相关工具。
对应提交 `f484824dc37199105ec6ca37d316df237c9d71d1`，保留原标签不变。
