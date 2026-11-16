# 标注审核

```text
annotatiuon_audit/
  episode_level_filter/  现有单 episode 审核，包含原模块全部文件和可视化工具
  keyframe_grounded_filter/  最终标注 + Stage4关键帧图像的并列多模态审核
```

## Episode 级审核

[详细使用说明](episode_level_filter/ReadMe.md)。原有代码、配置、提示词、日志、
可视化导出等均移动到此子目录；共享盘上的审核结果路径不变。

从框架根目录运行：

```bash
cd '/mnt/workspace/Subtask_Annotation _Review'
python -m annotatiuon_audit.episode_level_filter.run --help
python -m annotatiuon_audit.episode_level_filter.run --dry-run
python -m annotatiuon_audit.episode_level_filter.run
python -m unittest annotatiuon_audit.episode_level_filter.test_pipeline -v
```

配置文件为 `episode_level_filter/config.yaml`，凭据仍位于框架根目录的
`.credentials/xb4v.yaml`，配置相对路径已调整为 `../../.credentials/xb4v.yaml`。
外部启动脚本若引用旧的 `annotatiuon_audit.run`，需改用上面的新入口。
历史日志、冻结配置和恢复报告不改写，也不会因整理目录启动预测任务。

## 关键帧多模态审核

[详细使用说明](keyframe_grounded_filter/ReadMe.md)。该模块同时核对标注与画面的一致性
以及实际结果是否满足审核标准，并明确区分“执行失败但标注正确”和“标注错误”。
当前提示词尚待共同审核，默认只允许 dry-run，不会自动调用API。

## 前置文本分组

原 group_level_filter 预留目录已移至
[txt_base_clustering](../episode_clustering/vlm_clustering/txt_base_clustering/ReadMe.md)。
它在图像场景组内部按文本最终结果继续分组，并非审核模块，不代替单 episode 审核。
