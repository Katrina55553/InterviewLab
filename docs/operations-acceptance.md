# 上线运维验收

本轮按用户确认先完成本地验收准备。线上发布、业务数据库恢复、上一版本切换、真实收件渠道和实际规模压测，须在指定验收环境执行并另行记录结果。协议替身、脚本替身和本地通知接收器分别验证对应机制，不能据此勾选线上验收通过。执行记录见 [verification.md](verification.md)，操作细节见 [工程化运行手册](engineering-operations.md)。

## 验收对象和门禁

| 项目 | 通过条件 | 必须保存的证据 |
| --- | --- | --- |
| 远端 CI | 待发布完整 SHA 的 Frontend、Backend、Docker 全部成功，包含故障、容量、浏览器及告警检查 | SHA、run URL、作业结论、同次运行的 release/engineering-evidence 制品 |
| 实际发布 | 部署该次成功 CI 的制品，校验镜像、Compose 和脚本；迁移、组件健康、三个队列的持久任务探针通过 | 部署 run URL、镜像 ID、前后版本、维护窗口、健康和任务探针结果 |
| 备份恢复 | 停写后的数据库、上传文件和对应私有配置一起备份；在新建隔离资源恢复，表结构、迁移版本、逐表行数和文件摘要一致 | 私密备份目录、SHA256SUMS、恢复结果、耗时、业务抽检；备份本体不上传 CI |
| 版本回退 | 上一份已测试制品通过配置与当前数据库兼容检查，切换后健康和任务探针通过 | 两个 SHA、切换耗时、兼容检查、健康和任务结果；不执行数据库 downgrade |
| 告警通知 | 健康时静默；故障触发及恢复通知均实际到达指定渠道，确认收件人和值班处理 | 告警名称、触发/送达/恢复时间、渠道回执；本地接收记录只验收本地链路 |
| 实际规模压测 | 按约定人数、数据规模、持续时间和供应商额度，满足事先确定的错误率、P95、吞吐、资源上限和恢复要求 | 硬件/容器限额、数据规模、负载模型、阈值、原始 JSON、资源与队列曲线 |

当前工作区未提交的修改不在既有远端 CI 的验收范围内。先固定提交和目标环境，再运行上述门禁；本轮不推送、不发布。

## 本地通知链路演练

```bash
python scripts/check-notifications.py
```

脚本创建随机命名的独立 Compose 项目，运行真实 Prometheus、Alertmanager 和本地 HTTP 接收器；使用正式 `operations/alerts.yml` 的数据库告警，验证健康时静默、超过30秒后触发、恢复后送达 resolved。只将合成指标置为故障，不停止业务数据库。接收器只监听 localhost 随机端口，不请求外部通知服务，结束时清理本次项目及卷。

结果、时间和清理状态写入 Git 忽略的 `backend/.test-work/operations/resumeagent-notifications-*/notifications.json`，容器日志保存在同目录；失败退出非零。CI 在成功或失败时上传该证据目录。

生产通知需提供 **Alertmanager webhook 格式兼容**的接收地址。企业微信、飞书等机器人的普通 webhook 通常需要格式适配，或在 Alertmanager 中配置相应原生 receiver；本轮不假设已有渠道。

在部署机创建私密 `operations/alert-webhook.url` 文件，只写接收 URL，由 Alertmanager 的 `url_file` 读取。该文件及 `/backups/` 已加入 Git 忽略，发布制品只复制显式列出的配置类型。发布目录启动监控时，`ALERT_WEBHOOK_FILE` 应使用稳定位置的绝对路径：

```bash
export ALERT_WEBHOOK_FILE=/root/resume-agent/operations/alert-webhook.url
docker compose -p resumeagent -f releases/<sha>/docker-compose.yml --profile monitoring up -d prometheus alertmanager
```

文件须对容器中的 Alertmanager 用户可读，并通过宿主父目录权限限制其他用户访问。缺少接收 URL 文件时监控启动失败，不会把无接收器当作通知可用。Prometheus 与 Alertmanager 均不发布公网端口。通知配置参见 [Alertmanager 官方文档](https://prometheus.io/docs/alerting/latest/configuration/)。配置完成后仍须向实际渠道演练一次触发和恢复，并确认值班人收到。

## 发布前的私密备份与恢复演练

`deploy-release.sh` 在维护窗口停止全部写入进程（含 `celery-beat`），启动并等待数据库就绪，然后备份数据库、上传卷和原始 `backend/.env`。备份包含数据、凭据及 `SECRET_KEY`，目录权限700、文件默认600；不要把它加入 Git、CI 制品或普通工单附件。失败的备份带 `.incomplete`，不得用于恢复。服务器目录须另有访问控制、加密离机副本和保留策略；本地脚本不替代灾备存储。

```bash
bash releases/<sha>/scripts/restore-drill.sh \
  /root/resume-agent/backups/<backup-directory> \
  resume-agent-backend:<sha>
```

演练只新建随机命名的 PostgreSQL 容器和两个卷，使用 `--network none`，不开放主机端口、不连接业务网络，也不接受业务数据库目标。恢复成功需同时验证结构摘要、逐表行数、Alembic head、上传文件大小及 SHA256、私有配置完整性，以及使用备份 `SECRET_KEY` 解密现存和冻结配置中的凭据。校验器立即丢弃明文，仅输出通过的字段数量，不调用 AI。临时资源自动清理，清理失败同样非零退出。密码学一致不等于业务功能已抽检；真实环境另需以脱敏测试账号确认历史事实、私有文件及模型连接正常读取。

记录备份起止、恢复起止、备份时间点、可接受的数据损失和恢复时限，才可评价 RPO/RTO。全量停写备份有维护窗口，当前不提供在线时间点恢复。实际恢复到业务环境的切换方案须单独确定。

## 回退准备

保留上一份成功 CI 的 release 制品和镜像。`rollback-release.sh` 先检查私有配置及旧镜像对当前数据库的实际兼容性，再停止并替换 Web、两类 Worker、定时调度、dispatcher、inspector、metrics 和前端，最后检查健康和持久队列。数据库不兼容时在停写前拒绝切换。脚本替身测试只证明命令顺序和失败门禁，不能证明某个历史镜像确实兼容。

## 压测准备

HTTP 读取压测支持固定并发、持续时间、请求总量、单请求总截止时间、错误率、P95 和成功吞吐门槛；任何门槛失败均保存 JSON 并非零退出。以下仅为**参数示例**，不代表本项目已承诺或实测32并发容量：

```bash
python backend/tests/load_platform.py \
  --url http://127.0.0.1:18081 --path /health/ready \
  --duration 60 --requests 10000 --concurrency 32 --timeout 5 \
  --max-error-rate 0.01 --max-p95-ms 500 --min-throughput 10 \
  --output backend/.test-work/operations/http-load.json
```

用 `--cookie-env LOAD_TEST_COOKIE --path /api/auth/me` 验证有鉴权的读取，Cookie 从私密环境变量传入；证据不保存 Cookie 或查询参数。不要把凭据放在 URL 中。此工具只发送 HTTP GET，健康请求的吞吐不能换算为同时面试人数。`capacity_probe.py` 另验证双实例共享许可、拒绝延迟、故障时事实读取和模型总超时；其性能目标失败现在会令 CI 失败。

实际规模验收仍需补齐约定：同时在线与同时面试人数、用户到达/思考时间、简历与历史数据量、LLM/SSE/WS/报告混合比例、真实供应商额度和成本上限、稳态/峰值/恢复时长。运行期间采集 PG 连接、容器内存/CPU、队列积压、未知结果与模型在途数，并抽检历史事实没有改变。未确定这些条件前，不记录生产容量结论。
