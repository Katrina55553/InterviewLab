# 知面 InterviewLab 前端

登录后的 AI 面试平台前端：简历诊断、模拟面试、量化报告、SSE 对话与管理员配置。页面、登录状态和请求恢复逻辑集中在 `src/platform/`，旧页面与 stores 已删除。

## 技术栈

以下为当前 `package-lock.json` 锁定版本，使用 `npm ci` 安装。

| 用途 | 技术与版本 |
| --- | --- |
| 构建环境 | Node.js 24 LTS；`.nvmrc`、`package.json`、Docker builder 与 CI 统一主版本 |
| 界面 | React / React DOM 19.2.6、TypeScript 6.0.3 |
| 构建 | Vite 8.3.5 |
| 样式 | Tailwind CSS 4.3.0、Typography 0.5.20 |
| 路由与状态 | React Router 7.18.4、Zustand 5.0.13 |
| Markdown | react-markdown 10.1.0 |
| 代码检查 | ESLint 10.4.0 |
| 单元测试与覆盖率 | Vitest / `@vitest/coverage-v8` 5.0.5、jsdom 25.0.1 |
| 浏览器测试 | Playwright 1.58.2，已显式列入开发依赖 |

HTTP 请求使用原生 `fetch`，面试事件使用 WebSocket，聊天流使用 SSE。登录状态由 Zustand 管理，其余页面数据与恢复逻辑在 `platform/hooks.ts` 和对应页面内。

Typography 插件仍依赖旧版 CSS 选择器解析器，因此暂用局部 `overrides` 固定到 `postcss-selector-parser` 7.1.7，修复 GHSA-rj75-hqrm-r3gf；上游更新依赖后可移除此覆盖。

## 开发与检查

先按[平台启动文档](../docs/platform.md#启动)配置后端及 PostgreSQL / Redis。真实模式需要供应商配置；浏览器演示验收应使用隔离环境，并在所有后端进程共享的配置中显式设置 `PLATFORM_DEMO_MODE=true`。

后端首次准备，在仓库根目录执行：

```bash
cd backend
python -m pip install --require-hashes -r requirements-dev.txt
python -m app.platform.cli migrate
```

随后每个长驻命令使用独立终端；工作目录均相对于仓库根目录。

| 工作目录 | 命令 | 用途 |
| --- | --- | --- |
| `backend` | `uvicorn app.main:app --reload --port 8000` | Web / REST / WebSocket |
| `backend` | `celery -A app.tasks.celery_app worker --loglevel=info --pool=solo -Q prepare,report,default` | 本地单 Worker 消费准备、报告和过渡任务 |
| `backend` | `python -m app.platform.worker` | 必需的 outbox dispatcher |
| `backend` | `python -m app.platform.operations.recovery` | 必需的任务与命令恢复巡检 |
| `backend` | `celery -A app.tasks.celery_app beat --loglevel=info` | 仓库统计定时调度，每个部署只运行一个实例 |
| `frontend` | `npm ci`，完成后 `npm run dev` | 前端开发服务 |

前端默认地址为 `http://localhost:5173`，Vite 将 `/api` 和 `/ws` 转发到 `localhost:8000`。后端允许的来源需包含实际浏览器地址。仅启动 Web 服务无法完成异步准备与报告。

以下检查命令在 `frontend` 目录执行：

```bash
npm run typecheck
npm run lint
npm test
npm run test:coverage  # src/platform/ 源码覆盖率，HTML 报告在 coverage/
npm run test:platform  # 平台协议与完整用户流程测试
npm audit --audit-level=high
npm run build
```

`dist/` 需要随前端变更一并提交。Docker builder 会执行 `npm ci` 与生产构建，再将生成的静态文件交给 Nginx。

## 当前页面

| 路径 | 功能 |
| --- | --- |
| `/login` | 注册与登录 |
| `/` | 登录后重定向至 `/resumes` |
| `/resumes` | 简历诊断入口：上传简历，进入准备与诊断结果页 |
| `/my-resumes` | 已上传简历列表、准备状态和诊断结果入口 |
| `/resumes/:id` | 结构化解析、简历评分和改进建议；可携带简历进入 AI 对话或面试室 |
| `/interviews/new` | 模拟面试室：上传或选择已有简历，准备完成后开始提问；支持 `?resume=<id>` 预选简历 |
| `/interviews/:id` | 面试恢复、文本 / 语音答题、追问、换问、跳题、问题朗读与可选视觉反馈 |
| `/interviews/:id/report` | 异步报告、五维雷达图、逐轮评分与问答回放 |
| `/history` | 面试历史、继续未完成面试和查看报告 |
| `/chat`、`/chat/:id` | Agent 选择、附件、SSE 对话与历史 |
| `/admin` | 管理员模型连接与智能体配置 |

简历诊断与模拟面试分别进入。准备任务完成解析、评分和建议；面试主问题在首次开始面试时生成，诊断页不提供题目预览。

## 连接与恢复

- 使用 HttpOnly `ra_session` 会话 Cookie；修改请求携带可读 `ra_csrf` Cookie 对应的 `X-CSRF-Token`。
- 面试命令带独立 UUID、状态版本与当前问题 ID；提交期间禁用重复操作。网络结果未知时只刷新状态，不自动重交答案；服务端明确失败或未知后由用户确认重试。
- WS 按 sequence 恢复事件，并以持久化快照替换 UI；REST 定期同步兜底。旧版本快照不会覆盖新版本。
- ASR 使用 AudioWorklet，把浏览器采样率转换为 16kHz 单声道 PCM16，每帧 40ms；停止、提交或离页会释放麦克风。转写先进入草稿，确认后才答题。
- TTS 异步合成并查询状态；用户可以停止等待或播放。
- 进入面试室会自动请求摄像头权限，仅请求视频。未授权、没有设备或手动关闭都不阻塞文字面试；允许后可预览，也可随时关闭或重新开启。摄像头从简历收集到问答阶段保持开启，离开面试室或面试结束后释放。
- 简历收集阶段只在本机预览，不上传画面。面试页面在视觉能力可用且当前没有进行中的面试命令时，每 5 秒最多发送一个截图分析请求；仪态反馈不计入答题分数。
- 麦克风和摄像头需要 HTTPS 或 localhost。ASR / TTS / 视觉反馈由后端能力配置决定，本机摄像头预览不依赖视觉供应商；演示评分显式标注。
- Nginx 对 API 关闭响应缓冲，以便 SSE 逐块送达。

## 验证范围

Vitest 覆盖 CSRF / multipart、中文 SSE 分块、事件版本排序、48k/44.1k/16k 音频重采样、简历上传到报告的完整 DOM 流程、仓库统计刷新，以及未知提交不自动重发、终态失败恢复草稿、会话过期处理。覆盖率统计 `src/platform/` 源码，排除测试与类型声明。

真实浏览器脚本在 `tests/`。完整验收使用隔离的 Compose / Nginx 入口：按[工程化运行手册](../docs/engineering-operations.md)启动 `docker-compose.test.yml`，显式设置 `TEST_DEMO_MODE=true` 并重启测试栈，保持消费 `prepare,report,default` 的 Worker、outbox dispatcher 和 recovery 巡检持续运行。测试栈入口为 `http://127.0.0.1:18080`。

在 `frontend` 目录安装浏览器，并显式设置目标地址；以下以 PowerShell 为例：

```powershell
$env:E2E_BASE_URL = "http://127.0.0.1:18080"
npx playwright install chromium
npm run test:e2e
# 设置 E2E_ADMIN_USERNAME / E2E_ADMIN_PASSWORD 后：
npm run test:e2e:admin
```

脚本未设置 `E2E_BASE_URL` 时仍默认 `http://127.0.0.1:5173`。普通 Vite 开发服务会对未知路径回退到 SPA，完整管理员验收中的 `/metrics` 返回 404 等路由边界断言需通过 Nginx 入口验证。

Linux CI 使用 `npx playwright install --with-deps chromium` 安装浏览器及系统依赖。脚本默认使用已安装的本地 Playwright；`PLAYWRIGHT_MODULE` 可指定宿主已有包的绝对路径。截图默认写入忽略提交的 `test-results/`，可用 `E2E_OUTPUT` 指定其他目录。

普通用户脚本自建测试账号，验证注册、登录、上传到报告、刷新恢复、追问与换问、主动跳过零分、提前结束、SSE 对话与移动端。管理脚本需要 `E2E_ADMIN_USERNAME` / `E2E_ADMIN_PASSWORD` 指向隔离环境中的一次性管理员；可先在 `backend` 运行 `python -m app.platform.cli create-admin --username <测试管理员名>` 创建。脚本验证密钥不回传、编辑保留密钥、`scene=null` 独立 Agent、启停与工具配置，并清理本次创建的配置。

真实语音、视觉和 LLM 仍需配置相应供应商凭据后验收，演示浏览器脚本不会把替身结果当作供应商已验收。
