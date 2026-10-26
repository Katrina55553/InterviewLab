import { act } from 'react';
import { createRoot } from 'react-dom/client';
import type { Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { CameraPanel, InterviewRoomLayout } from './Camera';

let root: Root;
let container: HTMLDivElement;
let stop: ReturnType<typeof vi.fn>;
const originalMedia = navigator.mediaDevices;
async function click(text: string) { await act(async () => [...container.querySelectorAll('button')].find(item => item.textContent === text)!.click()) }
async function tick() { await act(async () => { await vi.advanceTimersByTimeAsync(5000) }) }

beforeEach(() => {
  vi.useFakeTimers(); vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  stop = vi.fn();
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: vi.fn(async () => ({ getTracks: () => [{ stop }] })) } });
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
  vi.spyOn(HTMLVideoElement.prototype, 'videoWidth', 'get').mockReturnValue(640);
  vi.spyOn(HTMLVideoElement.prototype, 'videoHeight', 'get').mockReturnValue(480);
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => ({ drawImage: vi.fn() }) as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(callback => callback(new Blob(['frame'], { type: 'image/jpeg' })));
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ available: true, feedback: '镜头高度合适。' }), { status: 200 })));
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount()); container.remove(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals();
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: originalMedia });
});
async function render() {
  await act(async () => root.render(<MemoryRouter><Routes><Route element={<InterviewRoomLayout />}><Route index element={<CameraPanel interviewId="interview-1" enabled active />} /></Route></Routes></MemoryRouter>));
}

it('keeps configured cameras local until explicit opt-in and stops uploading when paused or closed', async () => {
  await render(); await tick();
  expect(fetch).not.toHaveBeenCalled();
  expect(container.textContent).toContain('当前仅在本机预览');
  await click('开启视觉建议'); await tick();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(vi.mocked(fetch).mock.calls[0][0]).toBe('/api/interviews/interview-1/vision');
  expect(container.textContent).toContain('镜头高度合适');
  await click('暂停视觉建议'); await tick();
  expect(fetch).toHaveBeenCalledTimes(1);
  await click('开启视觉建议'); await click('关闭摄像头'); await tick();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(stop).toHaveBeenCalledTimes(1);
  await click('开启摄像头'); await tick();
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('aborts an in-flight frame request when visual suggestions are paused', async () => {
  vi.mocked(fetch).mockImplementation(() => new Promise(() => {}));
  await render(); await click('开启视觉建议'); await tick();
  const options = vi.mocked(fetch).mock.calls[0][1]!;
  expect(options.signal?.aborted).toBe(false);
  await click('暂停视觉建议');
  expect(options.signal?.aborted).toBe(true);
});
