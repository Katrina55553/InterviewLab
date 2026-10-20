import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorMessage, websocketUrl } from './api';

export function useSpeechInput(interviewId: string, onTranscript: (text: string) => void) {
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState('');
  const callback = useRef(onTranscript);
  useEffect(() => { callback.current = onTranscript }, [onTranscript]);
  const capture = useRef<{ stream?: MediaStream; context?: AudioContext; node?: AudioWorkletNode; socket?: WebSocket; timer?: number }>({});
  const generation = useRef(0);
  const cleanup = useCallback((finalize = true) => {
    generation.current++;
    const current = capture.current;
    current.node?.disconnect();
    current.stream?.getTracks().forEach(track => track.stop());
    void current.context?.close().catch(() => {});
    window.clearTimeout(current.timer);
    if (current.socket) {
      const socket = current.socket;
      if (finalize && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: 'end' }));
        current.timer = window.setTimeout(() => socket.close(), 3000);
      } else socket.close();
    }
    capture.current = finalize ? { socket: current.socket, timer: current.timer } : {};
    setRecording(false);
  }, []);
  const start = useCallback(async () => {
    cleanup(false);
    const currentGeneration = generation.current;
    setError('');
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error('语音输入需要 HTTPS 或本机 localhost，以及麦克风权限。');
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true }, video: false });
      if (currentGeneration !== generation.current) { stream.getTracks().forEach(track => track.stop()); return }
      const context = new AudioContext();
      capture.current = { stream, context };
      if (!context.audioWorklet) throw new Error('当前浏览器不支持音频工作线程，请使用新版 Chrome 或 Edge。');
      await context.audioWorklet.addModule('/pcm-worklet.js');
      if (currentGeneration !== generation.current) return;
      const socket = new WebSocket(websocketUrl(`/ws/asr/${interviewId}`));
      capture.current.socket = socket;
      const node = new AudioWorkletNode(context, 'pcm-capture');
      capture.current.node = node;
      node.port.onmessage = event => {
        if (socket.readyState !== WebSocket.OPEN) return;
        if (socket.bufferedAmount > 64000) { setError('网络传输较慢，语音输入已暂停，请检查连接后重试。'); cleanup(); return }
        socket.send(event.data);
      };
      socket.onopen = () => {
        if (currentGeneration !== generation.current) { socket.close(); return }
        socket.send(JSON.stringify({ type: 'start' }));
        context.createMediaStreamSource(stream).connect(node);
        node.connect(context.destination);
        void context.resume();
        setRecording(true);
      };
      socket.onmessage = event => {
        if (capture.current.socket !== socket) return;
        try {
          const message = JSON.parse(event.data);
          if (message.type === 'transcript') callback.current(message.display_text ?? `${message.committed_text ?? ''}${message.live_text ?? ''}`);
          if (message.type === 'error') { setError(message.message ?? message.error ?? '语音转写未完成'); cleanup(false) }
        } catch { /* Only structured transcription events are relevant. */ }
      };
      socket.onerror = () => { if (capture.current.socket === socket) { setError('语音连接失败，请重试或使用文字输入。'); cleanup(false) } };
      socket.onclose = () => { if (capture.current.socket === socket) cleanup(false) };
    } catch (err) { if (currentGeneration === generation.current) { setError(errorMessage(err)); cleanup(false) } }
  }, [cleanup, interviewId]);
  useEffect(() => () => cleanup(false), [cleanup, interviewId]);
  return { recording, error, start, stop: cleanup };
}

export function useSpeechOutput(interviewId: string) {
  const [speaking, setSpeaking] = useState(false);
  const [error, setError] = useState('');
  const playback = useRef<{ controller?: AbortController; audio?: HTMLAudioElement }>({});
  const stop = useCallback(() => {
    const previous = playback.current;
    playback.current = {};
    previous.controller?.abort();
    if (previous.audio) {
      previous.audio.onended = null;
      previous.audio.onerror = null;
      previous.audio.pause();
      previous.audio.src = '';
    }
    setSpeaking(false);
  }, []);
  const speak = useCallback(async (text: string) => {
    stop(); setSpeaking(true); setError('');
    const controller = new AbortController();
    playback.current.controller = controller;
    try {
      let task = await api<{ id: string; status: string; audio_url?: string; error?: string }>('/media/tts', { method: 'POST', body: { interview_id: interviewId, text }, signal: controller.signal });
      const deadline = Date.now() + 120000;
      while (!['completed', 'ready', 'succeeded', 'success', 'failed', 'unknown'].includes(task.status)) {
        if (Date.now() > deadline) throw new Error('语音合成等待超时，请稍后重试。');
        await new Promise<void>((resolve, reject) => {
          const abort = () => { window.clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')) };
          const timer = window.setTimeout(() => { controller.signal.removeEventListener('abort', abort); resolve() }, 1200);
          controller.signal.addEventListener('abort', abort, { once: true });
        });
        task = await api(`/media/tts/${task.id}`, { signal: controller.signal });
      }
      if (['failed', 'unknown'].includes(task.status)) throw new Error(task.error ?? '语音合成未完成，请确认后重新尝试。');
      if (!task.audio_url) throw new Error('语音合成尚未返回音频。');
      if (controller.signal.aborted) return;
      const audio = new Audio(task.audio_url);
      playback.current.audio = audio;
      audio.onended = () => { if (playback.current.audio === audio) stop() };
      audio.onerror = () => { if (playback.current.audio === audio) { setError('音频无法播放，请重试。'); stop() } };
      await audio.play();
    } catch (err) { if (!controller.signal.aborted) { setError(errorMessage(err)); stop() } }
  }, [interviewId, stop]);
  useEffect(() => () => stop(), [interviewId, stop]);
  return { speaking, error, speak, stop };
}
