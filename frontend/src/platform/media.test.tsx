import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import { useSpeechInput, useSpeechOutput } from './media';

afterEach(() => { vi.unstubAllGlobals() });
it('ignores a late error from an old microphone socket after a new recording starts', async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  const sockets: Socket[] = [];
  class Socket {
    static OPEN = 1;
    readyState = 0;
    bufferedAmount = 0;
    onopen?: () => void;
    onmessage?: () => void;
    onclose?: () => void;
    onerror?: () => void;
    constructor() { sockets.push(this) }
    send() { /* Deliberately no provider calls. */ }
    close() { if (this.readyState === 3) return; this.readyState = 3; queueMicrotask(() => this.onclose?.()) }
  }
  const tracks: { stop: ReturnType<typeof vi.fn> }[] = [];
  const mediaDevices = navigator.mediaDevices;
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: vi.fn(async () => { const track = { stop: vi.fn() }; tracks.push(track); return { getTracks: () => [track] } }) } });
  vi.stubGlobal('WebSocket', Socket);
  vi.stubGlobal('AudioContext', class {
    audioWorklet = { addModule: async () => {} };
    destination = {};
    createMediaStreamSource() { return { connect: vi.fn() } }
    close() { return Promise.resolve() }
    resume() { return Promise.resolve() }
  });
  vi.stubGlobal('AudioWorkletNode', class { port = { onmessage: null }; connect() {} disconnect() {} });
  function Capture() { const speech = useSpeechInput('interview-1', vi.fn()); return <><button onClick={() => void speech.start()}>start</button><span>{speech.recording ? 'recording' : 'stopped'}</span><div>{speech.error}</div></> }
  const container = document.createElement('div'); document.body.appendChild(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<Capture />));
    await act(async () => container.querySelector('button')!.click());
    await act(async () => { sockets[0].readyState = 1; sockets[0].onopen?.() });
    await act(async () => container.querySelector('button')!.click());
    await act(async () => { sockets[1].readyState = 1; sockets[1].onopen?.() });
    expect(tracks[0].stop).toHaveBeenCalled();
    await act(async () => sockets[0].onerror?.());
    expect(container.textContent).toContain('recording');
    expect(container.textContent).not.toContain('语音连接失败');
    expect(sockets[1].readyState).toBe(1);
    expect(tracks[1].stop).not.toHaveBeenCalled();
  } finally {
    await act(async () => root.unmount()); container.remove();
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: mediaDevices });
  }
});

it('ignores ended and error callbacks queued by a previous question audio', async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  const audios: TestAudio[] = [];
  class TestAudio {
    src: string;
    onended: (() => void) | null = null;
    onerror: (() => void) | null = null;
    pause = vi.fn();
    play = vi.fn(async () => {});
    constructor(src: string) { this.src = src; audios.push(this) }
  }
  vi.stubGlobal('Audio', TestAudio);
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ id: 'tts-1', status: 'completed', audio_url: '/api/media/tts/tts-1/audio' }), { status: 200 })));
  function Playback() { const speech = useSpeechOutput('interview-1'); return <><button onClick={() => void speech.speak('question')}>speak</button><span>{speech.speaking ? 'speaking' : 'stopped'}</span><div>{speech.error}</div></> }
  const container = document.createElement('div'); document.body.appendChild(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<Playback />));
    await act(async () => container.querySelector('button')!.click());
    const oldEnded = audios[0].onended;
    const oldError = audios[0].onerror;
    await act(async () => container.querySelector('button')!.click());
    expect(audios[0].onended).toBeNull(); expect(audios[0].onerror).toBeNull();
    await act(async () => { oldEnded?.(); oldError?.() });
    expect(audios[1].pause).not.toHaveBeenCalled();
    expect(container.textContent).toContain('speaking');
    expect(container.textContent).not.toContain('音频无法播放');
  } finally { await act(async () => root.unmount()); container.remove() }
});
