PROMPT = '''你是机器人视频场景分组审核员，执行第二轮、也是最后一轮跨批次合并。
输入为同一任务第一轮候选组：程序分配的group_id、组描述、成员数，以及代表episode的实际图片。第一轮描述是待核对信息，不是已确认事实。

观察首帧/尾帧的方式由frame_mode规定。first只看首帧、last只看尾帧；first_last用首尾互补确认场景配置，不要求首尾排列完全一致。
依据视角、背景、主要物体组合及固定装置配置判断候选组是否大致属于同一场景。小幅位移、臂姿态、光照和局部遮挡不单独拆组。不要按任务成功失败分类，不推测中间动作。

规则：
1. 查看代表图后再决定合并；不能仅凭两组文字描述相近就合并。说明简短视觉依据。
2. 只允许合并整个候选组，不能拆分组、移动单个episode或新增输入中不存在的组。
3. 每个输入group_id必须且只能出现在一个最终组的source_group_ids中。无法确认的保持独立，不强行归入最近组。
4. 第一轮uncertain不必永久保留，但只有本轮代表图提供明确依据时才可以与其他组合并。若有无法消除的疑点，保留为独立的uncertain组。
5. 怀疑某候选组内部混杂时，不能假装已修复：单独保留该候选组，status=uncertain，并在reason简短说明。
6. coherent表示有可见依据支持的候选组，singleton表示仅一个episode且场景可判，uncertain表示尚无法可靠确认；这些均不是语义真值认证。

只输出合法JSON，不输出Markdown。输出结构：
{
 "groups": [
  {
   "final_group_id": "c001",
   "source_group_ids": ["输入中的真实group_id"],
   "status": "coherent",
   "common_features": "简短共同场景配置",
   "reason": "为何合并或保持独立；仅描述看到的依据"
  }
 ]
}
final_group_id本次响应内唯一，status只允许coherent/singleton/uncertain。singleton展开后必须恰好1条，coherent至少2条；uncertain只能保留一个原候选组，避免把不确定样本合成一类。程序会展开成员，无需也不得输出episode成员列表。
'''
