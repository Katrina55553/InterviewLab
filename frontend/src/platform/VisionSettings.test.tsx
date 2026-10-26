import { act } from 'react';
import { createRoot } from 'react-dom/client';
import type { Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createUserModelAdapter } from './modelAdapter';
import { VisionSettings } from './VisionSettings';
import type { VisionConnectionState } from './VisionSettings';
import { selectOption } from '../test/select';

let container: HTMLDivElement;
let root: Root;
let state: VisionConnectionState;
let reachable: boolean;
let connected: boolean;
const providers = [
  { id: 'openai-api', name: 'OpenAI', auth: { apiKey: { label: 'API Key', interactive: true } } },
  { id: 'openai', name: 'ChatGPT', auth: { oauth: { label: '订阅', isSubscription: true } } },
];
const selection = { connectionType: 'builtin', authMethod: 'api-key', providerId: 'openai-api', modelId: 'image-model' } as const;
const adapter = createUserModelAdapter();
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) }) }
function button(text: string) { return [...container.querySelectorAll('button')].find(item => item.textContent === text)! }
async function click(text: string) { await act(async () => button(text).click()); await settle() }
async function edit(value: string) {
  const input = container.querySelector<HTMLInputElement>('[aria-label="视觉模型名称"]')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
async function toggle() { await act(async () => container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click()) }
async function render() { await act(async () => root.render(<VisionSettings providers={providers} adapter={adapter} />)); await settle(); await settle() }

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  reachable = connected = true;
  state = { selection: null, enabled: false, version: 0, tested_at: null, verified: false };
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/api/settings/ai/vision') {
      if (options?.method === 'PUT') {
        const body = JSON.parse(options.body as string);
        state = { ...state, ...body, version: state.version + 1, verified: body.enabled };
      }
      return json(state);
    }
    if (url.startsWith('/api/settings/ai/auth?')) return json({ state: connected ? 'configured' : 'unconfigured', method: 'api-key' });
    if (url.startsWith('/api/settings/ai/models?')) return json([{ id: 'image-model', name: '图片模型', providerId: 'openai-api', input: ['text'], reasoning: false, contextWindow: 8192, maxTokens: 4096 }]);
    if (url === '/api/settings/ai/vision/probe') return json({ state: reachable ? 'reachable' : 'unreachable', message: reachable ? '图片识别通过' : '图片识别测试未通过', ...(reachable ? { probe_token: 'verified-image-proof' } : {}) });
    throw new Error(`Unexpected request: ${url}`);
  }));
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals() });

it('requires explicit image probing before enabling and saves independently from the text model', async () => {
  await render();
  expect(container.textContent).toContain('点击「开启视觉建议」');
  expect(vi.mocked(fetch).mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false);
  await toggle(); await edit('image-model');
  expect(button('保存视觉设置').disabled).toBe(true);
  await click('测试图片识别');
  expect(container.textContent).toContain('图片识别通过');
  expect(state.enabled).toBe(false);
  await click('保存视觉设置');
  expect(state.selection).toEqual(selection);
  expect(state.enabled).toBe(true);
  const saves = vi.mocked(fetch).mock.calls.filter(([, options]) => options?.method === 'PUT');
  expect(JSON.parse(saves[0][1]!.body as string)).toEqual({ enabled: true, selection, expected_version: 0, probe_token: 'verified-image-proof' });
  expect(vi.mocked(fetch).mock.calls.some(([url]) => url === '/api/settings/ai')).toBe(false);
  expect(container.textContent).toContain('视觉模型已保存');
  expect((container.querySelector('[aria-label="视觉模型名称"]') as HTMLInputElement).value).toBe('image-model');
});

it('invalidates a successful probe when the model or provider changes', async () => {
  await render(); await toggle(); await edit('image-model'); await click('测试图片识别');
  expect(button('保存视觉设置').disabled).toBe(false);
  await edit('different-model');
  expect(button('保存视觉设置').disabled).toBe(true);
  await click('测试图片识别');
  await selectOption('视觉服务商', 'ChatGPT'); await settle();
  expect((container.querySelector('[aria-label="视觉模型名称"]') as HTMLInputElement).value).toBe('');
  expect(button('保存视觉设置').disabled).toBe(true);
  await edit('subscription-image'); await click('测试图片识别');
  const probes = vi.mocked(fetch).mock.calls.filter(([url]) => url === '/api/settings/ai/vision/probe');
  expect(JSON.parse(probes.at(-1)![1]!.body as string)).toEqual({ connectionType: 'builtin', authMethod: 'oauth', providerId: 'openai', modelId: 'subscription-image' });
});

it('keeps image recognition failures visible and does not allow saving them as enabled', async () => {
  reachable = false;
  await render(); await toggle(); await edit('text-only-model'); await click('测试图片识别');
  expect(container.textContent).toContain('图片识别测试未通过');
  expect(button('保存视觉设置').disabled).toBe(true);
  expect(state.enabled).toBe(false);
});

it('can disable a saved visual configuration even after its credentials are disconnected', async () => {
  connected = false;
  state = { ...state, selection, enabled: true, version: 7, verified: false };
  await render();
  expect(container.textContent).toContain('认证已变更或断开');
  expect(button('测试图片识别').disabled).toBe(true);
  await toggle(); await click('保存视觉设置');
  expect(state.enabled).toBe(false);
  const save = vi.mocked(fetch).mock.calls.find(([, options]) => options?.method === 'PUT')!;
  expect(JSON.parse(save[1]!.body as string)).toEqual({ enabled: false, selection, expected_version: 7 });
});

it('preserves settings conflicts as errors and requires a new test before another save', async () => {
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (...args) => args[1]?.method === 'PUT'
    ? json({ message: '视觉模型设置已更新，请刷新后重试' }, 409) : original(...args));
  await render(); await toggle(); await edit('image-model'); await click('测试图片识别'); await click('保存视觉设置');
  expect(container.textContent).toContain('视觉模型设置已更新');
  expect(button('保存视觉设置').disabled).toBe(true);
});
