import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { HistoryPage } from './History';
import { MyResumesPage } from './MyResumes';

let root: Root;
let container: HTMLDivElement;
let items: { id: string; filename: string; status: string; created_at: string }[];
let deleteStatus: number;
let pendingDelete: Promise<Response> | undefined;

const response = (body: unknown, status = 200) => new Response(status === 204 ? null : JSON.stringify(body), { status });
const settle = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) }) };
const deleteButton = () => container.querySelector<HTMLButtonElement>('button[aria-label^="删除"]')!;
const click = async (element: HTMLElement) => { await act(async () => element.click()); await settle() };
async function render(kind: 'resume' | 'interview') {
  const path = kind === 'resume' ? '/my-resumes' : '/history';
  await act(async () => root.render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path={path} element={kind === 'resume' ? <MyResumesPage /> : <HistoryPage />} />
    <Route path="*" element={<p>打开详情</p>} />
  </Routes></MemoryRouter>));
  await settle();
}

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  document.cookie = 'ra_csrf=delete-csrf';
  items = [{ id: 'record-1', filename: '我的简历.txt', status: 'completed', created_at: '2026-10-26T00:00:00' }];
  deleteStatus = 204; pendingDelete = undefined;
  vi.stubGlobal('fetch', vi.fn(async (_url: string, options?: RequestInit) => {
    if (options?.method === 'DELETE') {
      if (pendingDelete) return pendingDelete;
      if (deleteStatus === 204) items = [];
      return response({ detail: '这份简历有关联面试，请先删除相关面试' }, deleteStatus);
    }
    return response(items);
  }));
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount()); container.remove();
  sessionStorage.clear(); document.cookie = 'ra_csrf=; Max-Age=0';
  vi.restoreAllMocks(); vi.unstubAllGlobals();
});

describe.each(['resume', 'interview'] as const)('%s deletion', kind => {
  it('cancels without deleting or opening the record', async () => {
    vi.mocked(window.confirm).mockReturnValue(false);
    await render(kind);
    await click(deleteButton());
    expect(window.confirm).toHaveBeenCalledTimes(1);
    expect(vi.mocked(fetch).mock.calls.some(([, options]) => options?.method === 'DELETE')).toBe(false);
    expect(deleteButton()).not.toBeNull();
    expect(container.textContent).not.toContain('打开详情');
  });

  it('confirms, sends CSRF and removes only the selected record', async () => {
    sessionStorage.setItem('meeting:command:record-1', 'saved command');
    await render(kind);
    await click(deleteButton());
    const request = vi.mocked(fetch).mock.calls.find(([, options]) => options?.method === 'DELETE')!;
    expect(request[0]).toBe(`/api/${kind === 'resume' ? 'resumes' : 'interviews'}/record-1`);
    expect(new Headers(request[1]?.headers).get('X-CSRF-Token')).toBe('delete-csrf');
    expect(container.textContent).toContain(kind === 'resume' ? '还没有简历' : '还没有面试记录');
    expect(container.textContent).not.toContain('打开详情');
    if (kind === 'interview') expect(sessionStorage.getItem('meeting:command:record-1')).toBeNull();
  });

  it('keeps the record and shows the server error when deletion fails', async () => {
    deleteStatus = 409;
    await render(kind);
    await click(deleteButton());
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('请先删除相关面试');
    expect(deleteButton().disabled).toBe(false);
    deleteStatus = 204;
    await click(deleteButton());
    expect(deleteButton()).toBeNull();
  });

  it('prevents duplicate deletes while waiting for the server', async () => {
    let resolve!: (value: Response) => void;
    pendingDelete = new Promise(done => { resolve = done });
    await render(kind);
    const button = deleteButton();
    await act(async () => { button.click(); button.click() });
    expect(button.disabled).toBe(true);
    expect(vi.mocked(fetch).mock.calls.filter(([, options]) => options?.method === 'DELETE')).toHaveLength(1);
    items = [];
    await act(async () => resolve(response(null, 204))); await settle();
    expect(deleteButton()).toBeNull();
  });
});

it('ignores a stale list response that finishes after deletion', async () => {
  await render('interview');
  let resolve!: (value: Response) => void;
  vi.mocked(fetch).mockImplementationOnce(() => new Promise(done => { resolve = done }));
  const refresh = [...container.querySelectorAll('button')].find(button => button.textContent === '刷新')!;
  await click(refresh);
  const staleItems = [...items];
  await click(deleteButton());
  await act(async () => resolve(response(staleItems))); await settle();
  expect(container.textContent).toContain('还没有面试记录');
  expect(deleteButton()).toBeNull();
});
