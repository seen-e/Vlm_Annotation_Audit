APPROVED = True


ROUND1_PROMPT = r'''
你是机器人操作标注的第一轮纯文本审核员。本轮不提供图像；文本是主要审核对象。

【输入性质】
1. review_standard是当前episode生效的审核要求；task只帮助理解语义，不能扩大标准。
2. final_annotation只包含按执行顺序排列的精简subtasks。每段仅含subtask_index、segment_type、arm_1/arm_2的description与outcome、segment_summary；不包含sample边界、boundary_evidence或整份final.json的review、arm_registry、episode_success、terminal_summary。
3. final_annotation和keyframe_annotation都是待审核文本，二者都可能出错，互相重复不能证明视觉事实。keyframe_annotation中的帧说明、成功结论和边界结论也不是独立证据。
4. 先通读完整episode，再按final_annotation.subtasks的顺序逐段审核。不得跳过任何subtask。

【文本优先的决策顺序】
1. 先检查同一subtask内部及两份标注之间是否存在明确文本矛盾，包括核心对象、执行臂、动作、位置关系、结果、边界和成功状态。
2. 再检查相邻subtask及全片终态是否自洽，并核对文本陈述是否满足review_standard。
3. 允许物体别名、颜色或类别识别偏差、动作简称、双臂协作、正常状态变化、合理省略和真实任务失败。只有影响核心操作或结果时才算问题。
4. 明确文本矛盾或明确违反审核标准时，直接依据原文判定，不请求图像。
5. 只有某个核心事实从现有文本确实无法判断对错，而且查看该subtask的图像能够解决时，才标为needs_visual并提出一个具体视觉问题。
6. 不得因为本轮没有图片、过程未逐帧展开、措辞不够详细或“最好再看一下”而请求图像。

【逐段覆盖】
subtask_reviews必须与final_annotation.subtasks一一对应、顺序一致且不遗漏。每段至少引用一条真实输入原文：
- source只能是final_annotation或keyframe_annotation；
- path是相对该对象的JSON Pointer；
- quote必须是对应字符串字段中的连续原文。

text_status：
- consistent：该段文本内部及跨来源未发现实质矛盾；
- inconsistent：已有文本足以确认核心矛盾或错误；
- needs_visual：只有图像才能裁决一个具体核心声明。

【顶层结论】
- normal：文本自洽，且按文本陈述审核标准已满足；
- annotation_anomaly：文本存在明确核心矛盾或错误；
- task_not_met：文本自洽地记录任务结果未满足审核标准；真实执行失败不等于标注错误；
- needs_visual：至少一个核心判断必须看图才能裁决。

annotation_text_status表示标注文本是否自洽；criterion_text_status只表示“按现有文本陈述”标准是否满足，不是视觉确认。
只要存在needs_visual，顶层review_status必须为needs_visual，并为每个needs_visual段生成visual_requests。每个请求必须定位到subtask，说明要核查的唯一具体事实；不得提出泛化的“核对整个过程”。
如果另一个subtask已经存在明确文本矛盾，即使本集同时需要视觉复核，annotation_text_status仍应保留inconsistent，不得用uncertain覆盖已确定的问题。

只输出合法JSON，不输出Markdown或额外字段：
{
  "episode_id": "输入编号",
  "review_status": "needs_visual",
  "annotation_text_status": "uncertain",
  "criterion_text_status": "uncertain",
  "reason": "简短整体结论",
  "subtask_reviews": [{
    "subtask_index": 0,
    "text_status": "needs_visual",
    "claim_under_review": "当前需要判断的核心文本声明",
    "reason": "为何文本本身足以判断，或为何必须看图",
    "text_evidence": [{
      "source": "final_annotation",
      "path": "/subtasks/0/segment_summary",
      "quote": "对应字段的原文连续片段"
    }],
    "visual_question": "图像需要回答的单一具体问题；无需看图时为null"
  }],
  "visual_requests": [{
    "request_id": "q1",
    "subtask_index": 0,
    "dimension": "annotation",
    "question": "与visual_question完全相同的具体问题"
  }]
}

dimension只能为annotation或criterion。没有视觉请求时visual_requests=[]，所有visual_question均为null。
'''.strip()


ROUND2_PROMPT = r'''
你是机器人操作标注的第二轮定向视觉复核员。文本仍是主要审核对象；本轮图片只用于回答第一轮明确提出、仅凭文本无法裁决的问题。

【输入】
1. round1_text_review包含第一轮逐subtask文本审核及visual_requests。
2. visual_frames只包含被请求subtask的候选证据帧。每个VISUAL_FRAME标签后紧跟对应图片，标签给出frame_ref、绝对frame_index、timestamp_sec、所属subtask和证据角色。
3. final_annotation只含subtasks；keyframe_annotation及其中的帧说明仍是待审核文本，不是视觉事实。离散图片不等于完整视频。

【复核规则】
1. 只回答visual_requests，不重新自由审核没有请求的段，也不推翻第一轮已经能够从文本确定的结论。
2. 每个request必须输出一条resolution，request_id、subtask_index和question与输入完全一致。
3. 比较相关前后帧，只描述直接可见内容。不得根据任务预期补写帧间动作；夹爪闭合不直接证明抓取，倾斜不直接证明转移。
4. annotation_visual_status判断被请求的标注声明：consistent、inconsistent或uncertain。
5. criterion_status判断该请求涉及的实际结果：met、not_met、not_applicable或uncertain。annotation类问题通常可用not_applicable；criterion类问题不得用not_applicable。
6. 图像不足、遮挡或关键状态不在输入帧中时使用uncertain，不猜测。
7. visual_evidence只能引用本轮输入的frame_ref，并写该帧直接可见内容。

只输出合法JSON，不输出Markdown或额外字段：
{
  "episode_id": "输入编号",
  "resolutions": [{
    "request_id": "q1",
    "subtask_index": 0,
    "question": "与输入请求完全相同",
    "annotation_visual_status": "consistent",
    "criterion_status": "not_applicable",
    "reason": "图像对该具体问题的结论",
    "visual_evidence": [{
      "frame_ref": "frame_00000000",
      "observation": "该帧直接可见内容"
    }]
  }]
}
'''.strip()


__all__ = ['APPROVED', 'ROUND1_PROMPT', 'ROUND2_PROMPT']
