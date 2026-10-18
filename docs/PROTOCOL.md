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

演示服务不导入生产硬件/AI模块，全部POST返回409。SVG仅为人工前端示例，生产回收仍校验PNG。
