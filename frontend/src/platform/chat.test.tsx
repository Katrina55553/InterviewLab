import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import App from '../App';
import { useAuth } from './auth';

afterEach(() => { vi.unstubAllGlobals() });

async function chatWorkspace(path: string, handler: (url: string, options?: RequestInit) => Promise<Response>) {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  Element.prototype.scrollIntoView = vi.fn();
  HTMLDialogElement.prototype.showModal = function () { this.open = true };
  HTMLDialogElement.prototype.close = function () { this.open = false };
  window.history.replaceState({}, '', path);
  useAuth.setState({ user: { id: 'u1', username: 'test', role: 'user' }, loading: false, error: '' });
  const fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/api/auth/me') return json({ id: 'u1', username: 'test', role: 'user' });
    if (url === '/api/repository/stats') return json({ stars: 4, forks: 0, source: 'fallback', updated_at: null });
    if (url === '/api/agents') return json([]);
    return handler(url, options);
  });
  vi.stubGlobal('fetch', fetchMock);
  const container = document.createElement('div'); document.body.appendChild(container);
  const root = createRoot(container);
  const settle = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) });
  await act(async () => root.render(<App />)); await settle();
  return {
    container, fetchMock, settle,
    input: () => container.querySelector('#chat-message') as HTMLTextAreaElement,
    fill: async (text: string) => act(async () => {
      const input = container.querySelector('#chat-message')!;
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, text);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }),
    click: async (text: string) => {
      const button = [...document.querySelectorAll('button')].find(element => element.textContent === text || element.getAttribute('aria-label') === text)!;
      await act(async () => button.click()); await settle();
    },
    dispose: async () => { await act(async () => root.unmount()); container.remove() },
  };
}

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
const reply = (text: string) => new Response(`event: delta\ndata: ${JSON.stringify({ content: text })}\n\nevent: done\ndata: {}\n\n`);

const renameInput = async (container: HTMLElement, title: string) => act(async () => {
  const input = container.querySelector('dialog input')!;
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, title);
  input.dispatchEvent(new Event('input', { bubbles: true }));
});

it('manages titles, pin order and deletion without losing another conversation draft', async () => {
  const records = [
    { id: 'older', title: '岗位 A', is_pinned: false, created_at: '2026-10-24T12:00:00', messages: [] },
    { id: 'newer', title: '岗位 B', is_pinned: false, created_at: '2026-10-25T12:00:00', messages: [] },
  ];
  let deletions = 0;
  const workspace = await chatWorkspace('/chat/older', async (url, options) => {
    if (url === '/api/chats') return json(records);
    const record = records.find(chat => `/api/chats/${chat.id}` === url);
    if (!record) throw new Error(`Unexpected request: ${url}`);
    if (options?.method === 'PATCH') {
      Object.assign(record, JSON.parse(options.body as string));
      return json({ id: record.id, title: record.title, is_pinned: record.is_pinned, created_at: record.created_at });
    }
    if (options?.method === 'DELETE') { deletions++; return new Response(null, { status: 204 }) }
    return json(record);
  });
  const order = () => [...workspace.container.querySelectorAll('.chat-session-link')].map(node => node.textContent);
  try {
    await workspace.fill('保留当前草稿');
    expect(order()).toEqual(['岗位 B', '岗位 A']);
    await workspace.click('管理对话：岗位 B');
    expect([...document.querySelectorAll('[role=menuitem]')].map(node => node.textContent)).toEqual(['重命名', '置顶', '删除']);
    await workspace.click('重命名'); await renameInput(workspace.container, '  新岗位 B  '); await workspace.click('保存');
    expect(workspace.container.querySelector('.chat-history-dialog')).toBeNull();
    expect(order()).toEqual(['新岗位 B', '岗位 A']);
    expect(workspace.container.querySelector('.conversation-title h3')?.textContent).toBe('岗位 A');
    expect(workspace.input().value).toBe('保留当前草稿');
    await workspace.click('管理对话：岗位 A'); await workspace.click('重命名');
    await renameInput(workspace.container, '岗位 A 复盘'); await workspace.click('保存');
    expect(workspace.container.querySelector('.conversation-title h3')?.textContent).toBe('岗位 A 复盘');
    await workspace.click('管理对话：岗位 A 复盘'); await workspace.click('置顶');
    expect(order()).toEqual(['岗位 A 复盘', '新岗位 B']);
    await workspace.click('管理对话：岗位 A 复盘'); await workspace.click('取消置顶');
    expect(order()).toEqual(['新岗位 B', '岗位 A 复盘']);
    await workspace.click('管理对话：新岗位 B'); await workspace.click('删除'); await workspace.click('取消');
    expect(deletions).toBe(0);
    await workspace.click('管理对话：新岗位 B'); await workspace.click('删除'); await workspace.click('确认删除');
    expect(deletions).toBe(1);
    expect(order()).toEqual(['岗位 A 复盘']);
    expect(window.location.pathname).toBe('/chat/older');
    expect(workspace.input().value).toBe('保留当前草稿');
    await workspace.click('管理对话：岗位 A 复盘'); await workspace.click('删除'); await workspace.click('确认删除');
    expect(deletions).toBe(2);
    expect(window.location.pathname).toBe('/chat');
    expect(workspace.input().value).toBe('');
    expect(order()).toEqual([]);
  } finally { await workspace.dispose() }
});

it.each(['rename', 'pin', 'delete'])('keeps history and drafts intact when %s fails', async action => {
  const record = { id: 'managed', title: '我的分析', is_pinned: false, messages: [] };
  const workspace = await chatWorkspace('/chat/managed', async (url, options) => {
    if (url === '/api/chats') return json([record]);
    if (options?.method === 'PATCH' || options?.method === 'DELETE') return json({ detail: '管理操作失败' }, 503);
    if (url === '/api/chats/managed') return json(record);
    throw new Error(`Unexpected request: ${url}`);
  });
  try {
    await workspace.fill('草稿不应丢失');
    await workspace.click('管理对话：我的分析');
    if (action === 'rename') {
      await workspace.click('重命名'); await renameInput(workspace.container, '   ');
      expect([...workspace.container.querySelectorAll('button')].find(button => button.textContent === '保存')?.disabled).toBe(true);
      await renameInput(workspace.container, '新名字'); await workspace.click('保存');
      expect(workspace.container.querySelector('dialog input')?.getAttribute('value')).toBe('新名字');
    } else if (action === 'delete') { await workspace.click('删除'); await workspace.click('确认删除') }
    else await workspace.click('置顶');
    expect(workspace.container.querySelector('[role=alert]')?.textContent).toContain('管理操作失败');
    expect(workspace.container.querySelector('.chat-session-link')?.textContent).toBe('我的分析');
    expect(workspace.input().value).toBe('草稿不应丢失');
    expect(window.location.pathname).toBe('/chat/managed');
  } finally { await workspace.dispose() }
});

it('supports keyboard menu navigation, Escape and outside dismissal', async () => {
  const record = { id: 'managed', title: '我的分析', messages: [] };
  const workspace = await chatWorkspace('/chat/managed', async url => json(url === '/api/chats' ? [record] : record));
  try {
    await workspace.click('管理对话：我的分析');
    const menu = document.querySelector('[role=menu]')!;
    expect(document.activeElement?.textContent).toBe('重命名');
    await act(async () => menu.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })));
    expect(document.activeElement?.textContent).toBe('置顶');
    await act(async () => menu.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    expect(document.querySelector('[role=menu]')).toBeNull();
    expect(document.activeElement?.getAttribute('aria-label')).toBe('管理对话：我的分析');
    await workspace.click('管理对话：我的分析');
    await act(async () => document.body.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true })));
    expect(document.querySelector('[role=menu]')).toBeNull();
  } finally { await workspace.dispose() }
});

it('creates one chat on the first send and keeps streaming across the URL change', async () => {
  const chat = { id: 'created-chat', title: '新对话', messages: [] as { id: string; role: string; content: string }[] };
  let creations = 0;
  let sends = 0;
  let stream!: ReadableStreamDefaultController<Uint8Array>;
  let signal: AbortSignal | null | undefined;
  const workspace = await chatWorkspace('/chat', async (url, options) => {
    if (url === '/api/chats') {
      if (options?.method === 'POST') { creations++; return json(chat) }
      return json(creations ? [chat] : []);
    }
    if (url === '/api/chats/created-chat') {
      if (options?.method === 'PATCH') {
        chat.title = JSON.parse(options.body as string).title;
        return json({ id: chat.id, title: chat.title });
      }
      return json(chat);
    }
    if (url === '/api/chats/created-chat/messages') {
      sends++;
      const content = JSON.parse(options!.body as string).content;
      chat.title = '分析这个岗位';
      chat.messages.push({ id: `u${sends}`, role: 'user', content });
      if (sends > 1) { chat.messages.push({ id: 'a2', role: 'assistant', content: '继续分析' }); return reply('继续分析') }
      signal = options?.signal;
      return new Response(new ReadableStream({ start(controller) {
        stream = controller;
        controller.enqueue(new TextEncoder().encode('event: delta\ndata: {"content":"正在分析"}\n\n'));
      } }));
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  try {
    expect(workspace.input().disabled).toBe(false);
    expect(creations).toBe(0);
    await workspace.fill('分析这个岗位');
    await act(async () => {
      for (let index = 0; index < 2; index++) workspace.input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, bubbles: true }));
    });
    await workspace.settle();
    expect(window.location.pathname).toBe('/chat/created-chat');
    expect(creations).toBe(1);
    expect(sends).toBe(1);
    expect(signal?.aborted).toBe(false);
    expect(workspace.container.querySelector('.message-user')?.textContent).toContain('分析这个岗位');
    expect(workspace.container.querySelector('.message-assistant')?.textContent).toContain('正在分析');
    await workspace.click('管理对话：新对话');
    expect(document.querySelector<HTMLButtonElement>('.chat-history-delete')?.disabled).toBe(true);
    await workspace.click('重命名'); await renameInput(workspace.container, '进行中的分析'); await workspace.click('保存');
    expect(workspace.container.querySelector('.conversation-title h3')?.textContent).toBe('进行中的分析');
    expect(signal?.aborted).toBe(false);
    expect(workspace.container.querySelector('.message-assistant')?.textContent).toContain('正在分析');
    await act(async () => {
      chat.messages.push({ id: 'a1', role: 'assistant', content: '正在分析，完成。' });
      stream.enqueue(new TextEncoder().encode('event: delta\ndata: {"content":"，完成。"}\n\nevent: done\ndata: {}\n\n'));
      stream.close();
    });
    await workspace.settle();
    expect(workspace.container.querySelectorAll('.message-assistant')).toHaveLength(1);
    expect(workspace.container.querySelector('.message-assistant')?.textContent).toContain('正在分析，完成。');
    await workspace.fill('继续'); await workspace.click('发送');
    expect(creations).toBe(1);
    expect(sends).toBe(2);
    await workspace.click('新建分析');
    expect(window.location.pathname).toBe('/chat');
    expect(workspace.input().disabled).toBe(false);
    expect(workspace.input().value).toBe('');
    expect(workspace.container.querySelectorAll('.message')).toHaveLength(0);
    expect(creations).toBe(1);
  } finally { await workspace.dispose() }
});

it.each(['create', 'send'])('preserves the first message when %s fails and supports an explicit retry', async failure => {
  let creations = 0;
  let sends = 0;
  let failed = false;
  const chat = { id: 'retry-chat', title: '新对话', messages: [] as { id: string; role: string; content: string }[] };
  const workspace = await chatWorkspace('/chat', async (url, options) => {
    if (url === '/api/chats') {
      if (options?.method !== 'POST') return json(creations ? [chat] : []);
      creations++;
      if (failure === 'create' && !failed) { failed = true; return json({ detail: '暂时不可用' }, 503) }
      return json(chat);
    }
    if (url === '/api/chats/retry-chat') return json(chat);
    if (url === '/api/chats/retry-chat/messages') {
      sends++;
      if (failure === 'send' && !failed) { failed = true; return json({ detail: '暂时不可用' }, 503) }
      chat.messages = [{ id: 'u1', role: 'user', content: '请帮我看 JD' }, { id: 'a1', role: 'assistant', content: '收到' }];
      return reply('收到');
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  try {
    await workspace.fill('请帮我看 JD'); await workspace.click('发送');
    expect(workspace.input().value).toBe('请帮我看 JD');
    expect(workspace.container.querySelector('[role=alert]')?.textContent).toContain('暂时不可用');
    await workspace.click('发送');
    expect(window.location.pathname).toBe('/chat/retry-chat');
    expect(creations).toBe(failure === 'create' ? 2 : 1);
    expect(sends).toBe(failure === 'send' ? 2 : 1);
    expect(workspace.container.querySelectorAll('.message-user')).toHaveLength(1);
    expect(workspace.container.querySelector('.message-assistant')?.textContent).toContain('收到');
  } finally { await workspace.dispose() }
});

it('keeps an unsent message when stopping during automatic creation', async () => {
  let finishCreation!: (response: Response) => void;
  const workspace = await chatWorkspace('/chat', async (url, options) => {
    if (url === '/api/chats' && options?.method === 'POST') return new Promise<Response>(resolve => { finishCreation = resolve });
    if (url === '/api/chats') return json([]);
    throw new Error(`Unexpected request: ${url}`);
  });
  try {
    await workspace.fill('保留这条草稿'); await workspace.click('发送');
    await workspace.click('停止接收');
    await act(async () => finishCreation(json({ id: 'stopped-chat', title: '新对话' })));
    await workspace.settle();
    expect(workspace.input().value).toBe('保留这条草稿');
    expect(window.location.pathname).toBe('/chat');
    expect(workspace.fetchMock.mock.calls.some(([url]) => url.endsWith('/messages'))).toBe(false);
  } finally { await workspace.dispose() }
});

it('creates a chat for a first attachment without losing the unsent draft', async () => {
  let creations = 0;
  const chat = { id: 'attachment-chat', title: '新对话', messages: [] as { id: string; role: string; content: string }[] };
  const workspace = await chatWorkspace('/chat', async (url, options) => {
    if (url === '/api/chats') {
      if (options?.method === 'POST') { creations++; return json(chat) }
      return json(creations ? [chat] : []);
    }
    if (url === '/api/chats/attachment-chat') return json(chat);
    if (url === '/api/chats/attachment-chat/attachments') {
      expect(options?.body).toBeInstanceOf(FormData);
      chat.messages = [{ id: 's1', role: 'system', content: '附件：resume.txt\n简历内容' }];
      return json({ filename: 'resume.txt', text_excerpt: '简历内容' });
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  try {
    await workspace.fill('请分析附件中的经历');
    const upload = workspace.container.querySelector('input[type=file]')!;
    Object.defineProperty(upload, 'files', { value: [new File(['简历内容'], 'resume.txt', { type: 'text/plain' })] });
    await act(async () => upload.dispatchEvent(new Event('change', { bubbles: true }))); await workspace.settle();
    expect(creations).toBe(1);
    expect(window.location.pathname).toBe('/chat/attachment-chat');
    expect(workspace.input().value).toBe('请分析附件中的经历');
    expect(workspace.container.textContent).toContain('resume.txt');
    expect(workspace.container.querySelector('details')?.textContent).toContain('简历内容');
  } finally { await workspace.dispose() }
});

it('aborts an active reply when opening history and clears the previous draft', async () => {
  let signal: AbortSignal | null | undefined;
  const workspace = await chatWorkspace('/chat', async (url, options) => {
    if (url === '/api/chats') return json(options?.method === 'POST' ? { id: 'new-chat', title: '新对话' } : [{ id: 'old-chat', title: '历史分析' }]);
    if (url === '/api/chats/old-chat') return json({ id: 'old-chat', title: '历史分析', messages: [{ id: 'old1', role: 'assistant', content: '历史回复' }] });
    if (url === '/api/chats/new-chat/messages') {
      signal = options?.signal;
      return new Response(new ReadableStream({ start(controller) {
        controller.enqueue(new TextEncoder().encode('event: delta\ndata: {"content":"新回复"}\n\n'));
        signal?.addEventListener('abort', () => controller.error(new DOMException('Aborted', 'AbortError')));
      } }));
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  try {
    await workspace.fill('新问题'); await workspace.click('发送');
    await workspace.click('历史分析');
    expect(signal?.aborted).toBe(true);
    expect(window.location.pathname).toBe('/chat/old-chat');
    expect(workspace.container.querySelectorAll('.message')).toHaveLength(1);
    expect(workspace.container.querySelector('.message')?.textContent).toContain('历史回复');
    expect(workspace.input().value).toBe('');
    expect(workspace.input().disabled).toBe(false);
  } finally { await workspace.dispose() }
});

it('keeps exactly one persisted partial reply after stopping a stream and refreshing', async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  Element.prototype.scrollIntoView = vi.fn();
  window.history.replaceState({}, '', '/chat/chat-1');
  useAuth.setState({ user: { id: 'u1', username: 'test', role: 'user' }, loading: false, error: '' });
  let stopped = false;
  const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/api/auth/me') return json({ id: 'u1', username: 'test', role: 'user' });
    if (url === '/api/repository/stats') return json({ stars: 4, forks: 0, source: 'fallback', updated_at: null });
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
