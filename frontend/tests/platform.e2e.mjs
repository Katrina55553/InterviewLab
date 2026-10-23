import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';

// Use a locally installed playwright, or the host's preinstalled runtime package.
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.E2E_BASE_URL || 'http://127.0.0.1:5173';
const output = resolve(process.env.E2E_OUTPUT || 'test-results');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1050 } });
const page = await context.newPage();
const failures = [];
page.on('pageerror', error => failures.push(error.message));
const timestamp = Date.now();
const username = `e2e_${timestamp}`;
const password = `Local-e2e-${timestamp}!`;
const answer = '在这个项目中，我负责异步队列与接口设计。最初通过监控定位到数据库连接等待时间较长，进一步用压测复现并对比调用链。我的方案是把耗时任务改为队列执行，增加幂等键、超时与失败重试，明确任务状态，再通过固定数据集对比吞吐量和延迟。最终请求延迟下降约百分之六十，并通过故障演练验证任务不会丢失。这些结果都可以在监控与测试记录中验证。';

async function snapshot(id) {
  const response = await page.request.get(`${base}/api/interviews/${id}`);
  assert.equal(response.status(), 200);
  return response.json();
}
async function commandClick(name) {
  const [response] = await Promise.all([
    page.waitForResponse(response => response.url().endsWith('/commands') && response.request().method() === 'POST'),
    page.getByRole('button', { name, exact: true }).click(),
  ]);
  assert.equal(response.status(), 200, await response.text());
  return response.json();
}
try {
  await page.goto(base);
  await page.getByRole('heading', { name: '欢迎回来' }).waitFor();
  await page.screenshot({ path: resolve(output, '01-login.png'), fullPage: true });
  await page.getByRole('button', { name: '创建一个', exact: true }).click();
  await page.getByLabel('用户名', { exact: true }).fill(username);
  await page.getByLabel('密码', { exact: true }).fill(password);
  await page.getByRole('button', { name: '创建账号', exact: true }).click();
  await page.getByRole('heading', { name: '简历诊断' }).waitFor();
  await page.screenshot({ path: resolve(output, '02-resumes.png'), fullPage: true });
  assert.equal(await page.getByRole('link', { name: '管理设置', exact: true }).count(), 0);
  const cookies = await context.cookies();
  assert.ok(cookies.find(cookie => cookie.name === 'ra_session')?.httpOnly);
  assert.ok(cookies.find(cookie => cookie.name === 'ra_csrf' && !cookie.httpOnly));
  await page.getByLabel('上传简历文件').setInputFiles({
    name: 'e2e-resume.txt', mimeType: 'text/plain',
    buffer: Buffer.from('姓名：测试候选人\n个人概述：三年软件工程经验。\n技术技能：Python、FastAPI、React、PostgreSQL、Redis、Docker。\n工作经历：某软件公司 后端工程师 2023年到2026年。\n项目经历：负责异步任务队列、幂等接口、数据库索引优化与监控建设。使用 Celery 和 Redis 调度任务，使用 PostgreSQL 保存状态，通过压测减少响应延迟百分之六十。\n教育经历：某大学 软件工程 本科 2019年到2023年。'),
  });
  await page.waitForURL(/\/resumes\//);
  const resumeId = page.url().split('/').at(-1);
  await page.getByRole('link', { name: '用这份简历练习' }).waitFor({ timeout: 90000 });
  assert.ok(await page.getByText('演示模式 · 非真实 AI 评估').count());
  await page.screenshot({ path: resolve(output, '03-preparation.png'), fullPage: true });
  await page.getByRole('link', { name: '用这份简历练习' }).click();
  let current = await commandClick('开始模拟面试');
  const interviewId = current.id;
  assert.equal(current.version, 1);
  const question = current.current_question;
  await page.reload();
  await page.getByLabel('你的回答', { exact: true }).waitFor();
  assert.equal((await snapshot(interviewId)).current_question.id, question.id);
  await page.screenshot({ path: resolve(output, '04-interview.png'), fullPage: true });
  while (current.status === 'active') {
    await page.getByLabel('你的回答', { exact: true }).fill(answer);
    current = await commandClick('提交回答');
    assert.ok(current.version >= 2);
  }
  assert.equal(current.status, 'reporting');
  await page.getByRole('link', { name: '查看面试报告' }).click();
  await page.getByRole('heading', { name: '面试综合得分' }).waitFor({ timeout: 90000 });
  assert.ok(await page.getByText('逐轮回放', { exact: true }).count());
  assert.ok(await page.locator('svg[aria-label*="专业能力"]').count());
  const persisted = await snapshot(interviewId);
  assert.equal(persisted.report.completion.answered, 5);
  assert.equal(persisted.report.completion.skipped, 0);
  assert.equal(persisted.report.turns.length, 5);
  await page.screenshot({ path: resolve(output, '05-report.png'), fullPage: true });
  // A second session checks follow-up, rephrasing, explicit skip=0 and early ending.
  await page.goto(`${base}/resumes/${resumeId}`);
  await page.getByRole('link', { name: '用这份简历练习' }).click();
  const started = await commandClick('开始模拟面试');
  const partialId = started.id;
  await page.getByLabel('你的回答', { exact: true }).fill('用了 Redis。');
  let partial = await commandClick('提交回答');
  assert.equal(partial.current_question.is_followup, true);
  const followupId = partial.current_question.id;
  partial = await commandClick('换个问法');
  assert.notEqual(partial.current_question.id, followupId);
  assert.equal(partial.current_question.is_followup, true);
  partial = await commandClick('跳过这道题');
  assert.equal(partial.current_question.main_index, 1);
  partial = await commandClick('跳过这道题');
  assert.equal(partial.evaluations.at(-1).skipped, true);
  assert.equal(partial.evaluations.at(-1).score, 0);
  await page.getByRole('button', { name: '结束并复盘', exact: true }).click();
  await commandClick('结束并生成报告');
  await page.getByRole('link', { name: '查看面试报告' }).click();
  await page.getByRole('heading', { name: '面试综合得分' }).waitFor({ timeout: 90000 });
  const partialReport = (await snapshot(partialId)).report;
  assert.equal(partialReport.completion.answered, 1);
  assert.equal(partialReport.completion.skipped, 1);
  assert.equal(partialReport.turns.length, 2);
  assert.equal(partialReport.overall_score, Math.round(partialReport.turns[0].score / 2));
  await page.screenshot({ path: resolve(output, '08-partial-report.png'), fullPage: true });
  await page.goto(`${base}/resumes/${resumeId}`);
  await page.getByRole('button', { name: '和 AI 讨论改进建议', exact: true }).click();
  await page.waitForURL(/\/chat\//);
  await page.getByLabel('消息内容').fill('怎样用 STAR 方法介绍一个技术项目？');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await page.getByRole('button', { name: '发送', exact: true }).waitFor({ timeout: 60000 });
  await page.locator('.message-assistant').first().waitFor();
  const chatId = page.url().split('/').at(-1);
  const chat = await (await page.request.get(`${base}/api/chats/${chatId}`)).json();
  assert.ok(chat.messages.some(message => message.role === 'assistant' && message.content.length));
  await page.screenshot({ path: resolve(output, '06-chat.png'), fullPage: true });
  await page.reload();
  await page.locator('.message-assistant').first().waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: '打开导航菜单', exact: true }).click();
  await page.getByRole('link', { name: '简历诊断', exact: true }).click();
  await page.getByRole('heading', { name: '简历诊断' }).waitFor();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
  await page.screenshot({ path: resolve(output, '07-mobile-resumes.png'), fullPage: true });
  await page.getByRole('button', { name: '打开导航菜单', exact: true }).click();
  await page.getByRole('button', { name: '退出', exact: true }).click();
  await page.getByRole('heading', { name: '欢迎回来' }).waitFor();
  await page.getByLabel('用户名', { exact: true }).fill(username);
  await page.getByLabel('密码', { exact: true }).fill(password);
  await page.getByRole('button', { name: '登录知面', exact: true }).click();
  await page.getByRole('heading', { name: '简历诊断' }).waitFor();
  assert.deepEqual(failures, [], `Browser errors: ${failures.join('; ')}`);
  console.log(JSON.stringify({ passed: true, username, interviewId, chatId, screenshots: output, checks: ['cookie auth + CSRF', 'resume preparation', 'same question after reload', '5 persisted answer evaluations', 'followup + rephrase', 'skip=0 + early end', 'async report', 'SSE chat persistence', 'mobile layout', 'logout + login'] }, null, 2));
} catch (error) {
  await page.screenshot({ path: resolve(output, 'failure.png'), fullPage: true });
  console.error(await page.locator('body').innerText());
  throw error;
} finally { await browser.close() }
