import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdir, rm, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const base = (process.env.E2E_BASE_URL || 'http://127.0.0.1:5173').replace(/\/$/, '');
const output = resolve(process.env.E2E_OUTPUT || 'test-results');
const cookies = new Map();
const created = new Set();
const username = `chat_e2e_${Date.now()}`;
const password = `Local-e2e-${randomUUID()}!`;
let user;
let failure;
let result;
await mkdir(output, { recursive: true });
await rm(resolve(output, 'chat-management-failure.png'), { force: true });

async function request(path, { method = 'GET', body, status = 200 } = {}) {
  const headers = new Headers();
  if (cookies.size) headers.set('Cookie', [...cookies].map(([key, value]) => `${key}=${value}`).join('; '));
  if (!['GET', 'HEAD'].includes(method) && cookies.has('ra_csrf')) headers.set('X-CSRF-Token', decodeURIComponent(cookies.get('ra_csrf')));
  const multipart = body instanceof FormData;
  if (body !== undefined && !multipart) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${base}/api${path}`, { method, headers, body: body === undefined ? undefined : multipart ? body : JSON.stringify(body), signal: AbortSignal.timeout(15000) });
  for (const cookie of response.headers.getSetCookie()) {
    const [pair] = cookie.split(';');
    const separator = pair.indexOf('=');
    cookies.set(pair.slice(0, separator), pair.slice(separator + 1));
  }
  const text = await response.text();
  assert.ok([status].flat().includes(response.status), `${method} ${path}: expected ${status}, got ${response.status} ${text}`);
  return text ? JSON.parse(text) : null;
}

async function browserAcceptance() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1050 } });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await context.tracing.start({ screenshots: true, snapshots: true, sources: true });

  const sessions = page.locator('.chat-sessions .chat-session-link');
  const assistantReply = page.locator('.message').filter({ has: page.getByText('助手', { exact: true }) }).locator('.rich-text');
  const menu = page.getByRole('menu', { name: '对话管理', exact: true });
  const renameDialog = page.getByRole('dialog', { name: '重命名对话', exact: true });
  const deleteDialog = page.getByRole('dialog', { name: '删除对话', exact: true });
  async function mutation(path, method, action, status = 200) {
    const [response] = await Promise.all([
      page.waitForResponse(response => new URL(response.url()).pathname === `/api${path}` && response.request().method() === method),
      action(),
    ]);
    const text = response.status() === 204 ? '' : await response.text();
    assert.equal(response.status(), status, `${method} ${path}: ${response.status()} ${text}`);
    return text ? JSON.parse(text) : null;
  }
  async function openMenu(title) {
    const row = page.locator('.chat-session').filter({ has: page.getByRole('button', { name: title, exact: true }) });
    await row.hover();
    await row.getByRole('button', { name: `管理对话：${title}`, exact: true }).click();
    await menu.waitFor();
  }
  async function rename(id, from, to) {
    await openMenu(from);
    await menu.getByRole('menuitem', { name: '重命名', exact: true }).click();
    await renameDialog.getByLabel('对话名称', { exact: true }).fill(to);
    const saved = await mutation(`/chats/${id}`, 'PATCH', () => renameDialog.getByRole('button', { name: '保存', exact: true }).click());
    assert.equal(saved.title, to.trim());
    await renameDialog.waitFor({ state: 'hidden' });
    await page.getByRole('heading', { name: to.trim(), exact: true }).waitFor();
  }
  async function order(titles) {
    await page.waitForFunction(expected => {
      const actual = [...document.querySelectorAll('.chat-sessions .chat-session-link')].map(node => node.textContent.trim());
      return JSON.stringify(actual) === JSON.stringify(expected);
    }, titles);
    assert.deepEqual(await sessions.allTextContents(), titles);
  }
  async function attachNew(filename, content) {
    await page.getByRole('button', { name: '新建分析', exact: true }).click();
    await page.waitForURL(`${base}/chat`);
    await page.getByRole('heading', { name: '从一个岗位开始。', exact: true }).waitFor();
    const chat = await mutation('/chats', 'POST', () => page.getByLabel('上传对话附件', { exact: true }).setInputFiles({
      name: filename, mimeType: 'text/plain', buffer: Buffer.from(content),
    }), 201);
    created.add(chat.id);
    await page.waitForURL(`${base}/chat/${chat.id}`);
    await page.getByText('参考附件', { exact: true }).waitFor();
    await page.getByRole('button', { name: filename, exact: true }).waitFor();
    return chat.id;
  }
  try {
    await page.goto(`${base}/login`);
    await page.getByLabel('用户名', { exact: true }).fill(username);
    await page.getByLabel('密码', { exact: true }).fill(password);
    await mutation('/auth/login', 'POST', () => page.getByRole('button', { name: '登录知面', exact: true }).click());
    await page.getByRole('heading', { name: '简历诊断', exact: true }).waitFor();
    await page.goto(`${base}/chat`);

    const olderId = await attachNew('browser-chat-older.txt', '浏览器附件：负责 PostgreSQL、Redis 与异步任务队列。');
    await rename(olderId, '新对话', '浏览器较早对话');
    await page.getByLabel('消息内容', { exact: true }).fill('如何介绍异步任务队列的技术决策？');
    const [stream] = await Promise.all([
      page.waitForResponse(response => new URL(response.url()).pathname === `/api/chats/${olderId}/messages` && response.request().method() === 'POST'),
      page.getByRole('button', { name: '发送', exact: true }).click(),
    ]);
    assert.equal(stream.status(), 200);
    assert.ok((await stream.text()).includes('event: done'), 'SSE must finish successfully');
    await page.getByRole('button', { name: '发送', exact: true }).waitFor({ timeout: 60000 });
    await assistantReply.waitFor();
    const before = await request(`/chats/${olderId}`);
    assert.deepEqual(before.messages.map(message => message.role), ['system', 'user', 'assistant']);
    assert.ok(before.messages.every(message => message.content.length > 0));

    // Real dialogs, keyboard focus and cancellation must leave the server unchanged.
    await openMenu('浏览器较早对话');
    await menu.getByRole('menuitem', { name: '重命名', exact: true }).waitFor();
    assert.equal(await menu.getByRole('menuitem', { name: '重命名', exact: true }).evaluate(node => node === document.activeElement), true);
    await page.keyboard.press('ArrowDown');
    assert.equal(await menu.getByRole('menuitem', { name: '置顶', exact: true }).evaluate(node => node === document.activeElement), true);
    await page.keyboard.press('Escape');
    await menu.waitFor({ state: 'hidden' });
    assert.equal(await page.getByRole('button', { name: '管理对话：浏览器较早对话', exact: true }).evaluate(node => node === document.activeElement), true);
    await openMenu('浏览器较早对话');
    await menu.getByRole('menuitem', { name: '重命名', exact: true }).click();
    await renameDialog.getByLabel('对话名称', { exact: true }).fill('   ');
    assert.equal(await renameDialog.getByRole('button', { name: '保存', exact: true }).isDisabled(), true);
    await renameDialog.getByRole('button', { name: '取消', exact: true }).click();
    assert.equal((await request(`/chats/${olderId}`)).title, '浏览器较早对话');
    await rename(olderId, '浏览器较早对话', ' 12 ');
    await page.reload();
    await page.getByRole('heading', { name: '12', exact: true }).waitFor();
    await assistantReply.waitFor();
    await page.getByText('查看附件摘录', { exact: true }).click();
    await page.getByText('浏览器附件：负责 PostgreSQL、Redis 与异步任务队列。', { exact: false }).waitFor();
    assert.deepEqual((await request(`/chats/${olderId}`)).messages, before.messages);

    const newerId = await attachNew('browser-chat-newer.txt', '第二份浏览器聊天管理验收附件。');
    await rename(newerId, '新对话', '浏览器较新对话');
    await order(['浏览器较新对话', '12']);
    await openMenu('12');
    const pinned = await mutation(`/chats/${olderId}`, 'PATCH', () => menu.getByRole('menuitem', { name: '置顶', exact: true }).click());
    assert.equal(pinned.is_pinned, true);
    await order(['12', '浏览器较新对话']);
    assert.equal(page.url(), `${base}/chat/${newerId}`, 'Managing an inactive chat must keep the active conversation');
    await page.reload();
    await page.getByRole('heading', { name: '浏览器较新对话', exact: true }).waitFor();
    await order(['12', '浏览器较新对话']);
    assert.equal((await request(`/chats/${olderId}`)).is_pinned, true);
    await page.screenshot({ path: resolve(output, 'chat-management-desktop.png'), fullPage: true });

    await openMenu('12');
    const unpinned = await mutation(`/chats/${olderId}`, 'PATCH', () => menu.getByRole('menuitem', { name: '取消置顶', exact: true }).click());
    assert.equal(unpinned.is_pinned, false);
    await page.reload();
    await page.getByRole('heading', { name: '浏览器较新对话', exact: true }).waitFor();
    await order(['浏览器较新对话', '12']);
    assert.equal((await request(`/chats/${olderId}`)).is_pinned, false);
    assert.deepEqual((await request(`/chats/${olderId}`)).messages, before.messages);

    // Exercise the portal menu and native modal on a narrow viewport too.
    await page.setViewportSize({ width: 390, height: 844 });
    await openMenu('12');
    const box = await menu.boundingBox();
    assert.ok(box && box.x >= 0 && box.y >= 0 && box.x + box.width <= 391 && box.y + box.height <= 845, 'Mobile menu must fit in the viewport');
    await menu.getByRole('menuitem', { name: '删除', exact: true }).click();
    await deleteDialog.waitFor();
    await deleteDialog.getByRole('button', { name: '取消', exact: true }).click();
    assert.equal((await request(`/chats/${olderId}`)).title, '12');
    await order(['浏览器较新对话', '12']);
    await openMenu('12');
    await menu.getByRole('menuitem', { name: '删除', exact: true }).click();
    await deleteDialog.waitFor();
    await page.screenshot({ path: resolve(output, 'chat-management-mobile.png'), fullPage: true });
    await mutation(`/chats/${olderId}`, 'DELETE', () => deleteDialog.getByRole('button', { name: '确认删除', exact: true }).click(), 204);
    created.delete(olderId);
    await deleteDialog.waitFor({ state: 'hidden' });
    await order(['浏览器较新对话']);
    assert.equal(page.url(), `${base}/chat/${newerId}`);
    await request(`/chats/${olderId}`, { status: 404 });

    await openMenu('浏览器较新对话');
    await menu.getByRole('menuitem', { name: '删除', exact: true }).click();
    await mutation(`/chats/${newerId}`, 'DELETE', () => deleteDialog.getByRole('button', { name: '确认删除', exact: true }).click(), 204);
    created.delete(newerId);
    await page.waitForURL(`${base}/chat`);
    await page.getByRole('heading', { name: '从一个岗位开始。', exact: true }).waitFor();
    const [history] = await Promise.all([
      page.waitForResponse(response => new URL(response.url()).pathname === '/api/chats' && response.request().method() === 'GET'),
      page.reload(),
    ]);
    assert.equal(history.status(), 200);
    assert.deepEqual(await history.json(), []);
    await page.getByRole('heading', { name: '从一个岗位开始。', exact: true }).waitFor();
    assert.equal(await sessions.count(), 0);
    await request(`/chats/${newerId}`, { status: 404 });
    assert.deepEqual(await request('/chats'), []);
    await page.screenshot({ path: resolve(output, 'chat-management-deleted.png'), fullPage: true });
    await mutation('/auth/logout', 'POST', () => page.getByRole('button', { name: '打开导航菜单', exact: true }).click().then(() => page.getByRole('button', { name: '退出', exact: true }).click()), 204);
    await page.getByRole('heading', { name: '欢迎回来', exact: true }).waitFor();
    assert.deepEqual(errors, [], `Browser errors: ${errors.join('; ')}`);
  } catch (error) {
    await page.screenshot({ path: resolve(output, 'chat-management-failure.png'), fullPage: true }).catch(() => {});
    throw error;
  } finally {
    try { await context.tracing.stop({ path: resolve(output, 'chat-management-trace.zip') }) }
    finally { await browser.close() }
  }
}

try {
  // This must reach the auth guard. An old deployed backend returns 405 instead.
  await request('/chats/route-probe', { method: 'PATCH', body: { title: 'probe' }, status: 401 });
  user = await request('/auth/register', { method: 'POST', body: { username, password }, status: 201 });
  const older = await request('/chats', { method: 'POST', body: {}, status: 201 }); created.add(older.id);
  const newer = await request('/chats', { method: 'POST', body: {}, status: 201 }); created.add(newer.id);
  const attachment = new FormData();
  attachment.append('file', new Blob(['Conversation management regression fixture.'], { type: 'text/plain' }), 'chat-e2e.txt');
  await request(`/chats/${older.id}/attachments`, { method: 'POST', body: attachment, status: 201 });
  const before = await request(`/chats/${older.id}`);
  assert.equal(before.messages.length, 1);
  const renamed = await request(`/chats/${older.id}`, { method: 'PATCH', body: { title: ' 12 ' } });
  assert.equal(renamed.title, '12');
  assert.deepEqual((await request(`/chats/${older.id}`)).messages, before.messages);
  await request(`/chats/${older.id}`, { method: 'PATCH', body: { is_pinned: true } });
  assert.equal((await request('/chats'))[0].id, older.id);
  assert.equal((await request(`/chats/${older.id}`)).is_pinned, true);
  await request(`/chats/${older.id}`, { method: 'PATCH', body: { is_pinned: false } });
  assert.equal((await request('/chats'))[0].id, newer.id);
  await request(`/chats/${older.id}`, { method: 'PATCH', body: { title: ' ' }, status: 422 });
  assert.equal((await request(`/chats/${older.id}`)).title, '12');
  await request(`/chats/${older.id}`, { method: 'DELETE', status: 204 }); created.delete(older.id);
  await request(`/chats/${older.id}`, { status: 404 });
  await request(`/chats/${newer.id}`, { method: 'DELETE', status: 204 }); created.delete(newer.id);
  await browserAcceptance();
  result = { passed: true, base, userId: user.id, username, screenshots: output,
    apiChecks: ['deployed PATCH route', 'cookie session and CSRF', 'rename persistence', 'attachment preservation', 'pin and unpin ordering', 'blank name rejection', 'delete'],
    browserChecks: ['login', 'create chats and upload attachments', 'SSE reply', 'menu keyboard navigation and Escape focus', 'blank rename disabled and cancel', 'rename and preserve messages after reload', 'pin/unpin ordering after reload', 'inactive chat actions preserve active URL', 'mobile menu and delete cancel', 'inactive and active delete', 'empty history after reload', 'logout', 'no page errors'] };
} catch (error) {
  failure = error;
} finally {
  const cleanup = await Promise.allSettled([...created].map(id => request(`/chats/${id}`, { method: 'DELETE', status: [204, 404] })));
  if (user) cleanup.push(...await Promise.allSettled([request('/auth/logout', { method: 'POST', status: 204 })]));
  const errors = cleanup.filter(result => result.status === 'rejected').map(result => result.reason);
  if (errors.length) {
    if (failure) console.error('Chat fixture cleanup failed:', errors);
    else failure = new AggregateError(errors, 'Chat fixture cleanup failed');
  }
}
await writeFile(resolve(output, 'chat-management.json'), JSON.stringify(failure ? { passed: false, base, error: failure.message } : result, null, 2));
if (failure) throw failure;
console.log(JSON.stringify(result, null, 2));
