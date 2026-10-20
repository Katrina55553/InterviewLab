import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.E2E_BASE_URL || 'http://127.0.0.1:5173';
const output = resolve(process.env.E2E_OUTPUT || 'test-results');
const username = process.env.E2E_ADMIN_USERNAME;
const password = process.env.E2E_ADMIN_PASSWORD;
assert.ok(username && password, 'Use a disposable local administrator via E2E_ADMIN_USERNAME / E2E_ADMIN_PASSWORD.');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
page.on('dialog', dialog => dialog.accept());
const suffix = Date.now();
const providerName = `E2E provider ${suffix}`;
const agentName = `E2E independent ${suffix}`;
const fakeKey = `test-only-not-a-real-key-${suffix}`;
let createdProviderId = null;
let createdAgentId = null;
const getProviders = async () => (await page.request.get(`${base}/api/admin/providers`)).json();
const getAgents = async () => (await page.request.get(`${base}/api/admin/agents`)).json();
async function mutation(path, action) {
  const [response] = await Promise.all([
    page.waitForResponse(response => response.url().includes(path) && ['POST', 'PUT', 'DELETE'].includes(response.request().method())),
    action(),
  ]);
  if (!response.ok()) throw new Error(`Request failed (${response.status()}): ${await response.text()}`);
  return response.status() === 204 ? null : response.json();
}
try {
  await page.goto(`${base}/login`);
  await page.getByRole('heading', { name: '欢迎回来', exact: true }).waitFor();
  await page.getByLabel('用户名', { exact: true }).fill(username);
  await page.getByLabel('密码', { exact: true }).fill(password);
  assert.equal(await page.getByLabel('用户名', { exact: true }).inputValue(), username);
  const [loginResponse] = await Promise.all([
    page.waitForResponse(response => response.url().endsWith('/api/auth/login') && response.request().method() === 'POST'),
    page.getByRole('button', { name: '登录研习室', exact: true }).click(),
  ]);
  assert.equal(loginResponse.status(), 200, await loginResponse.text());
  await page.getByRole('link', { name: '管理设置', exact: true }).click();
  await page.getByLabel('连接名称', { exact: true }).fill(providerName);
  await page.getByLabel('API 地址', { exact: true }).fill('https://example.invalid/v1');
  await page.getByLabel('模型名称', { exact: true }).fill('test-model');
  await page.getByLabel('API 密钥', { exact: true }).fill(fakeKey);
  await page.getByLabel('启用此模型', { exact: true }).uncheck();
  const provider = await mutation('/api/admin/providers', () => page.getByRole('button', { name: '保存模型配置', exact: true }).click());
  createdProviderId = provider.id;
  assert.equal(provider.enabled, false);
  assert.equal(provider.has_key, true);
  assert.ok(!JSON.stringify(await getProviders()).includes(fakeKey));
  await page.locator('.config-row').filter({ hasText: providerName }).getByRole('button', { name: '编辑', exact: true }).click();
  assert.equal(await page.getByLabel('API 密钥', { exact: true }).inputValue(), '');
  await page.getByLabel('模型名称', { exact: true }).fill('test-model-v2');
  const updatedProvider = await mutation('/api/admin/providers', () => page.getByRole('button', { name: '保存模型配置', exact: true }).click());
  assert.equal(updatedProvider.model, 'test-model-v2');
  assert.equal(updatedProvider.has_key, true);
  assert.ok(!await page.locator('body').innerText().then(text => text.includes(fakeKey)));
  await page.screenshot({ path: resolve(output, '09-admin-providers.png'), fullPage: true });
  await page.getByRole('button', { name: '智能体配置', exact: true }).click();
  assert.equal(await page.getByRole('combobox', { name: /^使用场景/ }).inputValue(), '');
  await page.getByLabel('智能体名称', { exact: true }).fill(agentName);
  await page.getByRole('combobox', { name: /^使用模型/ }).selectOption(provider.id);
  await page.getByLabel('系统提示词', { exact: true }).fill('帮助用户使用具体、可验证的事实描述项目经历。');
  await page.getByLabel('lookup_resume_field', { exact: true }).check();
  await page.getByLabel('启用此智能体', { exact: true }).uncheck();
  const agent = await mutation('/api/admin/agents', () => page.getByRole('button', { name: '保存智能体', exact: true }).click());
  createdAgentId = agent.id;
  assert.equal(agent.scene, null);
  assert.deepEqual(agent.tools, ['lookup_resume_field']);
  assert.equal(agent.enabled, false);
  await page.locator('.config-row').filter({ hasText: agentName }).getByRole('button', { name: '编辑', exact: true }).click();
  assert.equal(await page.getByRole('combobox', { name: /^使用场景/ }).inputValue(), '');
  await page.getByRole('combobox', { name: /^使用模型/ }).selectOption('');
  await page.getByLabel('启用此智能体', { exact: true }).check();
  const updatedAgent = await mutation('/api/admin/agents', () => page.getByRole('button', { name: '保存智能体', exact: true }).click());
  assert.equal(updatedAgent.provider_id, null);
  assert.equal(updatedAgent.enabled, true);
  assert.equal(updatedAgent.scene, null);
  assert.ok((await getAgents()).some(item => item.id === agent.id));
  await page.screenshot({ path: resolve(output, '10-admin-agents.png'), fullPage: true });
  await page.getByRole('link', { name: 'AI 对话', exact: true }).click();
  await page.getByRole('combobox', { name: /^对话助手/ }).selectOption(agent.id);
  const chat = await mutation('/api/chats', () => page.getByRole('button', { name: '新建对话', exact: true }).click());
  assert.equal(chat.agent_id, agent.id);
  await page.getByLabel('消息内容').fill('如何把技术决策讲清楚？');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await page.locator('.message-assistant .rich-text').waitFor();
  await page.getByRole('button', { name: '发送', exact: true }).waitFor();
  await page.getByRole('link', { name: '管理设置', exact: true }).click();
  await page.getByRole('button', { name: '智能体配置', exact: true }).click();
  await mutation(`/api/admin/agents/${agent.id}`, () => page.locator('.config-row').filter({ hasText: agentName }).getByRole('button', { name: '删除', exact: true }).click());
  createdAgentId = null;
  await page.getByRole('button', { name: '模型连接', exact: true }).click();
  await mutation(`/api/admin/providers/${provider.id}`, () => page.locator('.config-row').filter({ hasText: providerName }).getByRole('button', { name: '删除', exact: true }).click());
  createdProviderId = null;
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, checks: ['admin route', 'create + edit provider', 'secret not returned', 'blank key preserves secret', 'independent Agent scene=null', 'tool selection', 'Agent enable + provider reset', 'Agent-backed SSE chat', 'remove own test fixtures'], screenshots: output }, null, 2));
} catch (error) {
  await page.screenshot({ path: resolve(output, 'admin-failure.png'), fullPage: true });
  console.error(await page.locator('body').innerText());
  throw error;
} finally {
  const csrf = (await page.context().cookies()).find(cookie => cookie.name === 'ra_csrf')?.value;
  if (csrf) {
    if (createdAgentId) await page.request.delete(`${base}/api/admin/agents/${createdAgentId}`, { headers: { 'X-CSRF-Token': csrf } }).catch(() => {});
    if (createdProviderId) await page.request.delete(`${base}/api/admin/providers/${createdProviderId}`, { headers: { 'X-CSRF-Token': csrf } }).catch(() => {});
  }
  await browser.close();
}
