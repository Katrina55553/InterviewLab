import { act } from 'react';
import { createRoot } from 'react-dom/client';
import type { Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AISettingsPage } from './AISettings';
import { createUserModelAdapter } from './modelAdapter';
import type { AIConnectionState } from './modelAdapter';

let container: HTMLDivElement;
let root: Root;
let state: AIConnectionState;
let connected: boolean;
const selection = { connectionType: 'builtin', authMethod: 'api-key', providerId: 'deepseek', modelId: 'deepseek-chat' } as const;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) }) }
async function click(label: string) {
  const button = [...container.querySelectorAll('button')].find(item => item.textContent === label);
  if (!button) throw new Error(`Missing ${label}`);
  await act(async () => button.click()); await settle();
}

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  state = { selection: null, version: 0, has_key: false, allowed_base_urls: ['https://api.deepseek.com'] }; connected = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/api/settings/ai' && options?.method === 'PUT') {
      state = { ...state, selection: JSON.parse(options.body as string).selection, version: 1, has_key: true };
      return json(state);
    }
    if (url === '/api/settings/ai') return json(state);
    if (url === '/api/settings/ai/providers') return json([{ id: 'deepseek', name: 'DeepSeek', auth: { apiKey: { label: 'DeepSeek API Key', interactive: true } } }]);
    if (url.startsWith('/api/settings/ai/models')) return json([{ id: 'deepseek-chat', name: 'deepseek-chat', providerId: 'deepseek', input: ['text'], reasoning: false, contextWindow: 8192, maxTokens: 4096 }]);
    if (url === '/api/settings/ai/connect') { connected = true; return json({ providerId: 'deepseek', auth: { state: 'configured', method: 'api-key' } }) }
    if (url.startsWith('/api/settings/ai/auth')) return json({ state: connected ? 'configured' : 'unconfigured', method: 'api-key' });
    if (url === '/api/settings/ai/probe') return json({ state: 'reachable', mayBeBillable: true, message: '真实测试响应' });
    throw new Error(`Unexpected request ${url}`);
  }));
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals() });

it('uses the real kit and keeps authentication, probing and saved selection separate', async () => {
  await act(async () => root.render(<AISettingsPage />)); await settle(); await settle();
  expect(container.textContent).toContain('尚未选择模型');
  expect(container.querySelector('.mck-panel')).not.toBeNull();
  const key = container.querySelector('input[aria-label="API Key"]') as HTMLInputElement;
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(key, 'user-private-test-key'); key.dispatchEvent(new Event('input', { bubbles: true })) });
  await click('连接');
  expect(key.value).toBe('');
  expect(container.textContent).toContain('尚未选择模型');
  await click('测试连接');
  expect(container.textContent).toContain('真实测试响应');
  expect(state.selection).toBeNull();
  await click('保存');
  expect(state.selection).toEqual(selection);
  expect(container.textContent).toContain('模型选择已保存');
  const saves = vi.mocked(fetch).mock.calls.filter(([, options]) => options?.method === 'PUT');
  expect(JSON.parse(saves[0][1]!.body as string)).toEqual({ selection, expected_version: 0 });
  expect(container.innerHTML).not.toContain('user-private-test-key');
});

it('restores a saved selection without requesting or displaying the secret', async () => {
  state = { ...state, selection, version: 2, has_key: true }; connected = true;
  await act(async () => root.render(<AISettingsPage />)); await settle(); await settle();
  expect(container.querySelector('.ai-current-connection')?.textContent).toContain('deepseek-chat');
  expect((container.querySelector('input[aria-label="API Key"]') as HTMLInputElement).value).toBe('');
  expect(vi.mocked(fetch).mock.calls.some(([url]) => url === '/api/settings/ai/probe')).toBe(false);
});

it('does not advertise an unimplemented subscription authentication flow', async () => {
  await expect(createUserModelAdapter().connect({ connectionType: 'builtin', providerId: 'openai-codex', authMethod: 'oauth' })).rejects.toThrow('订阅登录暂未接入');
  expect(fetch).not.toHaveBeenCalled();
});
