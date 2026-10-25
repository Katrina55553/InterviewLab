import { act } from 'react';
import { createRoot } from 'react-dom/client';
import type { Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { useAuth } from './auth';
import type { Interview, Report, Resume } from './types';

let container: HTMLDivElement;
let root: Root;
const report: Report = { overall_score: 84, dimensions: { professional: 85, project: 83, analysis: 82, logic: 86, completeness: 84 }, completion: { asked: 1, total: 1, answered: 1, skipped: 0 }, turns: [{ question: '请介绍一个项目。', answer: '我负责队列设计，最终减少延迟。', score: 84, feedback: '回答清晰，建议补充量化结果。' }], suggestions: ['加入具体的数据与结果。'], summary: '项目经历表达清晰。' };
const resume: Resume = { id: 'r-1', filename: '我的简历.txt', status: 'ready', parsed: { skills: ['Python', 'React'] }, diagnosis: { id: 'd-1', status: 'ready', result: { resume_score: 80, suggestions: ['补充成果数据'] } }, created_at: '2026-10-15T10:00:00Z' };
const initial: Interview = { id: 'i-1', resume_id: 'r-1', status: 'ready', version: 0, current_question: null, messages: [], evaluations: [], created_at: '2026-10-15T10:00:00Z' };
let snapshot: Interview;
let commands: Record<string, unknown>[];
let failCommand = false;
let resumeItems: Resume[];
let resumeDetail: Resume;
const originalMediaDevices = navigator.mediaDevices;
let cameraStop: ReturnType<typeof vi.fn>;
let cameraRequest: ReturnType<typeof vi.fn>;

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
  cameraStop = vi.fn();
  cameraRequest = vi.fn(async () => ({ getTracks: () => [{ stop: cameraStop }] }));
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: cameraRequest } });
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
  resumeItems = [resume]; resumeDetail = structuredClone(resume);
  sessionStorage.clear(); commands = []; failCommand = false; snapshot = structuredClone(initial);
  window.history.replaceState({}, '', '/');
  useAuth.setState({ user: { id: 'u-1', username: 'demo', role: 'user' }, loading: false, error: '' });
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
    if (url === '/api/chats' && options?.method === 'POST') return response({ id: 'c-1', title: '简历改进' });
    if (url === '/api/chats') return response([]);
    if (url === '/api/agents') return response([]);
    if (url === '/api/chats/c-1') return response({ id: 'c-1', title: '简历改进', messages: [] });
    if (url === '/api/auth/me') return response({ id: 'u-1', username: 'demo', role: 'user' });
    if (url === '/api/repository/stats') return response({ stars: 4, forks: 0, source: 'fallback', updated_at: null });
    if (url === '/api/resumes' && options?.method === 'POST') return response(resume);
    if (url === '/api/resumes') return response(resumeItems);
    if (url === '/api/resumes/r-1/retry') { resumeDetail = structuredClone(resume); return response(resumeDetail) }
    if (url === '/api/resumes/r-1/diagnosis') { resumeDetail = { ...resumeDetail, diagnosis: structuredClone(resume.diagnosis) }; return response(resumeDetail) }
    if (url === '/api/resumes/r-1') return response(resumeDetail);
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
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); vi.restoreAllMocks(); Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: originalMediaDevices }) });

describe('platform user flows', () => {
  it('only requests diagnosis after an explicit click and keeps the saved result', async () => {
    resumeDetail = { ...resume, diagnosis: null };
    window.history.replaceState({}, '', '/resumes/r-1');
    await act(async () => root.render(<App />)); await settle();
    expect(container.textContent).toContain('解析后的经历');
    expect(container.textContent).not.toContain('简历评分');
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => url === '/api/resumes/r-1/diagnosis')).toHaveLength(0);
    await click('开始诊断');
    expect(container.textContent).toContain('补充成果数据');
    await act(async () => root.unmount()); root = createRoot(container);
    await act(async () => root.render(<App />)); await settle();
    expect(container.textContent).toContain('补充成果数据');
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => url === '/api/resumes/r-1/diagnosis')).toHaveLength(1);
  });
  it.each(['failed', 'processing'] as const)('allows an interview when diagnosis is %s', async status => {
    resumeDetail = { ...resume, diagnosis: { id: 'd-1', status, error: '诊断暂不可用', result: null } };
    window.history.replaceState({}, '', '/resumes/r-1');
    await act(async () => root.render(<App />)); await settle();
    expect(container.textContent).toContain('解析后的经历');
    if (status === 'failed') expect(button('重试诊断').disabled).toBe(false);
    await act(async () => (container.querySelector('a[href^="/interviews/new?resume="]') as HTMLAnchorElement).click()); await settle();
    expect(button('开始模拟面试').disabled).toBe(false);
    await click('开始模拟面试');
    expect(commands[0].type).toBe('start');
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => url === '/api/resumes/r-1/diagnosis')).toHaveLength(0);
  });
  it('retries only the diagnosis after it fails', async () => {
    resumeDetail = { ...resume, diagnosis: { id: 'd-1', status: 'failed', error: '诊断暂不可用' } };
    window.history.replaceState({}, '', '/resumes/r-1');
    await act(async () => root.render(<App />)); await settle();
    await click('重试诊断');
    expect(container.textContent).toContain('补充成果数据');
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => url === '/api/resumes/r-1/retry')).toHaveLength(0);
  });
  it('starts from parsed facts without diagnosis and shows only interview scores in the report', async () => {
    resumeDetail = { ...resume, diagnosis: null };
    window.history.replaceState({}, '', '/interviews/new?resume=r-1');
    await act(async () => root.render(<App />)); await settle();
    await click('开始模拟面试');
    await type('#interview-answer', '我负责队列设计，最终减少延迟。'); await click('提交回答');
    await act(async () => (container.querySelector('a[href="/interviews/i-1/report"]') as HTMLAnchorElement).click()); await settle();
    expect(container.textContent).toContain('面试综合得分');
    expect(container.textContent).not.toContain('简历评分');
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => url === '/api/resumes/r-1/diagnosis')).toHaveLength(0);
  });
  it('opens a discussion with the diagnosed resume without creating an interview', async () => {
    window.history.replaceState({}, '', '/resumes/r-1');
    await act(async () => root.render(<App />)); await settle();
    await click('和 AI 讨论改进建议');
    expect(window.location.pathname).toBe('/chat/c-1');
    const creation = vi.mocked(fetch).mock.calls.find(([url, options]) => url === '/api/chats' && options?.method === 'POST');
    expect(JSON.parse(creation?.[1]?.body as string)).toEqual({ resume_id: 'r-1' });
    expect(commands).toHaveLength(0);
  });
  it('diagnoses a resume independently, then practices with it and opens the report', async () => {
    await act(async () => root.render(<App />)); await settle();
    expect(window.location.pathname).toBe('/resumes');
    expect(container.textContent).not.toContain('工作台');
    expect(cameraRequest).not.toHaveBeenCalled();
    expect(container.textContent).toContain('简历诊断');
    const input = container.querySelector('input[type=file]') as HTMLInputElement;
    Object.defineProperty(input, 'files', { configurable: true, value: [new File(['skills Python React'], '我的简历.txt', { type: 'text/plain' })] });
    await act(async () => input.dispatchEvent(new Event('change', { bubbles: true }))); await settle();
    expect(window.location.pathname).toBe('/resumes/r-1');
    expect(container.textContent).toContain('补充成果数据');
    expect(container.textContent).not.toContain('请介绍一个项目。');
    expect(vi.mocked(fetch).mock.calls.filter(([url, options]) => url === '/api/interviews' && options?.method === 'POST')).toHaveLength(0);
    await act(async () => (container.querySelector('a[href^="/interviews/new?resume="]') as HTMLAnchorElement).click()); await settle();
    expect(window.location.pathname).toBe('/interviews/new');
    await click('开始模拟面试');
    expect(commands[0]).toMatchObject({ type: 'start', version: 0, expected_question_id: null });
    await type('#interview-answer', '我负责队列设计，最终减少延迟。');
    await click('提交回答');
    expect(cameraStop).toHaveBeenCalled();
    expect(commands[1]).toMatchObject({ type: 'answer', version: 1, expected_question_id: 'q1', content: '我负责队列设计，最终减少延迟。' });
    expect(commands[0].request_id).not.toBe(commands[1].request_id);
    const reportLink = container.querySelector('a[href="/interviews/i-1/report"]') as HTMLAnchorElement;
    await act(async () => reportLink.click()); await settle();
    expect(container.textContent).toContain('面试综合得分');
    expect(cameraStop).toHaveBeenCalled();
    expect(container.textContent).toContain('回答清晰，建议补充量化结果。');
    expect(container.querySelector('svg[aria-label*="专业能力 85"]')).not.toBeNull();
  });
  it('enters the room without a resume and uploads there before creating an interview', async () => {
    resumeItems = [];
    await act(async () => root.render(<App />)); await settle();
    await act(async () => (container.querySelector('.app-sidebar a[href="/interviews/new"]') as HTMLAnchorElement).click()); await settle();
    expect(window.location.pathname).toBe('/interviews/new');
    expect(container.textContent).toContain('模拟面试室');
    expect(container.textContent).toContain('面试官已就绪');
    expect(cameraRequest).toHaveBeenCalledTimes(1);
    expect(cameraRequest).toHaveBeenCalledWith({ video: { width: { ideal: 640 }, height: { ideal: 480 } }, audio: false });
    expect(container.querySelector('video')!.className).not.toContain('hidden');
    expect(button('提交回答').disabled).toBe(true);
    expect(vi.mocked(fetch).mock.calls.filter(([url, options]) => url === '/api/interviews' && options?.method === 'POST')).toHaveLength(0);
    const input = container.querySelector('input[type=file]') as HTMLInputElement;
    Object.defineProperty(input, 'files', { configurable: true, value: [new File(['Python'], 'resume.txt', { type: 'text/plain' })] });
    await act(async () => input.dispatchEvent(new Event('change', { bubbles: true }))); await settle();
    expect(window.location.pathname).toBe('/interviews/new');
    expect(window.location.search).toBe('?resume=r-1');
    expect(container.textContent).not.toContain('补充成果数据');
    await click('开始模拟面试');
    expect(window.location.pathname).toBe('/interviews/i-1');
    expect(cameraRequest).toHaveBeenCalledTimes(1);
    expect(cameraStop).not.toHaveBeenCalled();
    expect(commands).toHaveLength(1);
    expect(commands[0].type).toBe('start');
    const create = vi.mocked(fetch).mock.calls.find(([url, options]) => url === '/api/interviews' && options?.method === 'POST');
    expect(JSON.parse(create![1]!.body as string)).toEqual({ resume_id: 'r-1' });
  });
  it('consumes the start action and does not start again when the interview is reopened', async () => {
    window.history.replaceState({}, '', '/interviews/new?resume=r-1');
    await act(async () => root.render(<App />)); await settle();
    await click('开始模拟面试');
    expect(commands).toHaveLength(1);
    expect(window.history.state.usr).toBeNull();
    snapshot = structuredClone(initial);
    await act(async () => root.unmount());
    root = createRoot(container);
    await act(async () => root.render(<App />)); await settle();
    expect(commands).toHaveLength(1);
    expect(button('开始模拟面试').disabled).toBe(false);
  });
  it('continues the interview when camera permission is denied', async () => {
    cameraRequest.mockRejectedValueOnce(new DOMException('摄像头权限被拒绝', 'NotAllowedError'));
    window.history.replaceState({}, '', '/interviews/new?resume=r-1');
    await act(async () => root.render(<App />)); await settle();
    expect(container.textContent).toContain('摄像头权限未获允许');
    expect(button('开始模拟面试').disabled).toBe(false);
    await click('开始模拟面试');
    expect(commands[0].type).toBe('start');
    expect(cameraRequest).toHaveBeenCalledTimes(1);
  });
  it('keeps an explicitly closed camera off when the first question starts', async () => {
    window.history.replaceState({}, '', '/interviews/new?resume=r-1');
    await act(async () => root.render(<App />)); await settle();
    await click('关闭摄像头');
    expect(cameraStop).toHaveBeenCalledTimes(1);
    await click('开始模拟面试');
    expect(cameraRequest).toHaveBeenCalledTimes(1);
    expect(container.querySelector('video')!.className).toContain('hidden');
    expect(commands[0].type).toBe('start');
  });
  it('releases a late camera grant after leaving the room', async () => {
    let grant!: (media: { getTracks: () => { stop: typeof cameraStop }[] }) => void;
    cameraRequest.mockImplementationOnce(() => new Promise(resolve => { grant = resolve }));
    window.history.replaceState({}, '', '/interviews/new');
    await act(async () => root.render(<App />)); await settle();
    await act(async () => (container.querySelector('a[href="/history"]') as HTMLAnchorElement).click()); await settle();
    await act(async () => grant({ getTracks: () => [{ stop: cameraStop }] }));
    expect(cameraStop).toHaveBeenCalledTimes(1);
  });
  it('waits for preparation to finish before allowing an interview', async () => {
    window.history.replaceState({}, '', '/interviews/new?resume=r-1');
    resumeDetail = { ...resume, status: 'processing', diagnosis: null };
    vi.useFakeTimers();
    const visibility = vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
    try {
      await act(async () => root.render(<App />));
      await act(async () => { await vi.advanceTimersByTimeAsync(30) });
      expect(button('开始模拟面试').disabled).toBe(true);
      expect(container.textContent).toContain('正在解析简历中的经历和技能');
      resumeDetail = structuredClone(resume);
      await act(async () => { await vi.advanceTimersByTimeAsync(2500) });
      expect(button('开始模拟面试').disabled).toBe(false);
    } finally { visibility.mockRestore(); vi.useRealTimers() }
  });
  it('allows an explicit preparation retry inside the room', async () => {
    window.history.replaceState({}, '', '/interviews/new?resume=r-1');
    resumeDetail = { ...resume, status: 'failed', error: '供应商暂不可用', diagnosis: null };
    await act(async () => root.render(<App />)); await settle();
    expect(container.textContent).toContain('供应商暂不可用');
    expect(button('开始模拟面试').disabled).toBe(true);
    await click('重新解析');
    expect(button('开始模拟面试').disabled).toBe(false);
    expect(commands).toHaveLength(0);
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
    expect(window.location.pathname).toBe('/resumes');
    expect(container.textContent).not.toContain('场景与智能体管理');
    expect(container.textContent).not.toContain('保存智能体');
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).startsWith('/api/admin/'))).toBe(false);
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
