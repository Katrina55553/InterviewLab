import { act } from 'react';
import { createRoot } from 'react-dom/client';
import type { Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { RepositoryBadge } from './RepositoryBadge';

const hour = 60 * 60 * 1000;
let container: HTMLDivElement;
let root: Root | null;
let hidden = false;
const snapshot = { stars: 12, forks: 2, source: 'github', updated_at: '2026-10-20T19:00:00Z' };
const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
const counts = () => [...container.querySelectorAll('.repository-badge-stats > span')].map(item => item.textContent);
const mount = () => act(async () => { root!.render(<RepositoryBadge />) });
const tick = () => act(async () => { await vi.advanceTimersByTimeAsync(hour) });

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  hidden = false;
  vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden);
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(snapshot)));
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => { root?.unmount() });
  container.remove();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it('shows defaults immediately, then updates numbers and the accessible label', async () => {
  let resolve!: (response: Response) => void;
  vi.mocked(fetch).mockReturnValue(new Promise<Response>(done => { resolve = done }));
  await mount();
  expect(counts()).toEqual(['4', '0']);
  expect(fetch).toHaveBeenCalledWith('/api/repository/stats', expect.objectContaining({ credentials: 'include' }));
  await act(async () => { resolve(json(snapshot)) });
  expect(counts()).toEqual(['12', '2']);
  const link = container.querySelector('a')!;
  expect(link.getAttribute('aria-label')).toContain('12 个 star，2 个 fork');
  expect(link.href).toBe('https://github.com/Katrina55553/InterviewLab');
  expect(link.target).toBe('_blank');
  expect(link.rel).toBe('noopener noreferrer');
});

it('accepts zero as a real GitHub count', async () => {
  vi.mocked(fetch).mockResolvedValue(json({ ...snapshot, stars: 0, forks: 0 }));
  await mount();
  expect(counts()).toEqual(['0', '0']);
});

it('keeps real numbers through cache fallback and subsequent network failures', async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(json(snapshot))
    .mockResolvedValueOnce(json({ stars: 4, forks: 0, source: 'fallback', updated_at: null }))
    .mockRejectedValueOnce(new TypeError('offline'));
  await mount();
  await tick();
  expect(counts()).toEqual(['12', '2']);
  await tick();
  expect(counts()).toEqual(['12', '2']);
});

it('keeps defaults when the initial metadata request fails', async () => {
  vi.mocked(fetch).mockRejectedValue(new TypeError('offline'));
  await mount();
  expect(counts()).toEqual(['4', '0']);
});

it.each([-1, '7', true, null])('ignores invalid counts: %s', async stars => {
  vi.mocked(fetch).mockResolvedValue(json({ ...snapshot, stars }));
  await mount();
  expect(counts()).toEqual(['4', '0']);
});

it('polls hourly only while visible and clears the timer on unmount', async () => {
  // Each response body is consumed only once, as with a real HTTP response.
  vi.mocked(fetch).mockImplementation(async () => json(snapshot));
  await mount();
  hidden = true;
  await tick();
  expect(fetch).toHaveBeenCalledTimes(1);
  hidden = false;
  await tick();
  expect(fetch).toHaveBeenCalledTimes(2);
  await act(async () => { root!.unmount(); root = null });
  expect(vi.getTimerCount()).toBe(0);
  await tick();
  expect(fetch).toHaveBeenCalledTimes(2);
});

it('avoids overlapping requests and aborts pending work on unmount', async () => {
  let resolve!: (response: Response) => void;
  vi.mocked(fetch).mockReturnValue(new Promise<Response>(done => { resolve = done }));
  await mount();
  await tick();
  expect(fetch).toHaveBeenCalledTimes(1);
  const signal = vi.mocked(fetch).mock.calls[0][1]?.signal;
  await act(async () => { root!.unmount(); root = null });
  expect(signal?.aborted).toBe(true);
  await act(async () => { resolve(json(snapshot)) });
  expect(container.textContent).toBe('');
});
