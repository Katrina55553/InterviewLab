import { act } from 'react';
import { createRoot } from 'react-dom/client';
import type { Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AISettingsPage } from './AISettings';
import { createUserModelAdapter } from './modelAdapter';
import type { AIConnectionState } from './modelAdapter';
import { openSelect, selectOption } from '../test/select';

let container: HTMLDivElement;
let root: Root;
let state: AIConnectionState;
let connected: Set<string>;
let subscriptionConnected: boolean;
let authorizationStatus: string;
let catalogFailure: boolean;
let revocation: 'revoked' | 'unconfirmed';
let modelCatalogs: Record<string, { id: string; name: string }[]>;
const selection = { connectionType: 'builtin', authMethod: 'api-key', providerId: 'deepseek', modelId: 'deepseek-flash' } as const;
const subscriptionSelection = { connectionType: 'builtin', authMethod: 'oauth', providerId: 'openai', modelId: 'account-model' } as const;
const apiProviders = [
  { id: 'deepseek', name: 'DeepSeek', baseUrl: 'https://api.deepseek.com', defaultModel: 'deepseek-flash' },
  { id: 'openai-api', name: 'OpenAI', baseUrl: 'https://api.openai.com/v1' },
  { id: 'openrouter', name: 'OpenRouter', baseUrl: 'https://openrouter.ai/api/v1' },
  { id: 'siliconflow', name: '硅基流动', baseUrl: 'https://api.siliconflow.cn/v1' },
  { id: 'dashscope', name: '阿里云百炼', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1' },
  { id: 'moonshot', name: 'Moonshot（Kimi）', baseUrl: 'https://api.moonshot.cn/v1' },
  { id: 'bigmodel', name: '智谱 AI', baseUrl: 'https://open.bigmodel.cn/api/paas/v4' },
];
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) }) }
async function click(label: string) {
  const button = [...container.querySelectorAll('button')].find(item => item.textContent === label);
  if (!button) throw new Error(`Missing ${label}`);
  await act(async () => {
    if (button.getAttribute('role') === 'tab') button.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0 }));
    button.click();
  }); await settle();
}

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  state = { selection: null, version: 0, has_key: false, allowed_base_urls: ['https://api.deepseek.com'] }; connected = new Set();
  subscriptionConnected = false; authorizationStatus = 'pending';
  revocation = 'revoked';
  catalogFailure = false;
  modelCatalogs = Object.fromEntries(apiProviders.map(provider => [provider.id, provider.id === 'deepseek'
    ? [{ id: 'deepseek-flash', name: 'DeepSeek-V4.1-Flash' }, { id: 'deepseek-v4-pro', name: 'DeepSeek-V4-Pro' }]
    : [{ id: `${provider.id}-model`, name: `${provider.name} 账户模型` }]]));
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/api/settings/ai' && options?.method === 'PUT') {
      state = { ...state, selection: JSON.parse(options.body as string).selection, version: 1, has_key: true };
      return json(state);
    }
    if (url === '/api/settings/ai') return json(state);
    if (url === '/api/settings/ai/vision') return json({ selection: null, enabled: false, verified: false, version: 0, tested_at: null });
    if (url === '/api/settings/ai/providers') return json([...apiProviders.map(provider => ({ ...provider, auth: { apiKey: { label: `${provider.name} API Key`, interactive: true } } })), { id: 'openai', name: 'ChatGPT', auth: { oauth: { label: 'Continue with ChatGPT', isSubscription: true } } }]);
    if (url === '/api/settings/ai/models?provider_id=openai') return json(subscriptionConnected ? [{ id: 'account-model', name: '账户模型', providerId: 'openai', input: ['text'], reasoning: false, contextWindow: 8192, maxTokens: 4096 }] : []);
    if (url.startsWith('/api/settings/ai/models')) {
      const providerId = new URL(url, 'http://test').searchParams.get('provider_id')!;
      if (catalogFailure) return json({ detail: '模型列表读取失败，可手动填写模型名称。' }, 502);
      return json(connected.has(providerId) ? (modelCatalogs[providerId] ?? []).map(model => ({ ...model, providerId, input: ['text'], reasoning: false, contextWindow: 8192, maxTokens: 4096 })) : []);
    }
    if (url === '/api/settings/ai/connect') { const { providerId } = JSON.parse(options!.body as string); connected.add(providerId); return json({ providerId, auth: { state: 'configured', method: 'api-key' } }) }
    if (url === '/api/settings/ai/auth?provider_id=openai') {
      if (options?.method === 'DELETE') {
        subscriptionConnected = false;
        state = { ...state, selection: null, version: 0, has_key: false };
        return json({ state: 'unconfigured', method: 'oauth', revocation, message: revocation === 'revoked'
          ? '已断开连接，并已撤销 OpenAI 授权。'
          : '已断开本站连接，但未能确认 OpenAI 授权已撤销。请到 ChatGPT 设置中断开此应用。' });
      }
      return json({ state: subscriptionConnected ? 'configured' : 'unconfigured', method: 'oauth' });
    }
    if (url.startsWith('/api/settings/ai/auth')) return json({ state: connected.has(new URL(url, 'http://test').searchParams.get('provider_id')!) ? 'configured' : 'unconfigured', method: 'api-key' });
    if (url === '/api/settings/ai/oauth/start') return json({ session_id: 'test-authorization', authorization_url: 'https://auth.openai.com/authorize?state=test', redirect_uri: 'http://127.0.0.1:8000/api/settings/ai/oauth/callback', expires_in: 600 });
    if (url === '/api/settings/ai/oauth/test-authorization/complete') { subscriptionConnected = true; authorizationStatus = 'completed'; return json({ providerId: 'openai', auth: { state: 'configured', method: 'oauth' } }) }
    if (url === '/api/settings/ai/oauth/test-authorization') { if (options?.method === 'DELETE') authorizationStatus = 'cancelled'; return json({ status: authorizationStatus }) }
    if (url === '/api/settings/ai/probe') return json({ state: 'reachable', mayBeBillable: true, message: '真实测试响应' });
    throw new Error(`Unexpected request ${url}`);
  }));
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals() });

it('keeps API Key authentication, probing and saved selection separate', async () => {
  await act(async () => root.render(<AISettingsPage />)); await settle(); await settle();
  expect(container.textContent).toContain('尚未选择模型');
  expect(container.querySelector('.mck-panel')).not.toBeNull();
  expect([...container.querySelectorAll('[role="tab"]')].map(tab => tab.textContent)).toEqual(['ChatGPT 订阅登录', 'API Key']);
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
  state = { ...state, selection, version: 2, has_key: true }; connected.add('deepseek');
  await act(async () => root.render(<AISettingsPage />)); await settle(); await settle();
  expect(container.querySelector('.ai-current-connection')?.textContent).toContain('deepseek-flash');
  expect((container.querySelector('input[aria-label="API Key"]') as HTMLInputElement).value).toBe('');
  expect(vi.mocked(fetch).mock.calls.some(([url]) => url === '/api/settings/ai/probe')).toBe(false);
});

it.each(['deepseek-chat', 'deepseek-reasoner'])('replaces retired %s in the editor and only persists the replacement on save', async legacyModel => {
  state = { ...state, selection: { ...selection, modelId: legacyModel }, version: 2, has_key: true }; connected.add('deepseek');
  await act(async () => root.render(<AISettingsPage />)); await settle(); await settle();
  const model = container.querySelector<HTMLInputElement>('input[aria-label="模型名称"]')!;
  expect(model.value).toBe('DeepSeek-V4.1-Flash');
  expect(state.selection?.modelId).toBe(legacyModel);
  await act(async () => model.focus());
  expect([...container.querySelectorAll('[role="option"]')].map(option => option.textContent)).toEqual(['DeepSeek-V4.1-Flash', 'DeepSeek-V4-Pro']);
  expect(container.querySelector('.app-combobox small')).toBeNull();
  await click('保存'); await settle();
  expect(state.selection).toEqual(selection);
  expect(container.querySelector<HTMLInputElement>('input[aria-label="模型名称"]')?.value).toBe('DeepSeek-V4.1-Flash');
});

it('fills each API Key endpoint and clears a draft key when the provider changes', async () => {
  await act(async () => root.render(<AISettingsPage />)); await settle(); await settle();
  expect(container.textContent).not.toContain('支持的自定义服务地址');
  expect((await openSelect('服务商')).map(option => option.textContent)).toEqual(apiProviders.map(provider => provider.name));
  await act(async () => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })) });
  for (const provider of apiProviders) {
    await selectOption('服务商', provider.name); await settle();
    const address = container.querySelector('input[aria-label="服务地址"]') as HTMLInputElement;
    expect(address.value).toBe(provider.baseUrl); expect(address.readOnly).toBe(true);
  }
  const key = container.querySelector('input[aria-label="API Key"]') as HTMLInputElement;
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(key, 'provider-specific-secret'); key.dispatchEvent(new Event('input', { bubbles: true })) });
  await selectOption('服务商', 'OpenAI'); await settle();
  expect(key.value).toBe('');
  expect((container.querySelector('input[aria-label="模型名称"]') as HTMLInputElement).value).toBe('');
  expect(container.innerHTML).not.toContain('provider-specific-secret');
});

it('saves and restores an OpenAI API Key independently from ChatGPT subscription', async () => {
  await act(async () => root.render(<AISettingsPage />)); await settle(); await settle();
  await selectOption('服务商', 'OpenAI'); await settle();
  const key = container.querySelector('input[aria-label="API Key"]') as HTMLInputElement;
  const model = container.querySelector('input[aria-label="模型名称"]') as HTMLInputElement;
  await act(async () => {
    for (const [input, value] of [[key, 'openai-private-key'], [model, 'my-account-model']] as const) {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  });
  await click('连接'); await click('保存'); await settle();
  expect(state.selection).toEqual({ connectionType: 'builtin', authMethod: 'api-key', providerId: 'openai-api', modelId: 'my-account-model' });
  expect(container.querySelector('.ai-current-connection')?.textContent).toContain('OpenAI · 个人连接');
  expect((container.querySelector('input[aria-label="服务地址"]') as HTMLInputElement).value).toBe('https://api.openai.com/v1');
  expect(container.querySelector('[role="combobox"][aria-label="服务商"]')?.textContent).toBe('OpenAI');
  expect(subscriptionConnected).toBe(false);
  const requests = vi.mocked(fetch).mock.calls.filter(([url]) => url === '/api/settings/ai/connect');
  expect(JSON.parse(requests[0][1]!.body as string)).toEqual({ connectionType: 'builtin', authMethod: 'api-key', providerId: 'openai-api', apiKey: 'openai-private-key' });
  expect(container.innerHTML).not.toContain('openai-private-key');
});

it('loads account models after connecting and saves the selected API value separately from its name', async () => {
  modelCatalogs['openai-api'] = [{ id: 'account-model-one', name: '账户模型' }, { id: 'account-model-two', name: '账户模型' }];
  await act(async () => root.render(<AISettingsPage />)); await settle(); await settle();
  await selectOption('服务商', 'OpenAI'); await settle();
  expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('/models?provider_id=openai-api'))).toBe(false);
  const key = container.querySelector<HTMLInputElement>('input[aria-label="API Key"]')!;
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(key, 'catalog-private-key'); key.dispatchEvent(new Event('input', { bubbles: true })) });
  await click('连接'); await settle();
  const model = container.querySelector<HTMLInputElement>('input[aria-label="模型名称"]')!;
  expect(key.value).toBe(''); expect(model.value).toBe('账户模型');
  expect(state.selection).toBeNull();
  await act(async () => model.focus());
  const choices = [...container.querySelectorAll<HTMLElement>('[role="option"]')];
  expect(choices.map(option => option.textContent)).toEqual(['账户模型', '账户模型']);
  expect(container.querySelector('.app-combobox small')).toBeNull();
  await act(async () => choices[1].click()); await click('保存'); await settle();
  expect(state.selection).toEqual({ connectionType: 'builtin', authMethod: 'api-key', providerId: 'openai-api', modelId: 'account-model-two' });
  expect(container.innerHTML).not.toContain('catalog-private-key');
  catalogFailure = true;
  await click('刷新模型'); await settle();
  expect(container.querySelector<HTMLInputElement>('input[aria-label="模型名称"]')?.value).toBe('账户模型');
  expect(container.textContent).toContain('模型列表读取失败');
});

it('keeps manual model entry and refresh available when the provider catalog fails', async () => {
  connected.add('openai-api'); catalogFailure = true;
  await act(async () => root.render(<AISettingsPage />)); await settle(); await settle();
  await selectOption('服务商', 'OpenAI'); await settle(); await settle();
  expect(container.textContent).toContain('模型列表读取失败');
  const model = container.querySelector<HTMLInputElement>('input[aria-label="模型名称"]')!;
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(model, 'manual-account-model'); model.dispatchEvent(new Event('input', { bubbles: true })) });
  await click('保存'); await settle();
  expect(state.selection?.modelId).toBe('manual-account-model');
  catalogFailure = false;
  await click('刷新模型'); await settle();
  expect(container.querySelector('.ai-model-settings')?.textContent).not.toContain('模型列表读取失败');
  const restored = container.querySelector<HTMLInputElement>('input[aria-label="模型名称"]')!;
  expect(restored.value).toBe('manual-account-model');
  await act(async () => restored.focus());
  expect(container.querySelector('[role="option"]')?.textContent).toBe('OpenAI 账户模型');
});

it('ignores a late account catalog after switching providers', async () => {
  connected.add('openai-api'); connected.add('moonshot');
  const originalFetch = vi.mocked(fetch).getMockImplementation()!;
  let complete!: (response: Response) => void;
  const late = new Promise<Response>(resolve => { complete = resolve });
  vi.mocked(fetch).mockImplementation((...args) => String(args[0]) === '/api/settings/ai/models?provider_id=openai-api' ? late : originalFetch(...args));
  await act(async () => root.render(<AISettingsPage />)); await settle(); await settle();
  await selectOption('服务商', 'OpenAI'); await settle();
  await selectOption('服务商', 'Moonshot（Kimi）'); await settle(); await settle();
  await act(async () => complete(json([{ id: 'private-old-account-model', providerId: 'openai-api', name: '迟到的旧账户模型', input: ['text'], reasoning: false, contextWindow: 8192, maxTokens: 4096 }]))); await settle();
  expect(container.querySelector<HTMLInputElement>('input[aria-label="模型名称"]')?.value).toBe('Moonshot（Kimi） 账户模型');
  expect(container.textContent).not.toContain('迟到的旧账户模型');
  expect(state.selection).toBeNull();
});

it('requires the host authorization UI before starting a subscription login', async () => {
  await expect(createUserModelAdapter().connect({ connectionType: 'builtin', providerId: 'openai', authMethod: 'oauth' })).rejects.toThrow('AI 设置页面');
  expect(fetch).not.toHaveBeenCalled();
});

it('completes subscription authorization, reloads account models and saves separately', async () => {
  await act(async () => root.render(<AISettingsPage />)); await settle(); await settle();
  await click('ChatGPT 订阅登录'); await settle();
  await click('连接');
  expect(container.textContent).toContain('连接你的 ChatGPT 订阅');
  expect(container.querySelector('a[href^="https://auth.openai.com/"]')?.getAttribute('rel')).toContain('noopener');
  expect(subscriptionConnected).toBe(false);
  expect(state.selection).toBeNull();
  const input = container.querySelector('#ai-oauth-callback') as HTMLInputElement;
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'http://127.0.0.1:8000/api/settings/ai/oauth/callback?code=private-code&state=test') });
  await click('完成授权'); await settle();
  expect(input.value).toBe('');
  expect(container.textContent).toContain('ChatGPT 订阅已连接');
  expect(container.textContent).toContain('账户模型');
  expect(state.selection).toBeNull();
  expect(container.innerHTML).not.toContain('private-code');
  await click('保存');
  expect(state.selection).toEqual(subscriptionSelection);
  expect(container.querySelector('.ai-current-connection')?.textContent).toContain('ChatGPT 订阅登录');
});

it('allows cancelling a pending authorization without saving a model', async () => {
  await act(async () => root.render(<AISettingsPage />)); await settle(); await settle();
  await click('ChatGPT 订阅登录'); await settle(); await click('连接');
  await click('取消授权');
  expect(authorizationStatus).toBe('cancelled');
  expect(state.selection).toBeNull();
  expect(container.querySelector('.ai-oauth-authorization')).toBeNull();
});

it('restores a saved subscription in the subscription tab without starting OAuth', async () => {
  state = { ...state, selection: subscriptionSelection, version: 2 }; subscriptionConnected = true;
  await act(async () => root.render(<AISettingsPage />)); await settle(); await settle();
  expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe('ChatGPT 订阅登录');
  expect(container.querySelector('.ai-current-connection')?.textContent).toContain('ChatGPT 计划');
  expect(vi.mocked(fetch).mock.calls.some(([url]) => url === '/api/settings/ai/oauth/start')).toBe(false);
});

it.each(['revoked', 'unconfirmed'] as const)('shows %s revocation after disconnecting a subscription and clearing the saved model', async result => {
  revocation = result;
  state = { ...state, selection: subscriptionSelection, version: 2 }; subscriptionConnected = true;
  await act(async () => root.render(<AISettingsPage />)); await settle(); await settle();
  await click('断开认证'); await settle();
  expect(subscriptionConnected).toBe(false);
  expect(state.selection).toBeNull();
  expect(container.querySelector('.ai-current-connection')?.textContent).toContain('尚未选择模型');
  if (result === 'revoked') {
    expect(container.querySelector('.notice-success')?.textContent).toContain('已撤销 OpenAI 授权');
  } else {
    expect(container.querySelector('.notice-info')?.textContent).toContain('未能确认 OpenAI 授权已撤销');
    expect(container.querySelector('.notice-info')?.textContent).toContain('ChatGPT 设置');
    expect(container.querySelector('.notice-success')).toBeNull();
  }
  await click('连接');
  expect(container.querySelector('.ai-settings-page > .notice-info')).toBeNull();
});
