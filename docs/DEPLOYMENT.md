# Windows 测试机部署

## 环境

克隆仓库、选择已验证版本。安装Python3.11/3.12、Node.js20+、NVIDIA驱动、WorkBuddy、实际打印机驱动；检查 `python --version`、`node --version`、`nvidia-smi`。

复制 `config.example.json` 为 `config.json`，`workbuddy/desktop-profile.example.json` 为 `workbuddy/desktop-profile.json`。setup.ps1也会在缺失时复制，不覆盖已有配置。

按 https://pytorch.org/get-started/locally/ 选择兼容目标显卡驱动的PyTorch wheel源：

```powershell
.\setup.ps1 -TorchIndexUrl <所选PyTorch官方wheel源>
```

支持 `-TorchWheel <本地wheel>`、`-TorchVersion <版本>`、`-Python <python.exe路径>`。其他依赖默认阿里云PyPI。不复制旧机器.venv，不自动安装NVIDIA驱动；已装torch时复用。

## 模型与技能

```powershell
.\.venv\Scripts\python.exe scripts/download_model.py --model 1.7B
.\.venv\Scripts\python.exe scripts/install_skill.py
```

也可复制完整 `models/Qwen3-ASR-1.7B/`（含download-manifest.json），再运行下载脚本校验，缺失才下载。4080笔记本先用1.7B，device/dtype保持auto，显存以目标机实测为准。

模板需要Chrome/Edge；查看技能源码中的浏览器查找路径。WorkBuddy需可用的生图模型/工具，ASR本地运行不意味着生图也离线。

## 设备配置

- `phone.local_address` 是连接HT802的本机网卡IP，`device_address` 是HT802地址。192.168.82.1/100仅作示例。
- HT802 FXS摘机自动拨号指向本机SIP地址/端口；示例本机SIP5090、设备5060、RTP15004。HT802设置不随Git迁移；防火墙规则参考scripts/firewall.ps1。
- `runtime.node`默认PATH中的node，也支持相对路径。
- `printing.printer_name`填本机实际队列名；先测试，再设`printing.enabled=true`。A4彩色、长边双面、Cover；双面使用两面空白纸。
- 登录WorkBuddy、安装skill并验证当前客户端控件后，设`automation.enabled=true`。
- 项目负责人授权积分生图后，在本机 `config.json` 设置 `generation.credit_authorized=true`。该授权不设积分或候选图张数上限，优先质量，自动择优；会传给每轮任务及下游生图技能。公开示例默认关闭，不把某项目的费用授权默认赋予所有部署者。
- 完全自动交付可设 `generation.auto_print_final_pages=true`：只自动打印模型已筛选并标记 `final_selection=true` 的最终成品/分页，不打印底图或候选。迁移机器需复制这两个本机设置，并重新安装新版 skill、重启程序。
- 完成响铃`notifications.enabled=true`；现场双栏`display.workbuddy_showcase=true`、`display.open_browser=false`。
- 桌面profile示例为V5.8.3已验证控件，使用当前用户LOCALAPPDATA；客户端版本不同需复核。

## 启动与验收

```powershell
.\.venv\Scripts\python.exe scripts/doctor.py
.\start.cmd
```

逐项检查：摘机提示音 → 挂机识别并保存 → skill选择/生图 → 网页进度/图像 → 响铃 → 双面出纸 → 下一轮电话。拿已知原文核对数字/单位。新机打印边距要重新测量。

模型选择取决于当前 WorkBuddy 内置生图工具实际开放的选项；程序要求优先适合任务的最佳质量档位，在结果 summary 中记录实际模型和质量检查。无法选择模型时不声称已选定某个模型。
WorkBuddy V5.8.3 的生图工具还有独立积分弹窗，不能只靠 prompt 关闭。授权开启后，监听器每 2 秒检查当前任务的“确认开始生成图片？”弹窗，同时核对“产生积分消耗”和说明文字，再点击该弹窗内的“1 确认”。确认记录写入任务的 credit-confirmations.json。只处理当前已确认发送且携带积分授权的任务，不点击其他权限/充值/购买弹窗，也不更改客户端全局权限。客户端控件变化时需更新匹配器并重新验收。
缺商品/价格/单位写 needs_input，持续工具失败写 failed，均结束该轮等待。默认 600 秒未收到有效结果则显示超时；已确认发送的超时任务不重发，仍接收迟到结果，并允许下一轮独立任务。发送状态不明或有人输入草稿仍会暂停，避免误发。平台原生强制权限弹窗无法仅靠 prompt 保证消失。

## 发布、升级、回退

`python scripts/package_release.py`生成含skill的干净源码包；`--include-model`校验并加入模型。均排除本机配置、数据、凭据、虚拟环境、驱动和node.exe。大模型包用团队存储或移动硬盘，不放普通Git提交。包仍需安装环境。

升级前停止服务、备份config/data，取指定版本，更新依赖并同步安装skill。回退代码也要回退skill；保留打印回执避免重复输出。当前开发机现场目录与Git开发目录分开维护，后续改动先在Git目录验证再部署。
