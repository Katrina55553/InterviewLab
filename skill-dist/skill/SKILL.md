---
name: "resume-agent-api"
description: "Calls ResumeAgent backend API to run AI resume diagnosis and mock interview. Invoke when user wants to upload a resume, get a diagnosis report, conduct a mock interview, or fetch an evaluation report through the ResumeAgent service."
---

# ResumeAgent API 对外服务封装

封装 ResumeAgent 后端的 REST + WebSocket API,让外部 agent 能通过标准 HTTP 调用完整的「简历上传 → 解析 → 诊断 → 模拟面试 → 评估报告」流程。

## 前置条件

服务必须已启动。默认 Base URL:`http://localhost:8000`(Docker 部署时为 Nginx 入口地址)。

健康检查:

```bash
curl http://localhost:8000/health
# {"status":"ok","version":"0.1.0"}
```

如果健康检查失败,先告知用户启动服务(`docker compose up -d` 或 `cd backend && uvicorn app.main:app --reload`),不要继续后续调用。

## 统一响应格式

所有 REST 端点返回:

```json
{ "code": 0, "message": "success", "data": { ... } }
```

错误时 `code` 非 0,`data` 中含 `status` 字段表示会话当前状态。HTTP 状态码 4xx/5xx 时 `detail.message` 描述错误原因。

## 完整工作流

```
1. 上传简历        POST /api/sessions                       → session_id
2. 轮询解析进度    GET  /api/sessions/{id}/status           → 直到 status=parsed
3. 获取解析结果    GET  /api/sessions/{id}/parse            → 结构化简历(可修正)
4. (可选)修正解析  PUT  /api/sessions/{id}/parse
5. 触发诊断        POST /api/sessions/{id}/diagnose
6. 轮询诊断进度    GET  /api/sessions/{id}/diagnose         → 直到 status=diagnosed
7. 开始面试        POST /api/sessions/{id}/interview/start  → first_question
8. 面试循环        POST /api/sessions/{id}/interview/respond (REST)
                   或  WS   /ws/interview/{id}              (实时流式)
9. 获取报告        GET  /api/sessions/{id}/report
```

步骤 2、6 是异步的(Celery 处理),必须轮询直到完成。步骤 3、5 之间存在可选的解析结果修正环节。

---

## 端点详解

### 1. 上传简历

```
POST /api/sessions
Content-Type: multipart/form-data
```

| 字段 | 类型 | 说明 |
|------|------|------|
| file | file | 简历文件,支持 pdf/docx/doc/txt,≤10MB |

四重校验:扩展名 + MIME + 魔数 + 大小。校验失败返回 400。

```bash
curl -X POST http://localhost:8000/api/sessions \
  -F "file=@resume.pdf"
```

响应:

```json
{
  "code": 0,
  "message": "简历已上传,正在解析",
  "data": {
    "session_id": "abc12345-...",
    "task_id": "celery-task-id",
    "status": "parsing"
  }
}
```

**记住 `session_id`,后续所有接口都需要它。**

### 2. 轮询解析进度

```
GET /api/sessions/{session_id}/status
```

```bash
curl http://localhost:8000/api/sessions/{session_id}/status
```

响应:

```json
{
  "code": 0,
  "data": {
    "session_id": "...",
    "status": "parsing",      // uploaded→parsing→parsed / failed
    "progress": 0.5
  }
}
```

**轮询策略**:每 1-2 秒查询一次,直到 `status` 为 `parsed`(成功)或 `failed`(失败,看 `error` 字段)。建议最多轮询 60 次(约 2 分钟),超时则告知用户。

### 3. 获取解析结果

```
GET /api/sessions/{session_id}/parse
```

仅当 `status` 为 `parsed` 及之后状态时可调用,否则返回 400。

```json
{
  "code": 0,
  "data": {
    "session_id": "...",
    "status": "parsed",
    "parsed_data": {
      "name": "张*",            // 已脱敏
      "phone": "138****1234",
      "email": "z***@example.com",
      "education": [...],
      "experience": [...],
      "skills": [...]
    }
  }
}
```

**敏感字段(手机号/邮箱/姓名)已脱敏**,展示给用户即可。

### 4. (可选)修正解析结果

```
PUT /api/sessions/{session_id}/parse
Content-Type: application/json
```

仅在 `status=parsed` 时可调用。用户发现解析错误时,提交修正后的完整 `parsed_data`:

```bash
curl -X PUT http://localhost:8000/api/sessions/{session_id}/parse \
  -H "Content-Type: application/json" \
  -d '{"parsed_data": {"name":"张三","skills":["Python","FastAPI"]}}'
```

### 5. 触发诊断

```
POST /api/sessions/{session_id}/diagnose
```

前提:`status=parsed`。触发后状态变为 `diagnosing`。

```bash
curl -X POST http://localhost:8000/api/sessions/{session_id}/diagnose
```

```json
{
  "code": 0,
  "message": "诊断已启动",
  "data": { "session_id": "...", "task_id": "...", "status": "diagnosing" }
}
```

### 6. 轮询诊断结果

```
GET /api/sessions/{session_id}/diagnose
```

```bash
curl http://localhost:8000/api/sessions/{session_id}/diagnose
```

诊断进行中:

```json
{ "code": 0, "data": { "status": "diagnosing", "progress": 0.3 } }
```

诊断完成:

```json
{
  "code": 0,
  "data": {
    "session_id": "...",
    "status": "diagnosed",
    "result": {
      "overall_assessment": "...",
      "doubt_points": [
        {
          "id": "point_0",
          "source_text": "简历中提到的原文字段",
          "priority": "high",       // high / medium / low
          "question_hint": "建议追问方向"
        }
      ],
      "improvement_suggestions": [...]
    }
  }
}
```

**轮询直到 `status=diagnosed`**。`doubt_points` 是后续面试的核心——每个点有 `id`,用户可挑选子集进入面试。

### 7. 开始面试

```
POST /api/sessions/{session_id}/interview/start
Content-Type: application/json
```

前提:`status` 为 `diagnosed` / `interviewing` / `completed`。

可选请求体:

```json
{ "selected_point_ids": ["point_0", "point_2"] }
```

- 不传 `selected_point_ids`:用全部存疑点(兼容旧前端)
- 传空列表 `[]`:返回 400 错误
- 传非空子集:只面试选中的点

```bash
curl -X POST http://localhost:8000/api/sessions/{session_id}/interview/start \
  -H "Content-Type: application/json" \
  -d '{"selected_point_ids": ["point_0", "point_1"]}'
```

响应:

```json
{
  "session_id": "...",
  "interview_id": "...",
  "status": "started",        // started / resumed
  "first_question": "请详细说明你在 XX 项目中的角色...",
  "point_id": "point_0",
  "round": 1,
  "total_points": 2,
  "point_list": [
    { "id": "point_0", "source_text": "...", "priority": "high", "status": "active" },
    { "id": "point_1", "source_text": "...", "priority": "medium", "status": "pending" }
  ]
}
```

如果面试已存在且未完成,会返回 `status: "resumed"` 并带上当前问题。

### 8. 面试循环(两种模式)

#### 模式 A:REST 同步调用

**提交回答:**

```
POST /api/sessions/{session_id}/interview/respond
Content-Type: application/json
```

请求体(`content` 长度 1-5000):

```json
{ "content": "我在该项目中负责后端架构设计..." }
```

响应中 `decision` 决定下一步:

| decision | 含义 | 响应包含 |
|----------|------|----------|
| `follow_up` | 继续追问当前点 | `question`、`point_id`、`round` |
| `next_point` | 切换到下一个存疑点 | `question`、`point_id`、`round` |
| `report` | 所有点处理完,生成报告 | `report`(完整评估报告) |

```bash
curl -X POST http://localhost:8000/api/sessions/{session_id}/interview/respond \
  -H "Content-Type: application/json" \
  -d '{"content": "我使用了 FastAPI + LangGraph..."}'
```

**跳过当前点:**

```
POST /api/sessions/{session_id}/interview/skip
```

标记当前点为 `skipped`,切换下一个。如果是最后一个点,直接生成报告。

**换个问法:**

```
POST /api/sessions/{session_id}/interview/rephrase
```

重新生成当前点的问题(不增加轮次)。

**恢复面试(断点续传):**

```
GET /api/sessions/{session_id}/interview/resume
```

返回完整状态:消息历史、当前问题、`point_states`、进度。

**主动结束:**

```
POST /api/sessions/{session_id}/interview/end
```

用已有评估数据生成报告,标记所有未处理点为 `skipped`。

#### 模式 B:WebSocket 实时流式

```
WS /ws/interview/{session_id}
```

**客户端发送**:

```json
{ "type": "answer", "content": "..." }
{ "type": "skip" }
{ "type": "rephrase" }
{ "type": "start" }          // 开始/恢复,重连时补推缓存消息
```

**服务端推送**:

| type | 含义 | 字段 |
|------|------|------|
| `question_start` | 流式问题开始 | `point_id`、`round` |
| `chunk` | 流式文本片段 | `content`(增量) |
| `question` | 完整问题 | `content`、`point_id`、`round` |
| `status` | 状态更新 | `point_states`、`progress` |
| `complete` | 面试完成 | `report` |
| `error` | 错误 | `code`、`error` |

**断线重连**:服务端用 Redis 缓存未消费消息,重连后发送 `{type: "start"}` 即可补推。建议指数退避重连,最多 5 次。

agent 场景下**优先用 REST 模式**(模式 A),WebSocket 适合需要流式输出的交互场景。

### 9. 获取评估报告

```
GET /api/sessions/{session_id}/report
```

```bash
curl http://localhost:8000/api/sessions/{session_id}/report
```

面试未完成时:

```json
{ "code": 0, "data": { "session_id": "...", "status": "interviewing" } }
```

面试完成时:

```json
{
  "code": 0,
  "data": {
    "session_id": "...",
    "status": "completed",
    "report": {
      "credibility_score": 78,        // 可信度评分
      "evaluations": [...],            // 逐点反馈
      "improvement_suggestions": [...] // 改进建议
    }
  }
}
```

---

## 错误码参考

| code | HTTP | 含义 |
|------|------|------|
| 1002 | 404 | 会话/面试不存在 |
| 1003 | 400 | 状态不允许(如未解析完就诊断) |
| 1004 | 400 | 无存疑点 / 选中的存疑点无效 |
| 1005 | 400 | 面试已结束,无法继续 |

遇到 1003 时,先调用 `/status` 查看当前状态,按工作流顺序补齐前置步骤。

## 使用要点

1. **始终保留 `session_id`**——它是整个流程的串联主键。
2. **异步步骤必须轮询**:解析(parsing→parsed)、诊断(diagnosing→diagnosed)。轮询间隔 1-2 秒,上限 60 次。
3. **敏感信息已脱敏**:API 返回的手机号/邮箱/姓名都是掩码后的,原值不会泄露。
4. **LLM 不可用时自动降级**:解析和面试节点有规则/模板降级,服务不会因 LLM 故障完全不可用,但问题质量会下降——这种情况要告知用户。
5. **面试防死循环**:单点最多 3 轮追问,连续异常 3 次熔断。用户可随时 `skip` 或 `end`。
6. **agent 集成建议**:用 REST 模式逐轮交互,把每轮的 `question` 展示给用户、收集用户回答后调 `/respond`,直到 `decision=report`。
