# 团队协作

1. `git switch -c feat/功能名`，在克隆目录开发，不在现场运行目录直接改。
2. 前端用 `python scripts/demo_server.py`。`--stage` 支持recording、transcribing、understanding、generating_background、rendering、verifying、completed、needs_input、failed、cycle。
3. skill源文件统一在 `skills/market-stall-demo/`，修改后执行安装脚本，不只改WorkBuddy安装副本。
4. 协议变更同步后端/前端/测试。商品价格单位不得编造；未知提交状态不自动重发或重印。
5. Windows验证：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests
node --test tests/completion-notifier.test.mjs
```

6. Pull Request注明修改、验证、界面截图和协议变化，由另一位组员审查后合并。`main`保留已验证代码，现场部署使用经过人工全链路测试的标签。首次导入不代表新机器验收完成。

禁止提交：config.json、desktop-profile.json、.env、账号/令牌、data、真实录音/记录、模型、虚拟环境、驱动和打包输出。提交前检查 `git diff --cached`。
