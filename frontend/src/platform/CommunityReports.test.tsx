import { act } from 'react';
import { createRoot } from 'react-dom/client';
import type { Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CommunityModerationPage, CommunityReportsPage } from './CommunityReports';
import { useAuth } from './auth';
import type { CommunityReport } from './community-data';

let container: HTMLDivElement;
let root: Root;
let reports: CommunityReport[];
let reviewError: number;
const calls: { path: string; method: string; body?: Record<string, unknown> }[] = [];
const report = (id: string): CommunityReport => ({ id, targetType: 'post', targetId: `post-${id}`, postId: `post-${id}`, reason: 'privacy', details: '包含电话号码', status: 'pending', createdAt: '2026-10-26T00:00:00Z', reviewedAt: null, reviewNote: '', targetTitle: `待审核面经 ${id}`, targetContent: '<script>保留原文证据</script>', reporter: '举报人', reviewer: null, targetState: 'visible' });
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const click = (label: string) => act(async () => {
  const button = [...container.querySelectorAll('button')].find(item => item.textContent?.trim() === label);
  if (!button) throw new Error(`Missing button ${label}`);
  button.click();
});
const inputNote = (value: string) => act(async () => {
  const input = container.querySelector<HTMLTextAreaElement>('#community-review-note')!;
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
});
const submit = () => act(async () => { container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })) });
const mount = (admin = true) => act(async () => {
  root.render(<MemoryRouter initialEntries={[admin ? '/admin/community' : '/community/reports']}><Routes><Route path="/admin/community" element={<CommunityModerationPage />} /><Route path="/community/reports" element={<CommunityReportsPage />} /><Route path="/community" element={<p>面经社区</p>} /></Routes></MemoryRouter>);
});

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  HTMLDialogElement.prototype.showModal = function () { this.open = true };
  HTMLDialogElement.prototype.close = function () { this.open = false };
  useAuth.setState({ user: { id: 'admin', username: '审核员', role: 'admin' }, loading: false });
  calls.length = 0; reviewError = 0; reports = [report('1')];
  vi.stubGlobal('fetch', vi.fn(async (path: string, options?: RequestInit) => {
    const url = new URL(path, 'http://test'); const method = options?.method ?? 'GET';
    const body = options?.body ? JSON.parse(String(options.body)) : undefined;
    calls.push({ path, method, body });
    if (method === 'POST') {
      if (reviewError) return json({ detail: reviewError === 409 ? '举报已被其他管理员处理' : '审核暂时不可用' }, reviewError);
      const current = reports.find(item => path.includes(`/${item.id}/review`))!;
      Object.assign(current, { status: body.action === 'hide' ? 'resolved' : 'dismissed', targetState: body.action === 'hide' ? 'hidden' : 'visible', reviewNote: body.note, reviewedAt: '2026-10-26T01:00:00Z', reviewer: '审核员' });
      return json(current);
    }
    const status = url.searchParams.get('status');
    const items = reports.filter(item => !status || status === 'all' || item.status === status);
    const page = Number(url.searchParams.get('page') ?? 1);
    return json({ items: items.slice((page - 1) * 20, page * 20), total: items.length, page, pageSize: 20 });
  }));
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals() });

describe('community moderation', () => {
  it('blocks ordinary accounts before requesting the administrator queue', async () => {
    useAuth.setState({ user: { id: 'reader', username: '读者', role: 'user' } });
    await mount(); expect(container.textContent).toBe('面经社区'); expect(calls).toHaveLength(0);
  });
  it('shows evidence as text, requires a review note, and refreshes after hiding content', async () => {
    await mount(); expect(calls[0].path).toContain('status=pending');
    expect(container.textContent).toContain('<script>保留原文证据</script>'); expect(container.querySelector('script')).toBeNull();
    await click('下架内容'); await inputNote('   '); await submit();
    expect(container.querySelector('[role="alert"]')!.textContent).toContain('请填写审核说明');
    expect(calls.filter(call => call.method === 'POST')).toHaveLength(0);
    await inputNote('已核实，正文公开了个人电话号码。'); await submit();
    expect(calls.find(call => call.method === 'POST')).toMatchObject({ path: '/api/community/admin/reports/1/review', body: { action: 'hide', note: '已核实，正文公开了个人电话号码。' } });
    expect(container.querySelector('dialog')).toBeNull(); expect(container.textContent).toContain('内容已下架'); expect(container.textContent).toContain('暂无相关举报');
    await click('已下架'); expect(container.textContent).toContain('已核实，正文公开了个人电话号码。');
    expect(container.textContent).toContain('审核人：审核员'); expect(container.querySelector('a[href^="/community?post="]')).toBeNull();
  });
  it('keeps a failed review note for retry and can dismiss a report', async () => {
    await mount(); await click('驳回举报'); await inputNote('核实为公开的企业招聘电话。');
    reviewError = 503; await submit();
    expect(container.querySelector<HTMLTextAreaElement>('#community-review-note')!.value).toBe('核实为公开的企业招聘电话。');
    expect(container.querySelector('[role="alert"]')!.textContent).toContain('审核暂时不可用');
    reviewError = 0; await submit(); await click('已驳回');
    expect(container.textContent).toContain('核实为公开的企业招聘电话。');
    expect(calls.filter(call => call.method === 'POST').at(-1)?.body?.action).toBe('dismiss');
  });
  it('refreshes conflicting decisions and prevents further submission from a stale form', async () => {
    await mount(); await click('下架内容'); await inputNote('已核实违规。');
    reviewError = 409; await submit();
    expect(container.querySelector('dialog')).toBeNull(); expect(container.textContent).toContain('举报已被其他管理员处理');
    expect(calls.filter(call => call.method === 'GET')).toHaveLength(2);
  });
  it('shows account history, review results, pagination and clears old records when accounts change', async () => {
    reports = Array.from({ length: 21 }, (_, i) => report(String(i)));
    reports[0] = { ...reports[0], status: 'dismissed', reviewedAt: '2026-10-26T01:00:00Z', reviewNote: '核实无违规。', reviewer: '审核员' };
    await mount(false); expect(calls[0].path).toBe('/api/community/reports/mine?page=1');
    expect(container.querySelectorAll('.community-report-card')).toHaveLength(20); expect(container.textContent).toContain('核实无违规。');
    expect(container.textContent).not.toContain('下架内容'); await click('下一页');
    expect(container.querySelectorAll('.community-report-card')).toHaveLength(1);
    reports = [];
    await act(async () => useAuth.setState({ user: { id: 'other', username: '另一账户', role: 'user' } }));
    expect(container.querySelectorAll('.community-report-card')).toHaveLength(0); expect(container.textContent).toContain('暂无举报记录');
    expect(calls.at(-1)?.path).toBe('/api/community/reports/mine?page=1');
  });
});
