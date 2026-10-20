import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import App from '../App';
import { useAuth } from './auth';

afterEach(() => { vi.unstubAllGlobals() });
it('keeps exactly one persisted partial reply after stopping a stream and refreshing', async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  Element.prototype.scrollIntoView = vi.fn();
  window.history.replaceState({}, '', '/chat/chat-1');
  useAuth.setState({ user: { id: 'u1', username: 'test', role: 'user' }, loading: false, error: '' });
  let stopped = false;
  const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/api/auth/me') return json({ id: 'u1', username: 'test', role: 'user' });
    if (url === '/api/chats') return json([{ id: 'chat-1', title: '测试对话' }]);
    if (url === '/api/agents') return json([]);
    if (url === '/api/chats/chat-1') return json({ id: 'chat-1', title: '测试对话', messages: stopped ? [{ id: 'm1', role: 'user', content: '问题' }, { id: 'm2', role: 'assistant', content: '已保存的部分回答' }] : [] });
    if (url === '/api/chats/chat-1/messages') return new Response(new ReadableStream({ start(controller) {
      controller.enqueue(new TextEncoder().encode('event: delta\ndata: {"content":"已保存的部分回答"}\n\n'));
      options?.signal?.addEventListener('abort', () => { stopped = true; controller.error(new DOMException('Aborted', 'AbortError')) });
    } }), { status: 200 });
    throw new Error(`Unexpected request: ${url}`);
  }));
  const container = document.createElement('div'); document.body.appendChild(container);
  const root = createRoot(container);
  const settle = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) });
  const click = async (text: string) => {
    const target = [...container.querySelectorAll('button')].find(element => element.textContent === text)!;
    await act(async () => target.click()); await settle();
  };
  try {
    await act(async () => root.render(<App />)); await settle();
    const input = container.querySelector('#chat-message') as HTMLTextAreaElement;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, '问题'); input.dispatchEvent(new Event('input', { bubbles: true })) });
    await click('发送');
    expect(container.querySelectorAll('.message-assistant')).toHaveLength(1);
    await click('停止接收');
    await click('刷新');
    expect(container.querySelectorAll('.message-assistant')).toHaveLength(1);
    expect(container.textContent?.split('已保存的部分回答')).toHaveLength(2);
  } finally { await act(async () => root.unmount()); container.remove() }
});
