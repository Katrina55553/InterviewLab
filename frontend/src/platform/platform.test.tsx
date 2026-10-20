import { act } from 'react';
import { createRoot } from 'react-dom/client';
import type { Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { useAuth } from './auth';
import type { Interview, Report, Resume } from './types';

let container: HTMLDivElement;
let root: Root;
const report: Report = { overall_score: 84, resume_score: 80, dimensions: { professional: 85, project: 83, analysis: 82, logic: 86, completeness: 84 }, completion: { asked: 1, total: 1, answered: 1, skipped: 0 }, turns: [{ question: '请介绍一个项目。', answer: '我负责队列设计，最终减少延迟。', score: 84, feedback: '回答清晰，建议补充量化结果。' }], suggestions: ['加入具体的数据与结果。'], summary: '项目经历表达清晰。' };
const resume: Resume = { id: 'r-1', filename: '我的简历.txt', status: 'ready', parsed: { skills: ['Python', 'React'] }, preparation: { resume_score: 80, suggestions: ['补充成果数据'], questions: [{ id: 'q1', text: '请介绍一个项目。', category: '项目经验' }] }, created_at: '2026-10-15T10:00:00Z' };
const initial: Interview = { id: 'i-1', resume_id: 'r-1', status: 'draft', version: 0, current_question: null, messages: [], evaluations: [], created_at: '2026-10-15T10:00:00Z' };
let snapshot: Interview;
let commands: Record<string, unknown>[];
let failCommand = false;

async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)) }) }
function button(label: string) { const element = [...container.querySelectorAll('button')].find(item => item.textContent?.includes(label)); if (!element) throw new Error(`Button ${label} not found`); return element }
async function click(label: string) { await act(async () => { button(label).click() }); await settle() }
async function type(selector: string, value: string) {
  const element = container.querySelector(selector) as HTMLInputElement | HTMLTextAreaElement;
  const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  await act(async () => { Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(element, value); element.dispatchEvent(new Event('input', { bubbles: true })) });
}

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  Element.prototype.scrollIntoView = vi.fn();
  sessionStorage.clear(); commands = []; failCommand = false; snapshot = structuredClone(initial);
  window.history.replaceState({}, '', '/');
  useAuth.setState({ user: { id: 'u-1', username: 'demo', role: 'user' }, loading: false, error: '' });
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
    if (url === '/api/auth/me') return response({ id: 'u-1', username: 'demo', role: 'user' });
    if (url === '/api/resumes' && options?.method === 'POST') return response(resume);
    if (url === '/api/resumes') return response([resume]);
    if (url === '/api/resumes/r-1') return response(resume);
    if (url === '/api/interviews' && options?.method === 'POST') return response(snapshot);
    if (url === '/api/interviews') return response([snapshot]);
    if (url === '/api/interviews/i-1') return response(snapshot);
    if (url === '/api/interviews/i-1/report') return response(report);
    if (url.startsWith('/api/media/capabilities')) return response({ asr: false, tts: false, vision: false });
    if (url === '/api/interviews/i-1/commands') {
      const command = JSON.parse(options?.body as string); commands.push(command);
      if (failCommand) throw new TypeError('network disconnected');
      snapshot = command.type === 'start' ? { ...snapshot, version: snapshot.version + 1, status: 'active', current_question: { id: 'q1', text: '请介绍一个项目。' }, messages: [{ id: 'm1', role: 'assistant', content: '请介绍一个项目。' }] } : { ...snapshot, version: snapshot.version + 1, status: 'completed', pending_command: null, report, current_question: null, messages: [...snapshot.messages, { id: 'm2', role: 'user', content: command.content }] };
      return response(snapshot);
    }
    throw new Error(`Unexpected request ${url}`);
  }));
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals() });

describe('platform user flows', () => {
  it('uploads a resume, previews real preparation, answers the current question and opens the report', async () => {
    await act(async () => root.render(<App />)); await settle();
    expect(container.textContent).toContain('开启新的练习');
    const input = container.querySelector('input[type=file]') as HTMLInputElement;
    Object.defineProperty(input, 'files', { configurable: true, value: [new File(['skills Python React'], '我的简历.txt', { type: 'text/plain' })] });
    await act(async () => input.dispatchEvent(new Event('change', { bubbles: true }))); await settle();
    expect(window.location.pathname).toBe('/resumes/r-1');
    expect(container.textContent).toContain('补充成果数据');
    expect(container.textContent).toContain('请介绍一个项目。');
    await click('进入模拟面试');
    await click('开始模拟面试');
    expect(commands[0]).toMatchObject({ type: 'start', version: 0, expected_question_id: null });
    await type('#interview-answer', '我负责队列设计，最终减少延迟。');
    await click('提交回答');
    expect(commands[1]).toMatchObject({ type: 'answer', version: 1, expected_question_id: 'q1', content: '我负责队列设计，最终减少延迟。' });
    expect(commands[0].request_id).not.toBe(commands[1].request_id);
    const reportLink = container.querySelector('a[href="/interviews/i-1/report"]') as HTMLAnchorElement;
    await act(async () => reportLink.click()); await settle();
    expect(container.textContent).toContain('面试综合得分');
    expect(container.textContent).toContain('回答清晰，建议补充量化结果。');
    expect(container.querySelector('svg[aria-label*="专业能力 85"]')).not.toBeNull();
  });
  it('preserves an unknown command after a network failure and never blindly resends it', async () => {
    window.history.replaceState({}, '', '/interviews/i-1'); failCommand = true;
    await act(async () => root.render(<App />)); await settle();
    await click('开始模拟面试');
    expect(container.textContent).toContain('请求结果尚未确认');
    expect(commands).toHaveLength(1);
    expect(sessionStorage.getItem('meeting:command:i-1')).toContain(commands[0].request_id);
    await click('刷新状态');
    expect(commands).toHaveLength(1);
    expect(button('正在准备首个问题').disabled).toBe(true);
    await click('确认后恢复草稿');
    failCommand = false;
    await click('开始模拟面试');
    expect(commands).toHaveLength(2);
    expect(commands[1].request_id).not.toBe(commands[0].request_id);
  });
  it('keeps administrative controls unavailable to normal users', async () => {
    window.history.replaceState({}, '', '/admin');
    await act(async () => root.render(<App />)); await settle();
    expect(window.location.pathname).toBe('/');
    expect(container.textContent).not.toContain('管理设置');
    expect(container.textContent).not.toContain('保存模型配置');
  });
  it.each(['unknown', 'failed'])('restores a saved %s answer on explicit user action and uses a new request ID', async status => {
    const saved = { request_id: 'original-request-id', type: 'answer', version: 3, expected_question_id: 'q1', content: '原来的回答内容。' };
    snapshot = { ...initial, status: 'active', version: 3, current_question: { id: 'q1', text: '请介绍一个项目。' }, messages: [{ id: 'm1', role: 'assistant', content: '请介绍一个项目。' }], pending_command: { request_id: saved.request_id, status, content: saved.content, error: '调用未完成' } };
    sessionStorage.setItem('meeting:command:i-1', JSON.stringify(saved));
    window.history.replaceState({}, '', '/interviews/i-1');
    await act(async () => root.render(<App />)); await settle();
    expect(commands).toHaveLength(0);
    await click('恢复回答草稿');
    expect((container.querySelector('#interview-answer') as HTMLTextAreaElement).value).toBe(saved.content);
    await click('提交回答');
    expect(commands).toHaveLength(1);
    expect(commands[0].request_id).not.toBe(saved.request_id);
    expect(commands[0].content).toBe(saved.content);
    expect(sessionStorage.getItem('meeting:command:i-1')).toBeNull();
  });
  it('returns to login when a previously authenticated session expires', async () => {
    await act(async () => root.render(<App />)); await settle();
    await act(async () => window.dispatchEvent(new Event('meeting:session-expired'))); await settle();
    expect(window.location.pathname).toBe('/login');
    expect(container.textContent).toContain('欢迎回来');
  });
});
