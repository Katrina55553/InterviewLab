import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { chromium } from 'playwright';
import { createServer } from 'vite';

// Isolated UI fixtures: no server credentials, model calls, or persistent writes.
const server = process.env.E2E_BASE_URL ? null : await createServer({ server: { host: '127.0.0.1', port: 5177, strictPort: true } });
await server?.listen();
const base = process.env.E2E_BASE_URL || 'http://127.0.0.1:5177';
const output = resolve(process.env.E2E_OUTPUT || 'test-results/dropdowns');
await mkdir(output, { recursive: true });
const providers = [
  { id: 'deepseek', name: 'DeepSeek', baseUrl: 'https://api.deepseek.com', defaultModel: 'deepseek-flash' },
  { id: 'openai-api', name: 'OpenAI', baseUrl: 'https://api.openai.com/v1' },
  { id: 'openrouter', name: 'OpenRouter', baseUrl: 'https://openrouter.ai/api/v1' },
  { id: 'siliconflow', name: '硅基流动', baseUrl: 'https://api.siliconflow.cn/v1' },
  { id: 'dashscope', name: '阿里云百炼', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1' },
  { id: 'moonshot', name: 'Moonshot（Kimi）', baseUrl: 'https://api.moonshot.cn/v1' },
  { id: 'bigmodel', name: '智谱 AI', baseUrl: 'https://open.bigmodel.cn/api/paas/v4' },
].map(provider => ({ ...provider, auth: { apiKey: { label: 'API Key', interactive: true } } }));
const browser = await chromium.launch({ headless: true, channel: process.env.E2E_BROWSER_CHANNEL || undefined });
const page = await browser.newPage({ viewport: { width: 1440, height: 1080 } });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const unexpected = [];
await page.route('**/api/**', async route => {
  const url = new URL(route.request().url());
  const path = url.pathname;
  let body;
  if (path === '/api/auth/me') body = { id: 'ui-test', username: '界面测试', role: 'admin' };
  else if (path === '/api/repository/stats') body = { stars: 0, forks: 0 };
  else if (path === '/api/settings/ai') body = { selection: null, version: 0, has_key: false, allowed_base_urls: [] };
  else if (path === '/api/settings/ai/providers') body = [...providers, { id: 'openai', name: 'ChatGPT', auth: { oauth: { label: 'ChatGPT', isSubscription: true } } }];
  else if (path === '/api/settings/ai/auth') body = { state: 'configured', method: url.searchParams.get('provider_id') === 'openai' ? 'oauth' : 'api-key' };
  else if (path === '/api/settings/ai/models') {
    const providerId = url.searchParams.get('provider_id');
    const models = providerId === 'deepseek' ? [{ id: 'deepseek-flash', name: 'DeepSeek-V4.1-Flash' }, { id: 'deepseek-v4-pro', name: 'DeepSeek-V4-Pro' }] : [{ id: 'account-model', name: '账户模型' }];
    body = models.map(model => ({ ...model, providerId, input: ['text'], reasoning: false, contextWindow: 8192, maxTokens: 4096 }));
  }
  else if (path === '/api/admin/agents') body = [];
  else if (path === '/api/admin/capabilities') body = { demo_mode: false, asr: false, tts: false, vision: true, scenes: { vision: { enabled: true, agent_id: null } } };
  else if (path === '/api/community/meta') body = { companies: ['腾讯', '字节跳动', '一家名称很长很长的测试公司'], topics: [], myPosts: 0, myLikes: 0 };
  else if (path === '/api/community/posts') body = { items: [], total: 0, page: 1, pageSize: 5 };
  else if (path === '/api/community/draft') body = { draft: null, version: 0 };
  else { unexpected.push(path); return route.fulfill({ status: 404, json: { detail: 'Unmocked UI test route' } }) }
  return route.fulfill({ json: body });
});
await page.route('https://api.github.com/**', route => route.fulfill({ json: {} }));

async function assertMenuFits() {
  const box = await page.getByRole('listbox').boundingBox();
  const viewport = page.viewportSize();
  assert.ok(box && box.x >= 0 && box.y >= 0 && box.x + box.width <= viewport.width + 1 && box.y + box.height <= viewport.height + 1, 'Menu stays within the viewport');
}

try {
  await page.goto(`${base}/settings/ai`);
  const provider = page.getByRole('combobox', { name: '服务商', exact: true });
  await provider.click();
  await page.getByRole('option', { name: 'DeepSeek', exact: true }).waitFor();
  assert.equal(await page.getByRole('listbox').evaluate(element => getComputedStyle(element).borderRadius), '12px');
  assert.equal(await page.getByRole('option', { name: 'DeepSeek', exact: true }).getAttribute('data-state'), 'checked');
  await assertMenuFits();
  await page.screenshot({ animations: 'disabled', path: resolve(output, 'provider-desktop.png') });
  await page.getByRole('option', { name: 'OpenAI', exact: true }).click();
  assert.equal(await page.getByRole('textbox', { name: '服务地址', exact: true }).inputValue(), 'https://api.openai.com/v1');
  await provider.focus();
  await page.keyboard.press('ArrowDown');
  await page.waitForFunction(() => document.activeElement?.getAttribute('role') === 'option');
  await page.keyboard.press('Home');
  await page.waitForFunction(() => document.activeElement?.textContent === 'DeepSeek');
  await page.keyboard.press('Enter');
  assert.equal(await provider.innerText(), 'DeepSeek');
  const model = page.getByRole('combobox', { name: '模型名称', exact: true });
  await model.click();
  await page.getByRole('option', { name: /DeepSeek-V4-Pro/ }).click();
  assert.equal(await model.inputValue(), 'DeepSeek-V4-Pro');
  assert.equal(await page.getByRole('listbox').count(), 0);
  await model.fill('custom-private-model');
  assert.equal(await model.inputValue(), 'custom-private-model');
  assert.equal(await page.getByRole('listbox').count(), 0);
  await model.fill('flash');
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
  assert.equal(await model.inputValue(), 'DeepSeek-V4.1-Flash');
  await page.getByRole('tab', { name: 'ChatGPT 订阅登录' }).click();
  await page.getByRole('combobox', { name: '模型', exact: true }).click();
  await page.getByRole('option', { name: '账户模型' }).waitFor();
  assert.equal(await page.getByRole('listbox').evaluate(element => getComputedStyle(element).borderRadius), '12px');
  await page.screenshot({ animations: 'disabled', path: resolve(output, 'subscription-desktop.png') });
  await page.keyboard.press('Escape');

  await page.goto(`${base}/admin`);
  await page.getByRole('heading', { name: '场景与智能体管理', exact: true }).waitFor();
  assert.equal(await page.getByRole('combobox', { name: '使用模型', exact: true }).count(), 0);
  const scene = page.getByRole('combobox', { name: '使用场景', exact: true });
  await scene.click();
  await page.getByRole('option', { name: '回答评分', exact: true }).click();
  assert.equal(await scene.innerText(), '回答评分');
  await scene.click();
  await page.getByRole('option', { name: '独立对话助手', exact: true }).click();
  assert.equal(await scene.innerText(), '独立对话助手');
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1080 });
    await page.screenshot({ animations: 'disabled', path: resolve(output, `admin-${width}.png`), fullPage: true });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'Admin page fits the viewport');
  }

  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1080 });
    await page.goto(`${base}/community`);
    const company = page.getByRole('combobox', { name: '筛选公司', exact: true });
    await company.click();
    await page.getByRole('option', { name: '一家名称很长很长的测试公司', exact: true }).click();
    assert.equal(await company.innerText(), '一家名称很长很长的测试公司');
    await company.click();
    await assertMenuFits();
    await page.screenshot({ animations: 'disabled', path: resolve(output, `company-${width}.png`) });
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: '发布面经', exact: true }).click();
    const direction = page.getByRole('combobox', { name: '岗位方向', exact: true });
    await direction.click();
    await page.getByRole('option', { name: '后端开发', exact: true }).waitFor();
    assert.equal(await page.locator('dialog [role=listbox]').count(), 1, 'Dialog menu stays in the top layer');
    await assertMenuFits();
    await page.screenshot({ animations: 'disabled', path: resolve(output, `dialog-${width}.png`) });
    await page.getByRole('option', { name: '后端开发', exact: true }).click();
    assert.equal(await direction.innerText(), '后端开发');
    await direction.click();
    await page.keyboard.press('Escape');
    assert.equal(await page.getByRole('dialog').count(), 1, 'Escape closes the menu before the dialog');
    assert.equal(await direction.evaluate(element => element === document.activeElement), true, 'Focus returns to the trigger');
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${base}/settings/ai`);
  await provider.click();
  await page.getByRole('option', { name: 'DeepSeek', exact: true }).waitFor();
  await assertMenuFits();
  await page.screenshot({ animations: 'disabled', path: resolve(output, 'provider-mobile.png') });
  await page.keyboard.press('Escape');
  await model.scrollIntoViewIfNeeded();
  await model.click();
  await page.getByRole('option', { name: /DeepSeek-V4-Pro/ }).waitFor();
  await assertMenuFits();
  await page.screenshot({ animations: 'disabled', path: resolve(output, 'model-mobile.png') });
  assert.deepEqual(unexpected, []);
  assert.deepEqual(errors, []);
  console.log(`Dropdown UI checks passed. Screenshots: ${output}`);
} finally {
  await browser.close();
  await server?.close();
}
