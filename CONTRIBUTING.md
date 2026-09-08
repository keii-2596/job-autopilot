# 参与改进 Job Autopilot

欢迎反馈难用的流程、无法识别的招聘表单、文档问题，或提交小范围的修复。

## 反馈问题

请在 [Issues](https://github.com/keii-2596/job-autopilot/issues) 中说明：操作步骤、预期结果、实际结果，以及操作系统和插件版本。截图或错误信息请先去掉个人资料、完整本机路径、登录凭据和验证码。

不要上传真实简历、本地数据库、Cookie 或含隐私的对话记录。安全漏洞请按 [SECURITY.md](SECURITY.md) 私下报告。

## 本地开发

运行依赖 Python 3.9+；网页静态资源已包含在仓库中。功能与兼容性变更应附测试，不要用真实投递来验证修复。

| 目录或文件 | 用途 |
| --- | --- |
| `skills/job-autopilot/` | Codex 工作流和安全边界 |
| `.codex-plugin/plugin.json`、`.mcp.json` | 插件信息与本地 MCP 服务声明 |
| `runtime/job_autopilot/` | 数据库、任务连接、适配器与控制台服务 |
| `runtime/job_autopilot/web_static/` | 当前网页的 HTML、CSS 和 JavaScript |
| `data/jobs.json` | 仅公开字段的职位数据 |
| `scripts/job-autopilot`、`scripts/job-autopilot-mcp` | 命令入口与 MCP 入口 |
| `tests/` | 单元测试与隔离的界面回归测试 |

在仓库根目录运行单元测试：

```bash
PYTHONPATH=runtime python3 -m unittest discover -s tests -q
```

界面回归另需 Node.js 与 Playwright：

```bash
npm install --no-save --package-lock=false playwright
npx playwright install chromium
node tests/dashboard.e2e.cjs
```

界面测试自动创建临时数据库和服务，不读取个人投递记录，也不会发起真实投递。可用 `BROWSER_EXECUTABLE` 指定已安装的 Chrome 或 Edge，用 `SCREENSHOT_DIR` 指定截图目录。

README 的 `assets/dashboard.png` 应来自当前网页的实际截图，并使用示例数据。不要用设计稿替代真实产品界面，也不要把维护者的实际申请记录放进文档。

## 提交前

- 改动聚焦于一个问题；说明如何验证，不要顺带修改个人配置。
- 界面入口、默认值或安装步骤改变时，同步更新文档和截图。
- 公开仓库不接收本地爬虫、个人数据或登录状态；职位数据范围见 [data/README.md](data/README.md)。
- 保留提交确认与本地数据保护，不以提高自动化程度为由绕过这些边界。

代码按 [MIT License](LICENSE) 发布。
