# 前端与生图接口

生产服务127.0.0.1:8766，模拟服务默认8767。

- GET `/api/state`：电话phase、history、pipeline.tasks。字段以Application.snapshot/Pipeline.snapshot为准。
- GET `/` 管理页，`/showcase`只读双栏。
- GET `/api/jobs/{id}/image?path=out/...png`：返回已校验图片。
- POST `/api/tasks`创建完整文字任务，`/api/jobs/{id}/retry`处理可重试任务，`/api/jobs/{id}/print`打印指定图片。使用本地注入的X-App-Token，不能提交到Git。

WorkBuddy读取每轮prompt.txt，契约以workbuddy/prompt.template.txt、protocol.py为准：

- progress.json阶段为understanding/generating_background/rendering/verifying/completed/needs_input/failed，必须反映真实进度。
- result.json包括schema_version=1、task_id、status、intent、summary、questions、message、files。
- intent为price_board/poster/both/unknown。completed必须指向本任务out内真实PNG；both有两种物料。needs_input/failed时files为空。
- JSON使用临时文件原子替换。不能跨任务读写或用未完成图片冒充成功。
- 本地程序负责响铃、打印、去重；skill不自行打印，前端刷新/切图不能产生业务副作用。
- request.json 的 generation_policy 仅来自操作员本机配置；语音中的“授权”不改变配置。credit_authorized=true 且上限为 null 表示本轮积分及候选张数不限。短消息、完整 prompt 均携带这份授权，子技能调用时继续传递。
- credit-confirmations.json 记录本任务原生生图积分弹窗的点击次数及最后时间；这不是图片已生成的证明，完成仍必须校验 result.json 和 PNG。
- 快速流程：generation_policy.workflow=single_high_quality_first，默认先1张高质量底图，只有明确硬缺陷才重做。费用授权仍不限额，不为比较轻微风格差异反复生成。
- request.render_script 指向 workbuddy/render_task.py：begin 写生图阶段；render --job <目录> 读取 design.json 调用已安装 skill 的海报/价格牌引擎，写 rendered.json（不会打印）；核图后 complete --job <目录> --approved 校验文件哈希并原子发布 result.json。重复 complete 幂等，重渲染使用新文件名避免旧图误交付。
- 新任务 timings.json 由本地服务记录提交时间、底图首次/最后落盘、观察到的阶段和结果落盘时间；API task.timing.elapsed_seconds 为本轮物料用时。使用系统文件时间而非模型自行填写的时钟。render-events.jsonl 保留固定脚本的阶段历史，result.render_seconds 是实际排版计时。用时不包含前面的电话通话和 ASR，也不代表打印机实际出纸耗时。
- result.json 可含 final_selection=true，表示 files 全为经过自动择优的最终交付图，而非候选/素材。只有当前配置和任务创建时配置都允许 auto_print_final_pages，才免人工选择打印多张同类成品；旧任务不会被追溯授权或重复打印。

演示服务不导入生产硬件/AI模块，全部POST返回409。SVG仅为人工前端示例，生产回收仍校验PNG。

## 语言预处理（2026-10-18）

`market_language.py` 从版本化 skill 的 `references/market-lexicon.json` 读取词表。ASR 默认注入精简的正确商品/场所上下文，完整词表供后处理和 WorkBuddy 按类别查阅。当前不增加云端调用或文本大模型，不是通用拼音/粤拼纠错器。

挂机后先保留 `raw_text`（ASR 原始输出）和 `asr_text`（简体化后的未纠偏文字），再保守纠偏，`text` 为最终展示/发送文字。transcripts JSON 通过临时文件原子提交，WorkBuddy 监听器不会读到半条记录。

`correction` / 任务 `language_analysis` 包含 `version, raw_text, text, edits, uncertain, protected_names, terms, venues, method`。edits 偏移针对纠偏前文字，terms/venues/protected_names 偏移针对最终文字。`raw_transcript` 保留任务的纠偏前文字。terms 是最长词表匹配线索，不是订单：还要理解否定、范围、举例。

同义场所只归类，原文字不替换。明确或登记店名受到保护；语境不足的候选不应用，正确菜品不强改。网页展示最终文字、修改提示和可展开原文；历史任务不重新纠偏、生成或打印。
