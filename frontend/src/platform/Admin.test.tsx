import { act } from 'react';
import { createRoot } from 'react-dom/client';
import type { Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AdminPage } from './Admin';
import { useAuth } from './auth';
import type { Agent } from './types';
import { selectOption } from '../test/select';

let container: HTMLDivElement;
let root: Root;
let agents: Agent[];
let capabilityError: boolean;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) }) }
async function render() { await act(async () => root.render(<MemoryRouter><AdminPage /></MemoryRouter>)); await settle() }
async function click(label: string) {
  const button = [...container.querySelectorAll('button')].find(item => item.textContent === label);
  if (!button) throw new Error(`Missing ${label}`);
  await act(async () => button.click()); await settle();
}
async function type(selector: string, value: string) {
  const input = container.querySelector<HTMLInputElement | HTMLTextAreaElement>(selector)!;
  const prototype = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
function policy() { return [...container.querySelectorAll('.capability-row .badge')].find(item => item.textContent?.includes('仪态反馈策略'))?.textContent }

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  agents = []; capabilityError = false;
  useAuth.setState({ user: { id: 'admin', username: 'admin', role: 'admin' }, loading: false, error: '' });
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/api/admin/capabilities') {
      if (capabilityError) return json({ detail: '策略读取失败' }, 503);
      return json({ demo_mode: false, asr: true, tts: true, vision: agents[0]?.enabled ?? true,
        scenes: { vision: { enabled: agents[0]?.enabled ?? true, agent_id: agents[0]?.id ?? null } } });
    }
    if (url === '/api/admin/agents' && options?.method === 'POST') {
      const body = JSON.parse(options.body as string);
      agents = [{ ...body, id: 'vision-agent', version: 1 }];
      return json(agents[0], 201);
    }
    if (url === '/api/admin/agents') return json(agents);
    if (url === '/api/admin/agents/vision-agent' && options?.method === 'PUT') {
      agents = [{ ...agents[0], ...JSON.parse(options.body as string), version: agents[0].version + 1 }];
      return json(agents[0]);
    }
    if (url.startsWith('/api/admin/agents/vision-agent?') && options?.method === 'DELETE') {
      agents = [];
      return new Response(null, { status: 204 });
    }
    if (url === '/api/admin/revisions/agent/vision-agent?offset=0') return json({ items: [
      { id: 'revision-1', version: 1, deleted: false, created_at: '2026-10-25T00:00:00Z', key_changed: false, changes: { enabled: { before: null, after: false } } },
    ], has_more: false });
    if (url === '/api/admin/revisions/agent/vision-agent/restore') {
      agents = [{ ...agents[0], enabled: false, version: agents[0].version + 1 }];
      return json(agents[0]);
    }
    throw new Error(`Unexpected request ${url}`);
  }));
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals();
});

it('shows scene management and personal model guidance without loading legacy providers', async () => {
  await render();
  expect(container.querySelector('h1')?.textContent).toBe('场景与智能体管理');
  expect(container.querySelector('a[href="/settings/ai"]')).not.toBeNull();
  expect(container.textContent).toContain('语音转写 · 已配置');
  expect(policy()).toContain('允许使用');
  expect(container.querySelector('[aria-label="使用模型"]')).toBeNull();
  expect(container.querySelector('input[type=password]')).toBeNull();
  expect(vi.mocked(fetch).mock.calls.map(([url]) => url)).toEqual(['/api/admin/agents', '/api/admin/capabilities']);
});

it('creates a scene policy with prompt and tools, then refreshes the capability status', async () => {
  await render();
  await type('input[maxlength="100"]', '仪态助手');
  await selectOption('使用场景', '仪态反馈');
  await type('textarea', '根据画面提供具体建议。');
  await act(async () => container.querySelector<HTMLInputElement>('input[type=checkbox]')!.click());
  await act(async () => [...container.querySelectorAll<HTMLInputElement>('input[type=checkbox]')].at(-1)!.click());
  await click('保存智能体');
  const creation = vi.mocked(fetch).mock.calls.find(([, options]) => options?.method === 'POST');
  expect(JSON.parse(creation![1]!.body as string)).toEqual({ name: '仪态助手', scene: 'vision', prompt: '根据画面提供具体建议。', tools: ['lookup_resume_field'], enabled: false });
  expect(policy()).toContain('已停用');
  expect(container.textContent).toContain('智能体配置已保存。');
});

it('edits with the current version and restores history without sending legacy model bindings', async () => {
  agents = [{ id: 'vision-agent', version: 2, name: '仪态助手', scene: 'vision', prompt: '原提示词', enabled: true, tools: ['lookup_resume_field'], provider_id: 'legacy-provider' }];
  await render();
  await click('编辑');
  await type('textarea', '新的提示词');
  await click('保存智能体');
  const update = vi.mocked(fetch).mock.calls.find(([, options]) => options?.method === 'PUT');
  expect(JSON.parse(update![1]!.body as string)).toEqual({ name: '仪态助手', scene: 'vision', prompt: '新的提示词', tools: ['lookup_resume_field'], enabled: true, expected_version: 2 });
  await click('编辑');
  await click('历史');
  await click('恢复为新版本');
  const restore = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith('/restore'));
  expect(JSON.parse(restore![1]!.body as string)).toEqual({ version: 1, expected_version: 3 });
  expect(policy()).toContain('已停用');
  expect(container.querySelector('h2')?.textContent).toBe('场景智能体');
  expect(container.textContent).toContain('添加智能体');
  expect(container.textContent).not.toContain('配置修订记录');
});

it('deletes the edited agent with its version and refreshes the default policy', async () => {
  agents = [{ id: 'vision-agent', version: 4, name: '仪态助手', scene: 'vision', enabled: false }];
  await render();
  expect(policy()).toContain('已停用');
  await click('编辑');
  await click('删除');
  expect(vi.mocked(fetch).mock.calls.some(([url, options]) => url === '/api/admin/agents/vision-agent?expected_version=4' && options?.method === 'DELETE')).toBe(true);
  expect(policy()).toContain('允许使用');
  expect(container.textContent).toContain('添加智能体');
  expect(container.textContent).toContain('创建你的第一个智能体');
});

it('shows an unknown status when the capability request fails', async () => {
  capabilityError = true;
  await render();
  expect(policy()).toContain('状态未知');
  expect(policy()).not.toContain('已停用');
  expect(container.textContent).toContain('策略读取失败');
});
