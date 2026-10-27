import { act } from 'react';
import { createRoot } from 'react-dom/client';
import type { Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CommunityPage } from './Community';
import { useAuth } from './auth';
import type { CommunityPost, CommunityDraft, CommunityComment } from './community-data';
import { selectOption } from '../test/select';

let container: HTMLDivElement;
let root: Root;
let posts: CommunityPost[];
let draft: { draft: CommunityDraft | null; version: number };
let comments: CommunityComment[];
let liked: boolean;
let failRead: boolean;
let failWrite: boolean;
const firstId = '11111111-1111-4111-8111-111111111111';
const calls: { path: string; method: string; body?: Record<string, unknown> }[] = [];
const cards = () => [...container.querySelectorAll('.community-post')];
const button = (label: string) => {
  const item = [...container.querySelectorAll('button')].find(element => element.textContent?.trim() === label || element.getAttribute('aria-label') === label);
  if (!item) throw new Error(`Missing button: ${label}`);
  return item;
};
const click = (label: string) => act(async () => { button(label).click() });
const input = (selector: string, value: string) => act(async () => {
  const element = container.querySelector<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(selector)!;
  const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : element instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(element, value);
  element.dispatchEvent(new Event(element instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
});
const mount = (path = '/community') => act(async () => { root.render(<MemoryRouter initialEntries={[path]}><CommunityPage /></MemoryRouter>) });
const submit = (selector: string) => act(async () => { container.querySelector<HTMLFormElement>(selector)!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })) });
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  Element.prototype.scrollIntoView = vi.fn();
  HTMLDialogElement.prototype.showModal = function () { this.open = true };
  HTMLDialogElement.prototype.close = function () { this.open = false };
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  useAuth.setState({ user: { id: 'user-a', username: '测试同学', role: 'user' }, loading: false });
  calls.length = 0; failRead = false; failWrite = false; draft = { draft: null, version: 0 }; comments = []; liked = false;
  posts = Array.from({ length: 7 }, (_, index) => ({ id: index === 0 ? firstId : `11111111-1111-4111-8111-${String(index).padStart(12, '0')}`, authorId: 'author', author: '分享者', title: `后端面试复盘 ${index}`, company: index < 2 ? '腾讯' : '测试公司', role: '后端工程师', direction: '后端开发', stage: '校招 · 一面', outcome: '持续复盘', tags: index < 2 ? ['Java'] : ['Python'], summary: '一段来自服务端的面经摘要', content: '## 面试背景\n这次面试围绕系统设计展开，需要补充具体的指标和验证过程。', createdAt: '2026-10-24T06:00:00Z', likes: 0, liked: false, commentCount: 0, featured: false }));
  vi.stubGlobal('fetch', vi.fn(async (path: string, options?: RequestInit) => {
    const url = new URL(path, 'http://test'); const method = options?.method ?? 'GET';
    const body = options?.body ? JSON.parse(String(options.body)) : undefined;
    calls.push({ path, method, body });
    if ((method === 'GET' && failRead) || (method !== 'GET' && failWrite)) return json({ detail: '服务暂时不可用' }, 503);
    const user = useAuth.getState().user!;
    const view = (post: CommunityPost) => ({ ...post, liked: user.id === 'user-a' && post.id === firstId && liked, likes: post.id === firstId && liked ? 1 : 0, commentCount: comments.length });
    if (url.pathname === '/api/community/meta') return json({ companies: ['腾讯', '测试公司'], topics: [{ tag: 'Java', count: 2 }], myPosts: posts.filter(post => post.authorId === user.id).length, myLikes: liked ? 1 : 0 });
    if (url.pathname === '/api/community/draft') {
      if (method === 'PUT') { const { version, ...data } = body; draft = { draft: data, version: version + 1 } }
      return json(user.id === 'user-a' ? draft : { draft: null, version: 0 });
    }
    if (url.pathname === '/api/community/posts' && method === 'POST') {
      const created = { ...body, authorId: user.id, author: user.username, summary: body.content, likes: 0, liked: false, commentCount: 0, featured: false, createdAt: '2026-10-24T07:00:00Z' } as CommunityPost;
      posts.unshift(created); draft = { draft: null, version: 0 }; return json(created, 201);
    }
    if (url.pathname === '/api/community/posts') {
      const filtered = posts.filter(post => (!url.searchParams.get('q') || [post.title, ...post.tags].join(' ').includes(url.searchParams.get('q')!)) && (!url.searchParams.get('company') || post.company === url.searchParams.get('company')) && (!url.searchParams.get('direction') || post.direction === url.searchParams.get('direction')) && (!url.searchParams.get('topic') || post.tags.includes(url.searchParams.get('topic')!)) && (url.searchParams.get('tab') !== 'liked' || (view(post).liked)) && (url.searchParams.get('tab') !== 'mine' || post.authorId === user.id));
      const page = Math.min(Number(url.searchParams.get('page') ?? 1), Math.max(1, Math.ceil(filtered.length / 5)));
      return json({ items: filtered.slice((page - 1) * 5, page * 5).map(view), total: filtered.length, page, pageSize: 5 });
    }
    if (url.pathname === '/api/community/reports') return json({ ...body, status: 'pending' }, 201);
    const id = url.pathname.split('/')[4]; const post = posts.find(item => item.id === id);
    if (!post) return json({ detail: '面经不存在' }, 404);
    if (url.pathname.endsWith('/like')) { liked = method === 'PUT'; return json({ liked, likes: Number(liked) }) }
    if (url.pathname.endsWith('/comments')) {
      if (method === 'POST') { const item = { ...body, authorId: user.id, author: user.username, createdAt: '2026-10-24T08:00:00Z' }; comments.push(item); return json(item, 201) }
      return json({ items: comments, total: comments.length, page: 1, hasMore: false });
    }
    if (method === 'PUT' || method === 'DELETE') {
      if (post.authorId !== user.id) return json({ detail: '只能编辑或删除自己发布的面经' }, 403);
      if (method === 'PUT') {
        Object.assign(post, body, { summary: body.content });
        return json(view(post));
      }
      posts = posts.filter(item => item.id !== post.id);
      if (post.id === firstId) { liked = false; comments = [] }
      return new Response(null, { status: 204 });
    }
    return json(view(post));
  }));
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals() });

describe('server-backed community', () => {
  it('reports a post with a stable request id after failure and links to account history', async () => {
    await mount(); await click('举报面经：后端面试复盘 0');
    await selectOption('举报原因', '泄露个人隐私');
    await input('#community-report-details', '正文包含电话号码，请核实。');
    failWrite = true; await submit('.community-report-form');
    expect(container.querySelector('.community-report-dialog [role="alert"]')!.textContent).toContain('服务暂时不可用');
    expect(container.querySelector<HTMLTextAreaElement>('#community-report-details')!.value).toBe('正文包含电话号码，请核实。');
    failWrite = false; await submit('.community-report-form');
    const requests = calls.filter(call => call.method === 'POST' && call.path === '/api/community/reports');
    expect(requests).toHaveLength(2); expect(requests[1].body).toEqual(requests[0].body);
    expect(requests[1].body).toMatchObject({ targetType: 'post', targetId: firstId, reason: 'privacy', details: '正文包含电话号码，请核实。' });
    expect(container.querySelector('.community-report-dialog')).toBeNull();
    expect(container.textContent).toContain('举报已提交');
    expect(container.querySelector('a[href="/community/reports"]')!.textContent).toBe('我的举报');
  });
  it('reports comments from the reader and closes only the report dialog when cancelled', async () => {
    comments = [{ id: 'comment-1', authorId: 'reader', author: '读者', content: '需要核实的评论', createdAt: '2026-10-24T08:00:00Z' }];
    await mount(`/community?post=${firstId}`); await click('举报评论：读者');
    expect(container.querySelectorAll('dialog[open]')).toHaveLength(2);
    await click('取消'); expect(container.querySelector('.community-reader[open]')).not.toBeNull();
    await click('举报评论：读者'); await selectOption('举报原因', '人身攻击或骚扰'); await submit('.community-report-form');
    expect(calls.find(call => call.path === '/api/community/reports')?.body).toMatchObject({ targetType: 'comment', targetId: 'comment-1', reason: 'abuse', details: '' });
    expect(container.querySelector('.community-reader[open]')).not.toBeNull();
    expect(container.querySelector('.community-reader [role="status"]')!.textContent).toContain('举报已提交');
  });
  it('requests server pagination, search and exact topic filters', async () => {
    await mount(); expect(cards()).toHaveLength(5);
    await click('下一页'); expect(cards()).toHaveLength(2);
    await input('[aria-label="搜索面经"]', 'Java'); expect(cards()).toHaveLength(2);
    expect(calls.some(call => call.path.includes('q=Java') && call.path.includes('page=1'))).toBe(true);
    await click('前端开发'); expect(cards()).toHaveLength(0);
    expect(container.textContent).toContain('暂时没有找到相关面经');
    await click('清除筛选'); await selectOption('筛选公司', '腾讯'); expect(cards()).toHaveLength(2);
    await act(async () => container.querySelector<HTMLButtonElement>('.community-topics button')!.click());
    expect(calls.some(call => call.path.includes('topic=Java'))).toBe(true);
  });
  it('has a single like action and shows the account liked feed', async () => {
    await mount(); expect(container.textContent).not.toContain('收藏');
    await click('点赞：后端面试复盘 0');
    expect(calls.some(call => call.method === 'PUT' && call.path.endsWith('/like'))).toBe(true);
    await click('我的点赞'); expect(cards()).toHaveLength(1);
    await click('取消点赞：后端面试复盘 0'); expect(cards()).toHaveLength(0);
    expect(calls.some(call => call.method === 'DELETE' && call.path.endsWith('/like'))).toBe(true);
  });
  it('reads detail and shared comments and renders comment text safely', async () => {
    await mount(`/community?post=${firstId}`);
    expect(container.querySelector('.community-reader .rich-text h2')!.textContent).toBe('面试背景');
    await input('#community-comment', '<script>example</script> 补充一个排查思路'); await submit('.community-comments form');
    expect(container.querySelector('.community-comments article')!.textContent).toContain('<script>example</script>');
    expect(container.querySelector('.community-comments script')).toBeNull();
    expect(calls.some(call => call.method === 'POST' && call.path.endsWith('/comments'))).toBe(true);
    await click('关闭弹窗'); expect(container.querySelector('dialog')).toBeNull();
  });
  it('saves drafts to the account and publishes through the API', async () => {
    await mount(); await click('发布面经'); await input('.community-editor input', '我的面试复盘');
    await click('暂存关闭'); expect(draft.draft?.title).toBe('我的面试复盘');
    await click('发布面经'); expect(container.querySelector<HTMLInputElement>('.community-editor input')!.value).toBe('我的面试复盘');
    await input('.community-form-grid input', '一家新公司'); await input('.community-form-grid label:nth-child(2) input', '后端工程师');
    await input('#community-post-content', '## 我的复盘\n这次面试主要讨论了系统设计，下次准备时要补充观测指标和具体的故障恢复过程。');
    await click('预览排版'); expect(container.querySelector('.community-markdown-preview h2')!.textContent).toBe('我的复盘');
    await submit('.community-editor form');
    expect(container.querySelector('dialog')).toBeNull(); expect(cards()).toHaveLength(1);
    expect(cards()[0].textContent).toContain('我的面试复盘');
    const published = calls.find(call => call.method === 'POST' && call.path === '/api/community/posts')!;
    expect(published.body?.draftVersion).toBe(1); expect(published.body?.id).toBeTruthy();
    expect(container.textContent).toContain('其他登录用户');
  });
  it('never reads or writes browser storage and isolates data when accounts change', async () => {
    const read = vi.spyOn(Storage.prototype, 'getItem'); const write = vi.spyOn(Storage.prototype, 'setItem');
    liked = true; await mount(); await click('我的点赞'); expect(cards()).toHaveLength(1);
    await act(async () => useAuth.setState({ user: { id: 'user-b', username: '另一位用户', role: 'user' } }));
    await click('我的点赞'); expect(cards()).toHaveLength(0);
    expect(read).not.toHaveBeenCalled(); expect(write).not.toHaveBeenCalled();
  });
  it('reports failures without fake content or optimistic success', async () => {
    failRead = true; await mount(); expect(cards()).toHaveLength(0);
    expect(container.querySelector('[role="alert"]')!.textContent).toContain('服务暂时不可用');
    failRead = false; await click('重新加载'); expect(cards()).toHaveLength(5);
    failWrite = true; await click('点赞：后端面试复盘 0');
    expect(container.querySelector('[role="alert"]')!.textContent).toContain('服务暂时不可用');
    expect(container.querySelector('.community-feedback')!.textContent).toBe('');
  });
  it('keeps editor content open when server draft saving fails', async () => {
    await mount(); await click('发布面经'); await input('.community-editor input', '不能丢失的草稿');
    failWrite = true; await click('暂存关闭');
    expect(container.querySelector('dialog[open]')).not.toBeNull();
    expect(container.querySelector<HTMLInputElement>('.community-editor input')!.value).toBe('不能丢失的草稿');
    expect(container.querySelector('[role="alert"]')!.textContent).toContain('服务暂时不可用');
  });
  it('only offers edit and delete actions for the current author in the feed and reader', async () => {
    await mount(`/community?post=${firstId}`);
    expect(container.querySelector('[aria-label^="编辑面经："]')).toBeNull();
    expect(container.querySelector('[aria-label^="删除面经："]')).toBeNull();
    await act(async () => useAuth.setState({ user: { id: 'author', username: '分享者', role: 'user' } }));
    expect(container.querySelectorAll('.community-post-management')).toHaveLength(6);
    expect(container.querySelector('.community-reader [aria-label^="编辑面经："]')).not.toBeNull();
    expect(container.querySelector('.community-reader [aria-label^="删除面经："]')).not.toBeNull();
  });
  it('edits a published post from the feed without publishing again or changing an unpublished draft', async () => {
    posts[0].authorId = 'user-a';
    const savedDraft: CommunityDraft = { id: 'another-draft', title: '未发布草稿', company: '', role: '', direction: '前端开发', stage: '校招 · 一面', outcome: '持续复盘', tags: '', content: '' };
    draft = { draft: savedDraft, version: 3 };
    await mount(); await click('编辑面经：后端面试复盘 0');
    expect(container.querySelector<HTMLInputElement>('.community-editor input')!.value).toBe(posts[0].title);
    expect(container.querySelector<HTMLTextAreaElement>('#community-post-content')!.value).toBe(posts[0].content);
    await input('.community-editor input', '补充后的面经');
    await input('.community-editor input[placeholder^="用逗号"]', 'React、新话题');
    await input('#community-post-content', '## 新的复盘\n补充这次面试的详细思考过程，以及具体的改进方法和准备建议。');
    await click('预览排版');
    expect(container.querySelector('.community-markdown-preview h2')!.textContent).toBe('新的复盘');
    await submit('.community-editor form');
    expect(container.querySelector('dialog')).toBeNull();
    expect(cards()[0].textContent).toContain('补充后的面经');
    expect(cards()[0].textContent).toContain('新话题');
    expect(container.querySelector('.community-feedback')!.textContent).toBe('面经已更新');
    const edited = calls.find(call => call.method === 'PUT' && call.path === `/api/community/posts/${firstId}`)!;
    expect(edited.body?.tags).toEqual(['React', '新话题']);
    expect(edited.body).not.toHaveProperty('id'); expect(edited.body).not.toHaveProperty('draftVersion');
    expect(calls.filter(call => call.method !== 'GET')).toEqual([edited]);
    expect(posts).toHaveLength(7); expect(draft).toEqual({ draft: savedDraft, version: 3 });
    await click('发布面经');
    expect(container.querySelector<HTMLInputElement>('.community-editor input')!.value).toBe('未发布草稿');
  });
  it('cancels editing from the reader without saving and shows updated details after a successful edit', async () => {
    posts[0].authorId = 'user-a'; liked = true;
    posts[0].tags = ['算法 / AI', '系统 设计'];
    comments = [{ id: 'comment', authorId: 'reader', author: '读者', content: '保留的评论', createdAt: '2026-10-24T08:00:00Z' }];
    await mount(`/community?post=${firstId}`);
    await act(async () => container.querySelector<HTMLButtonElement>('.community-reader [aria-label^="编辑面经："]')!.click());
    await input('.community-editor input', '放弃的修改'); await click('取消编辑');
    expect(container.querySelector('.community-reader h1')!.textContent).toBe('后端面试复盘 0');
    expect(calls.filter(call => call.method !== 'GET')).toHaveLength(0);
    await act(async () => container.querySelector<HTMLButtonElement>('.community-reader [aria-label^="编辑面经："]')!.click());
    await input('.community-editor input', '保存的修改');
    await submit('.community-editor form');
    expect(container.querySelector('.community-reader h1')!.textContent).toBe('保存的修改');
    expect(posts[0].tags).toEqual(['算法 / AI', '系统 设计']);
    expect(container.querySelector('.community-reader-actions [aria-pressed="true"]')!.textContent).toContain('1');
    expect(container.querySelector('.community-comments article')!.textContent).toContain('保留的评论');
    expect(container.querySelector('.community-reader [role="status"]')!.textContent).toBe('面经已更新');
  });
  it('keeps edits on a failed save and permits retry', async () => {
    posts[0].authorId = 'user-a';
    await mount(); await click('编辑面经：后端面试复盘 0'); await input('.community-editor input', '等待重试的修改');
    failWrite = true; await submit('.community-editor form');
    expect(container.querySelector('.community-editor [role="alert"]')!.textContent).toContain('服务暂时不可用');
    expect(container.querySelector<HTMLInputElement>('.community-editor input')!.value).toBe('等待重试的修改');
    expect(posts[0].title).toBe('后端面试复盘 0');
    failWrite = false; await submit('.community-editor form');
    expect(container.querySelector('dialog')).toBeNull(); expect(posts[0].title).toBe('等待重试的修改');
  });
  it('confirms deletion, refreshes personal counts, and returns from an emptied last page', async () => {
    posts.forEach(post => { post.authorId = 'user-a' });
    await mount(); await click('我的面经'); await click('下一页'); expect(cards()).toHaveLength(2);
    vi.mocked(window.confirm).mockReturnValueOnce(false);
    await click('删除面经：后端面试复盘 5');
    expect(calls.filter(call => call.method === 'DELETE')).toHaveLength(0); expect(cards()).toHaveLength(2);
    await click('删除面经：后端面试复盘 5'); expect(cards()).toHaveLength(1);
    await click('删除面经：后端面试复盘 6'); expect(cards()).toHaveLength(5);
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('点赞和评论将永久删除'));
    expect(container.querySelector('.community-pagination')!.textContent).toContain('第 1 / 1 页');
    expect(container.querySelector('.community-personal-stats strong')!.textContent).toBe('5');
    expect(container.querySelector('.community-feedback')!.textContent).toBe('面经已删除');
  });
  it('preserves the post on deletion failure and closes its reader after retry succeeds', async () => {
    posts[0].authorId = 'user-a';
    await mount(`/community?post=${firstId}`);
    failWrite = true;
    await act(async () => container.querySelector<HTMLButtonElement>('.community-reader [aria-label^="删除面经："]')!.click());
    expect(container.querySelector('.community-reader [role="alert"]')!.textContent).toContain('服务暂时不可用');
    expect(container.querySelector('.community-reader h1')).not.toBeNull(); expect(posts).toHaveLength(7);
    failWrite = false;
    await act(async () => container.querySelector<HTMLButtonElement>('.community-reader [aria-label^="删除面经："]')!.click());
    expect(container.querySelector('dialog')).toBeNull(); expect(posts).toHaveLength(6);
    expect(container.textContent).not.toContain('后端面试复盘 0');
    expect(container.querySelector('.community-feedback')!.textContent).toBe('面经已删除');
  });
});
