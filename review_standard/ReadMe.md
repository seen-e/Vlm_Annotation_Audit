# 审核标准生成

统一管理两种审核标准生成方式，不负责异常筛查，也不自动合并两种标准。

```text
review_standard/
  manual_review_standard/  人工任务/组标准，展开到每个 episode
  vlm_review_standard/     原始指令经 API 翻译，映射到每个 episode
```

## 人工标准

[详细说明](manual_review_standard/ReadMe.md)。支持任务默认标准和组内覆盖标准。
已有 `datasets`、人工导出文件、页面和图像均随目录移动，不重新生成或覆盖。

```bash
cd '/mnt/workspace/Subtask_Annotation _Review/review_standard/manual_review_standard'
node build_viewer.cjs config.json
```

## API 翻译标准

[详细说明](vlm_review_standard/ReadMe.md)。仅忠实翻译原始指令，不扩写。
在框架根目录运行：

```bash
cd '/mnt/workspace/Subtask_Annotation _Review'
python -m review_standard.vlm_review_standard.run --dry-run
python -m review_standard.vlm_review_standard.run
python -m unittest review_standard.vlm_review_standard.test_pipeline -v
```

凭据仍放在框架根目录的 `.credentials`，配置中的相对路径已调整为
`../../.credentials/xb4v.yaml`。输出仍保存到原有共享盘路径，不随源码目录改名。

## 接入筛查

两种输出都提供 `episodes[episode_id].review_standard`。选择所需文件作为
`annotatiuon_audit/episode_level_filter/config.yaml` 的 `standards_path`，并为新实验设置独立
`output_root`。本次目录调整不改变当前筛查配置、人工标准或任何模型预测结果。

原根目录的两个模块已迁入此目录，旧模块命令及页面路径不再有效。
根目录旧恢复清单和历史核验报告保留原路径，作为历史记录，不进行改写。
浏览器 localStorage 不属于文件，移动后的页面可能使用不同的存储空间；
已导出的人工标准文件保留，未导出的浏览器编辑需在原页面导出后再导入。
