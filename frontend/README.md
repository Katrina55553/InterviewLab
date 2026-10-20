# 面试研习室 · AI Meeting 前端

React、TypeScript、Vite、Tailwind CSS 与 Zustand。延续暖纸色、墨黑与翡翠绿的原有视觉风格。

## 开发与检查

```bash
npm ci
npm run dev            # http://localhost:5173，/api 和 /ws 转发到 localhost:8000
npm run typecheck
npm run lint
npm test
npm run test:platform  # 平台协议与完整用户流程测试
npm run test:e2e       # 真实浏览器、真实后端：需先启动演示环境及 Playwright
npm run test:e2e:admin # 另需设置测试管理员环境变量
npm run build          # dist/ 需要一并提交，Docker 直接使用
```

## 当前页面

| 路径 | 功能 |
| --- | --- |
| /login | 注册与登录 |
| / | 简历上传、准备状态、最近面试 |
| /resumes/:id | 结构化解析、简历评分、建议与题目预览 |
| /history | 面试历史与断点恢复 |
| /interviews/:id | 文本 / 语音答题、追问、跳题、问题朗读与可选摄像头反馈 |
| /interviews/:id/report | 多维雷达图、真实逐轮评分与问答回放 |
| /chat、/chat/:id | Agent 选择、附件、SSE 对话与历史 |
| /admin | 仅管理员可见的模型连接与智能体配置 |

新平台集中在 src/platform/。旧 src/pages/ 与旧 stores 保留供原有测试参考，已从 App.tsx 路由移除。

## 连接与恢复

- 使用 HttpOnly ra_session 会话 cookie；修改请求携带可读 ra_csrf cookie 对应的 X-CSRF-Token。
- 面试命令带独立 UUID、状态版本与当前问题 ID；提交期间禁用重复操作。网络结果未知时只刷新状态，不自动重交答案；服务端明确失败或未知后由用户确认重试。
- WS 按 sequence 恢复事件，并以持久化快照替换 UI；REST 定期同步兜底。旧版本快照不会覆盖新版本。
- ASR 使用 AudioWorklet，把浏览器采样率转换为 16kHz 单声道 PCM16，每帧 40ms；停止、提交或离页会释放麦克风。转写先进入草稿，确认后才答题。
- TTS 异步合成并查询状态；用户可以取消等待 / 播放。摄像头需要主动开启，每 5 秒最多一个截图分析请求；仪态反馈不计入答题分数。
- 麦克风和摄像头需要 HTTPS 或 localhost。供应商未配置时对应功能禁用；演示评分显式标注。
- Nginx 对 API 关闭响应缓冲，以便 SSE 逐块送达。

## 验证范围

Vitest 覆盖 CSRF / multipart、中文 SSE 分块、事件版本排序、48k/44.1k/16k 音频重采样、简历上传到报告的完整 DOM 流程，以及未知提交不自动重发、终态失败恢复草稿、会话过期处理。

真实浏览器脚本在 tests/。先启动后端、Celery、outbox 和前端，并显式启用后端演示模式。脚本使用本地 Playwright 包，也可通过 PLAYWRIGHT_MODULE 指定宿主已有的 Playwright 包绝对目录，不必向应用增加依赖。E2E_BASE_URL 默认 http://127.0.0.1:5173，截图写入忽略提交的 test-results/。

普通用户脚本使用自建测试账号验证注册、登录、上传到报告、刷新恢复、追问与换问、主动跳过零分、提前结束、SSE 对话与移动端。管理脚本需要 E2E_ADMIN_USERNAME / E2E_ADMIN_PASSWORD 指向隔离环境中的测试管理员；验证密钥不回传、编辑保留密钥、scene=null 独立 Agent、启停与工具配置，并清理本次创建的配置。

真实语音、视觉和 LLM 仍需配置相应供应商凭据后验收，演示浏览器脚本不会把替身结果当作供应商已验收。
