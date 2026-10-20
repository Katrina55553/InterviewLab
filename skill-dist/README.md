# resume-agent-skill

TRAE skill for ResumeAgent API —— AI 简历诊断 + 模拟面试 Agent 的对外服务封装。

让 TRAE IDE 中的 AI agent 通过标准 HTTP 调用完整的「简历上传 → 解析 → 诊断 → 模拟面试 → 评估报告」流程。

## 快速安装

```bash
npx resume-agent-skill install
```

执行后会自动把 `SKILL.md` 复制到 TRAE 全局 skills 目录:

| 平台 | 路径 |
|------|------|
| Windows | `C:\Users\<用户名>\.trae-cn\builtin\global\skills\resume-agent-api\SKILL.md` |
| macOS / Linux | `~/.trae-cn/builtin/global/skills/resume-agent-api/SKILL.md` |

## 其他命令

```bash
npx resume-agent-skill uninstall    # 卸载
npx resume-agent-skill path         # 查看安装路径和状态
npx resume-agent-skill help         # 显示帮助
```

## 前置条件

skill 只是 API 调用文档,需要 ResumeAgent 后端服务运行才能用:

```bash
# 克隆并启动 ResumeAgent
git clone https://github.com/your-org/ResumeAgent.git
cd ResumeAgent
docker compose up -d

# 健康检查
curl http://localhost:8000/health
# {"status":"ok","version":"0.1.0"}
```

默认 Base URL:`http://localhost:8000`。如需指向远端部署,安装后编辑 `SKILL.md` 中的 URL 即可。

## 使用方式

安装完成后,在 TRAE IDE 中对 agent 说:

- "帮我诊断这份简历" + 上传 PDF
- "用 ResumeAgent 模拟面试我"
- "获取这个 session 的评估报告"

agent 会自动加载 skill 并按文档调用 API。

## skill 封装的能力

| 阶段 | 端点 | 说明 |
|------|------|------|
| 上传 | `POST /api/sessions` | PDF/Word/TXT 四重校验,Celery 异步解析 |
| 解析轮询 | `GET /api/sessions/{id}/status` | 直到 status=parsed |
| 解析结果 | `GET /api/sessions/{id}/parse` | 结构化简历(已脱敏) |
| 修正 | `PUT /api/sessions/{id}/parse` | 用户内联编辑 |
| 诊断 | `POST/GET /api/sessions/{id}/diagnose` | AI 识别存疑点(高/中/低优先级) |
| 开始面试 | `POST /api/sessions/{id}/interview/start` | 选择存疑点子集进入面试 |
| 面试循环 | `POST /api/sessions/{id}/interview/respond` | 决策:follow_up / next_point / report |
| 跳过/换问法 | `POST /api/sessions/{id}/interview/skip` `/rephrase` | 用户控制 |
| 恢复 | `GET /api/sessions/{id}/interview/resume` | 断点续传 |
| 主动结束 | `POST /api/sessions/{id}/interview/end` | 用已有数据生成报告 |
| 报告 | `GET /api/sessions/{id}/report` | 可信度评分 + 逐点反馈 |
| WebSocket | `WS /ws/interview/{id}` | 实时流式 + 断线重连消息补推 |

## 技术特性

- **异步轮询**:解析、诊断是 Celery 异步任务,skill 文档中写明了轮询策略(1-2 秒间隔,上限 60 次)
- **敏感信息脱敏**:API 返回的手机号/邮箱/姓名都是掩码后的
- **LLM 降级**:LLM 不可用时自动切换规则/模板,服务不会完全不可用
- **面试防死循环**:单点最多 3 轮追问,连续异常 3 次熔断
- **双模式**:REST 同步(推荐 agent 场景)+ WebSocket 流式(适合交互场景)

## License

MIT
