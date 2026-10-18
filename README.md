# AI 打印机

Windows 菜市场电话物料项目：**HT802 座机 → 本地 Qwen3-ASR → WorkBuddy skill → 网页展示 → 响铃 → A4 彩色双面打印**。

运行时不调用 Codex Agent。WorkBuddy 执行意图判断与图片任务，Python/Node 负责电话、识别、任务回收、显示和打印。

## 前端快速开始

安装 Python 3.11/3.12，克隆仓库后运行：

```powershell
python scripts/demo_server.py
```

打开 http://127.0.0.1:8767/showcase （现场双栏）或 http://127.0.0.1:8767/ （管理页）。无需GPU、模型、HT802或WorkBuddy，数据均为人工示例，所有POST操作禁用。

```powershell
python scripts/demo_server.py --stage cycle
python scripts/demo_server.py --stage needs_input
```

## 生图 skill 开发

修改 `skills/market-stall-demo/` 中的规则、文案和模板，再安装到本机：

```powershell
python scripts/install_skill.py
```

脚本先把旧版本备份到 `data/skill-backups/`，再复制源码。刷新技能列表或重启WorkBuddy；实际发送必须选中 `/market-stall-demo`。

## 完整部署与协作

- [测试机部署与迁移](docs/DEPLOYMENT.md)
- [前端与生图协议](docs/PROTOCOL.md)
- [协作规范与测试](CONTRIBUTING.md)

完整运行需要 Windows 10/11、Python 3.11/3.12、Node.js 20+、NVIDIA驱动和兼容PyTorch、已登录的WorkBuddy、打印机驱动。`setup.ps1` 创建环境与本地配置，CUDA构建由目标机决定。

示例配置默认关闭自动提交、响铃和打印，设备验证后逐项开启。运行 `start.cmd`，结束 `stop.cmd`。模型通过ModelScope脚本下载并校验，不放Git。

| 目录 | 职责 |
|---|---|
| `web/` | 双栏现场页与管理页 |
| `skills/market-stall-demo/` | 技能规则、文案、模板 |
| `workbuddy/` | prompt、UI操作、任务/打印管理 |
| `sip/` | SIP/RTP、录音和响铃 |
| `scripts/` | 安装、下载、诊断、打印、打包 |
| `tests/` | 不调用真实设备或AI的自动测试 |

## 已知限制

- WorkBuddy UI自动化需要Windows解锁、账号登录；客户端升级后需验证选择器。提交时短暂切到对话，随后回双栏展示。全屏产物抢占时自动返回网页。
- 电话窄带、市场噪声和方言可能导致ASR错词，商品/价格/单位需核对。
- “已提交打印”不代表物理出纸。当前Canon仍有白边和双面偏移，未实现真正无边距；提供独立 `-Calibration` 样张功能。
- 仓库不含模型、录音、生产记录、生成图片、本机配置、凭据和项目方案附件。

## 许可

SIP部分来自 `prikevs/codex-redline`，保留 `sip/LICENSE`。参见 [第三方说明](THIRD_PARTY_NOTICES.md)。自有代码暂未另行授予开源许可证，公开可见不代表可任意再授权。
