import { act } from 'react';
import { createRoot } from 'react-dom/client';
import type { Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import App from '../App';
import { AuthPage } from './AuthPage';
import { SecurityPage } from './Security';
import { useAuth } from './auth';

let container: HTMLDivElement;
let root: Root;
let emailEnabled: boolean;
let boundEmail: string | null;
let failAction: string;
const user = { id: 'u-1', username: '测试用户', role: 'user' as const };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const calls = (path: string) => vi.mocked(fetch).mock.calls.filter(([url, options]) => url === `/api${path}` && options?.method === 'POST');
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) }) }
async function render(path = '/login') {
  await act(async () => root.render(<MemoryRouter initialEntries={[path]}><Routes><Route path="/login" element={<AuthPage />} /><Route path="/settings/security" element={<SecurityPage />} /><Route path="/" element={<div>已登录</div>} /></Routes></MemoryRouter>));
  await settle();
}
async function type(name: string, value: string) {
  const input = container.querySelector<HTMLInputElement>(`input[name="${name}"]`)!;
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })) });
}
function button(text: string) {
  const found = [...container.querySelectorAll('button')].find(element => element.textContent === text || element.textContent === `${text}`);
  if (!found) throw new Error(`Missing button: ${text}`);
  return found;
}
async function click(text: string) { await act(async () => button(text).click()); await settle() }
async function submit(label: string) {
  await act(async () => container.querySelector(`form[aria-label="${label}"]`)!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
  await settle();
}
async function registration() {
  await render(); await click('创建一个');
  await type('username', '测试用户'); await type('email', 'Learner@Example.com'); await type('code', '123456');
  await type('password', 'new-password-123'); await type('confirmation', 'new-password-123');
}

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  emailEnabled = true; boundEmail = null; failAction = '';
  localStorage.clear(); sessionStorage.clear();
  useAuth.setState({ user: null, loading: false, error: '', notice: '' });
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/api/auth/capabilities') return json({ email_enabled: emailEnabled });
    if (url === '/api/auth/security') return json({ email: boundEmail });
    if (url === '/api/auth/me') return json(user);
    if (url === '/api/repository/stats') return json({ stars: 12, forks: 2, source: 'fallback', updated_at: null });
    if (String(url) === failAction) return json({ detail: '验证失败，请重试。' }, 400);
    if (url === '/api/auth/email/code' || url === '/api/auth/email/bind/code') return json({ message: '验证码已发送，请检查邮箱。', expires_in: 600, resend_after: 60 }, 202);
    if (url === '/api/auth/register' || url === '/api/auth/login') return json(user);
    if (url === '/api/auth/email/bind') boundEmail = JSON.parse(options!.body as string).email;
    return new Response(null, { status: 204 });
  }));
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() });

it('logs in with a verified email using the existing username field', async () => {
  await render();
  expect(container.querySelector<HTMLInputElement>('input[name="username"]')!.maxLength).toBe(254);
  expect(container.textContent).toContain('用户名或邮箱');
  await type('username', 'learner@example.com'); await type('password', 'correct-password'); await submit('登录');
  expect(JSON.parse(calls('/auth/login')[0][1]!.body as string)).toEqual({ username: 'learner@example.com', password: 'correct-password' });
  expect(container.textContent).toBe('已登录');
});

it('registers with a normalized email and verification code', async () => {
  await registration(); await submit('注册');
  expect(JSON.parse(calls('/auth/register')[0][1]!.body as string)).toEqual({ username: '测试用户', password: 'new-password-123', email: 'learner@example.com', code: '123456' });
  expect(container.textContent).toBe('已登录');
  expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
});

it('keeps mismatched passwords and ambiguous usernames from reaching registration', async () => {
  await registration(); await type('confirmation', 'different-password'); await submit('注册');
  expect(container.textContent).toContain('两次输入的密码不一致'); expect(calls('/auth/register')).toHaveLength(0);
  await type('confirmation', 'new-password-123'); await type('username', 'user@example.com'); await submit('注册');
  expect(container.textContent).toContain('不能包含 @'); expect(calls('/auth/register')).toHaveLength(0);
});

it('enforces email syntax, clears codes when email changes, and waits before resending', async () => {
  await render(); await click('创建一个');
  await type('email', 'invalid'); expect(button('发送验证码').disabled).toBe(true);
  await type('email', 'first@example.com'); await type('code', '123456');
  vi.useFakeTimers();
  await act(async () => button('发送验证码').click());
  expect(calls('/auth/email/code')).toHaveLength(1);
  expect((container.querySelector('input[name="code"]') as HTMLInputElement).value).toBe('');
  expect(button('60 秒后重发').disabled).toBe(true);
  await act(async () => { await vi.advanceTimersByTimeAsync(60000) });
  expect(button('发送验证码').disabled).toBe(false);
  await type('code', '123456'); await type('email', 'second@example.com');
  expect((container.querySelector('input[name="code"]') as HTMLInputElement).value).toBe('');
});

it('discards stale send results after the email changes', async () => {
  let complete!: (response: Response) => void;
  const fallback = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation((...args) => args[0] === '/api/auth/email/code' ? new Promise(resolve => { complete = resolve }) : fallback(...args));
  await render(); await click('创建一个'); await type('email', 'first@example.com'); await click('发送验证码');
  await type('email', 'second@example.com');
  await act(async () => complete(json({ message: 'first mailbox result', expires_in: 600, resend_after: 60 }, 202)));
  expect(container.textContent).not.toContain('first mailbox result'); expect(button('发送验证码').disabled).toBe(false);
});

it('reports send failures and lets the user retry', async () => {
  failAction = '/api/auth/email/code';
  await render(); await click('忘记密码？'); await type('email', 'learner@example.com'); await click('发送验证码');
  expect(container.textContent).toContain('验证失败，请重试'); expect(button('发送验证码').disabled).toBe(false);
});

it('resets a password using the bound email, without signing in', async () => {
  await render(); await click('忘记密码？');
  await type('email', 'Learner@Example.com'); await type('code', '123456'); await type('password', 'new-password-123'); await type('confirmation', 'new-password-123');
  await submit('找回密码');
  expect(JSON.parse(calls('/auth/password/reset')[0][1]!.body as string)).toEqual({ email: 'learner@example.com', code: '123456', new_password: 'new-password-123' });
  expect(container.textContent).toContain('密码已重置，请使用新密码登录'); expect(useAuth.getState().user).toBeNull();
  expect((container.querySelector('input[name="password"]') as HTMLInputElement).value).toBe('');
});

it('clears password and verification drafts when switching forms', async () => {
  await registration(); await click('去登录');
  expect((container.querySelector('input[name="password"]') as HTMLInputElement).value).toBe('');
  await click('忘记密码？');
  expect((container.querySelector('input[name="email"]') as HTMLInputElement).value).toBe('');
  expect((container.querySelector('input[name="code"]') as HTMLInputElement).value).toBe('');
});

it('disables email registration and reset when SMTP is unavailable while allowing login', async () => {
  emailEnabled = false;
  await render(); expect(button('登录知面').disabled).toBe(false);
  await click('创建一个'); expect(container.textContent).toContain('邮箱服务暂未启用'); expect(button('创建账号').disabled).toBe(true);
  await click('去登录'); await click('忘记密码？'); expect(button('重置密码').disabled).toBe(true);
});

it('shows a retryable service-status error instead of enabling email registration', async () => {
  const fallback = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation((...args) => args[0] === '/api/auth/capabilities' ? Promise.resolve(json({ detail: 'offline' }, 503)) : fallback(...args));
  await render(); await click('创建一个');
  expect(container.textContent).toContain('邮箱服务状态读取失败'); expect(button('创建账号').disabled).toBe(true); expect(button('重新加载')).not.toBeNull();
});

it('validates password changes and preserves the login session after a wrong password', async () => {
  useAuth.setState({ user }); await render('/settings/security');
  await type('current_password', 'old-password-123'); await type('new_password', 'new-password-123'); await type('confirmation', 'different-password'); await submit('修改密码');
  expect(container.textContent).toContain('两次输入的密码不一致'); expect(calls('/auth/password/change')).toHaveLength(0);
  await type('confirmation', 'new-password-123'); failAction = '/api/auth/password/change'; await submit('修改密码');
  expect(container.textContent).toContain('验证失败，请重试'); expect(useAuth.getState().user).toEqual(user);
});

it('returns through the real session gate with a one-time success notice after changing the password', async () => {
  useAuth.setState({ user }); window.history.replaceState({}, '', '/settings/security');
  await act(async () => root.render(<App />)); await settle();
  await type('current_password', 'old-password-123'); await type('new_password', 'new-password-123'); await type('confirmation', 'new-password-123'); await submit('修改密码');
  expect(JSON.parse(calls('/auth/password/change')[0][1]!.body as string)).toEqual({ current_password: 'old-password-123', new_password: 'new-password-123' });
  expect(useAuth.getState().user).toBeNull(); expect(container.textContent).toContain('密码已修改，所有设备已退出');
  expect(container.textContent).toContain('欢迎回来'); expect(window.location.pathname).toBe('/login');
  expect(useAuth.getState().notice).toBe('');
});

it('binds a verified email for a legacy account after verifying the password', async () => {
  useAuth.setState({ user }); await render('/settings/security');
  await type('email', 'Legacy@Example.com'); expect(button('发送验证码').disabled).toBe(true);
  await type('binding_password', 'old-password-123'); await click('发送验证码');
  expect(JSON.parse(calls('/auth/email/bind/code')[0][1]!.body as string)).toEqual({ email: 'legacy@example.com', current_password: 'old-password-123' });
  await type('code', '123456'); await submit('绑定邮箱');
  expect(JSON.parse(calls('/auth/email/bind')[0][1]!.body as string)).toEqual({ email: 'legacy@example.com', code: '123456', current_password: 'old-password-123' });
  expect(container.textContent).toContain('已验证并绑定'); expect(container.textContent).toContain('legacy@example.com'); expect(container.querySelector('input[name="binding_password"]')).toBeNull();
});

it('shows the verified email without an email replacement form', async () => {
  useAuth.setState({ user }); boundEmail = 'learner@example.com'; await render('/settings/security');
  expect(container.textContent).toContain(boundEmail); expect(container.querySelector('form[aria-label="绑定邮箱"]')).toBeNull();
});
