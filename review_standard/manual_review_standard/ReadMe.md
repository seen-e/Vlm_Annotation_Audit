# manual_review_standard 使用说明

更新：2026-11-12。部署位置：1015/1013，`/mnt/workspace/Subtask_Annotation _Review/review_standard/manual_review_standard`。

## 1. 用途与工作流

在聚类可视化中填写任务级和组级审核标准，将其展开为每个episode的有效标准，供异常筛选使用。本模块不调用模型、不判断标注正误、不修改聚类成员。

流程：构建或打开页面 → 选择任务 → 填任务默认标准 → 逐组填写可选覆盖标准 → 保存 → 导出JSON → 展开到episode → 将结果交给annotatiuon_audit。

## 2. 文件结构

```text
manual_review_standard/
  config.json
  build_viewer.cjs
  quick_viewer.html
  review_standards.js
  expand_review_standards.cjs
  datasets/robodojo3500/
    summary.json
    task_000/final.json
    task_000/round1_candidates.json
    viewer/index.html
    viewer/data.js
    viewer/review_standards.js
    viewer/images/
    standards/
      robodojo_review_standards_2026-11-11T10-33-05-569Z.json
      robodojo_review_standard.json
```

数据包含35任务、199组、3500个episode及3500张首尾配对缩略图。每张左首右尾。标准导出原件和展开文件分别保存，不能互相当作同一种格式。
运行构建/展开需要现代Node.js（建议18及以上），无需npm依赖。查看器仅需浏览器，不需Python/API服务。
当前1015的SSH默认PATH中未找到node命令；可先在已具备Node.js的本地环境构建，或者配置实例的Node.js路径后运行。已生成页面不依赖Node.js运行。

## 3. 构建配置

| config.json字段 | 说明 |
| --- | --- |
| results_root | 两轮VLM聚类结果根目录，含summary和task_*/final、round1_candidates |
| images_root | 首左尾右的配对JPEG目录，文件名为episode_id.jpg |
| output_root | 独立可视化包输出目录，默认datasets/robodojo3500 |

相对路径基于config.json目录，不是当前shell位置。默认输入指向此前全量VLM结果和DINO尾帧查看器的缩略图缓存，复用图像不意味着使用DINO组归属。

```bash
cd '/mnt/workspace/Subtask_Annotation _Review/review_standard/manual_review_standard'
node build_viewer.cjs config.json
```

构建前核对全部任务、组、episode和图片覆盖，复制原始结果及图片，生成viewer。当前只接受完整summary匹配的数据包，不提供部分任务过滤参数。
构建器会更新目标页面、data.js和原始结果副本；不要把不同聚类版本写到同一个标准编辑数据包里，建议更换output_root。
本模块保留一份独立源码，后续修改优先改这里，不需要修改旧scene_clustering_vlm中的兼容页面。

## 4. 打开和填写

当前远程页面：
`/mnt/workspace/Subtask_Annotation _Review/review_standard/manual_review_standard/datasets/robodojo3500/viewer/index.html`。

可将整个datasets/robodojo3500复制到本地再打开viewer/index.html。保留task_*目录才能使用原始结果JSON链接，保留viewer/images才能显示图像。只有HTML一个文件不够。远程文件路径不是其他电脑可直接访问的公开网址。

1. 左上选择任务，左侧任务默认审核标准适用于该任务没有独立标准的组。
2. 点击左侧组，右侧顶部本组独立审核标准输入框对应当前组，标签显示任务ID和组ID。
3. 分别点击保存任务标准或保存本组标准，旁边显示保存结果和时间。输入本身也自动保存。
4. 右侧当前生效区域显示实际使用标准；左侧组标记显示本组独立、沿用任务或未设置。
5. 搜索episode、仅代表成员、点击图像放大和原始结果入口仅影响查看，不修改标准。

## 5. 继承规则

- 组标准含非空白字符：完整使用组标准，**不与任务标准拼接**。
- 组标准为空或只有空白：使用任务默认标准。
- 两者均空：显示未设置，展开脚本会拒绝输出，不生成空标准。
- 修改任务标准会影响所有继承它的组，不改变独立组标准。
- 删除组标准恢复继承，不是把该组排除审核。

## 6. 保存、备份与换电脑

保存按钮和自动保存均写当前浏览器localStorage，不直接写本地JSON、服务器或共享挂载。
存储键含页面pathname；换路径、浏览器、电脑或清理浏览器数据可能无法自动恢复。query参数变化通常不改变同一路径的存储键。
点击导出标准下载JSON才得到可迁移文件。请保留导出原件；无浏览器连接时程序不能凭空取得未导出的标准。

导入时校验schema版本、任务文本、组ID及成员清单完全一致；已有非空内容冲突时需要确认覆盖。导入不自动修改聚类。换到新目录第一次打开时，导入standards目录中的原件，不会自动以目录文件覆盖浏览器已有内容。
原桌面页面没有移动或清空，新模块不会修改原页面localStorage。

导出格式含version、dataset_manifest、task_standards、group_standards、updated_at、exported_at、resolved_groups。展开时以原始任务/组标准重新计算，不盲信resolved_groups。

## 7. 展开为每个episode的标准

```bash
cd '/mnt/workspace/Subtask_Annotation _Review/review_standard/manual_review_standard'
node expand_review_standards.cjs \
  datasets/robodojo3500/standards/robodojo_review_standards_2026-11-11T10-33-05-569Z.json \
  datasets/robodojo3500 \
  /mnt/data/chachaxu/dataset/robodojo/robodojo_review_standard.NEW.json
```

三个位置参数依次为导出原件、聚类结果根目录、目标JSON。目标父目录需要存在；目标文件存在则拒绝覆盖。核验新文件后再备份并替换正式版本，勿把旧文件删除作为第一步。

输出结构：

```json
{
  "schema_version": 1,
  "episode_count": 3500,
  "episodes": {
    "robodojo_episode_000000": {
      "task_id": 0,
      "task": "原始任务文本",
      "cluster_id": "t000_c000",
      "standard_source": "task",
      "review_standard": "有效标准原文"
    }
  }
}
```

示例仅展示主要字段；实际还保存导出原件SHA256、时间、优先级说明、任务/组数量以及来源计数。保留标准原文，不调用模型润色，不删改大小写或对象特征。
转换会检查清单匹配、合法键、重复episode、任务/总数覆盖和空标准，任何一项不通过都报错。

当前正式文件：`/mnt/data/chachaxu/dataset/robodojo/robodojo_review_standard.json`，覆盖3500条，400条组标准、3100条任务标准。本次模块副本在standards/；后续更新正式文件不会自动更新副本或浏览器。

## 8. 故障排查

| 情况 | 处理 |
| --- | --- |
| 图片空白 | 整体复制数据包，检查images目录和文件名 |
| 看不到组标准 | 选择左侧组，右侧顶部输入；刷新后确认加载新版页面 |
| 保存失败 | 浏览器存储可能禁用/已满，先导出备份，不清空现有数据 |
| 换路径后内容为空 | 从最近导出JSON导入，不代表原文件被删除 |
| 导入清单不匹配 | 使用对应聚类版本，不能强行把旧组ID套到新分组 |
| 转换提示空标准 | 为相应任务或组补齐后重新导出 |
| EEXIST | 目标已存在，选新文件名核验，不静默覆盖 |

编辑、继承、清空回退、持久化、导入导出、清单不匹配与存储失败已通过隔离浏览器测试。测试不向用户真实浏览器写入示例标准。
