import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, readSSE, requestHeaders, requestId } from './api';
import { newerSnapshot } from './hooks';
import type { Interview } from './types';
import { formatDate } from './format';

afterEach(() => { vi.unstubAllGlobals(); document.cookie = 'ra_csrf=; Max-Age=0; path=/' });

describe('platform request boundaries', () => {
  it('creates a UUID on browsers without secure-context randomUUID', () => {
    vi.stubGlobal('crypto', { getRandomValues: (bytes: Uint8Array) => bytes.fill(1) });
    expect(requestId()).toMatch(/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
  });
  it('sends HttpOnly session cookies and the readable CSRF token without setting a multipart content type', async () => {
    document.cookie = 'ra_csrf=test%20token; path=/';
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 'resume-1' }), { status: 200 }));
    vi.stubGlobal('fetch', fetch);
    const form = new FormData(); form.append('file', new File(['resume'], 'resume.txt'));
    await api('/resumes', { method: 'POST', body: form });
    const options = fetch.mock.calls[0][1] as RequestInit;
    expect(options.credentials).toBe('include');
    expect((options.headers as Headers).get('X-CSRF-Token')).toBe('test token');
    expect((options.headers as Headers).has('Content-Type')).toBe(false);
    expect(options.body).toBe(form);
  });
  it('does not add CSRF to read-only requests', () => {
    document.cookie = 'ra_csrf=test; path=/';
    expect(requestHeaders('GET').has('X-CSRF-Token')).toBe(false);
  });
  it('retains HTTP status and displays structured business errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: { message: '问题已切换，请刷新。' } }), { status: 409 })));
    try { await api('/interviews/i/commands', { method: 'POST', body: {} }); throw new Error('expected rejection') }
    catch (error) { expect(error).toBeInstanceOf(ApiError); expect(error).toMatchObject({ status: 409, message: '问题已切换，请刷新。' }) }
  });
  it('supports empty successful responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    expect(await api('/chats/1', { method: 'DELETE' })).toBeUndefined();
  });
});

describe('SSE protocol', () => {
  it('reassembles Chinese UTF-8 content and CRLF event boundaries split across arbitrary network chunks', async () => {
    const bytes = new TextEncoder().encode('event: delta\r\ndata: {"content":"你好"}\r\n\r\nevent: delta\ndata: {"content":"，世界"}\n\nevent: done\ndata: {"message_id":"saved"}\n\n');
    const body = new ReadableStream<Uint8Array>({ start(controller) { for (let i = 0; i < bytes.length; i += 2) controller.enqueue(bytes.slice(i, i + 2)); controller.close() } });
    const events: unknown[] = [];
    await readSSE(body, (event, data) => events.push([event, data]));
    expect(events).toEqual([['delta', { content: '你好' }], ['delta', { content: '，世界' }], ['done', { message_id: 'saved' }]]);
  });
  it('ignores heartbeats and retains an error event at end of stream without a final blank line', async () => {
    const body = new ReadableStream<Uint8Array>({ start(controller) { controller.enqueue(new TextEncoder().encode(': heartbeat\n\nevent: error\ndata: {"message":"不可用"}')); controller.close() } });
    const callback = vi.fn(); await readSSE(body, callback);
    expect(callback).toHaveBeenCalledTimes(1);
    expect(callback).toHaveBeenCalledWith('error', { message: '不可用' });
  });
  it('releases the reader when malformed provider events are rejected', async () => {
    const body = new ReadableStream<Uint8Array>({ start(controller) { controller.enqueue(new TextEncoder().encode('data: invalid\n\n')); controller.close() } });
    await expect(readSSE(body, vi.fn())).rejects.toThrow();
    expect(body.locked).toBe(false);
  });
});

describe('interview event ordering', () => {
  const snapshot = { id: 'i-1', version: 7 } as Interview;
  it('does not replace a committed state with an out-of-order event', () => { expect(newerSnapshot(snapshot, { ...snapshot, version: 6 })).toBe(snapshot) });
  it('accepts advanced and different interview snapshots', () => { expect(newerSnapshot(snapshot, { ...snapshot, version: 8 }).version).toBe(8); expect(newerSnapshot(snapshot, { ...snapshot, id: 'i-2', version: 1 }).id).toBe('i-2') });
});

describe('UTC persistence timestamps', () => {
  it('renders naive UTC and explicit UTC identically in Shanghai time', () => {
    expect(formatDate('2026-10-15T10:00:00')).toContain('18:00');
    expect(formatDate('2026-10-15T10:00:00')).toBe(formatDate('2026-10-15T10:00:00Z'));
  });
});
