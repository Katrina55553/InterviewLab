import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { Outlet } from 'react-router-dom';
import { api, errorMessage } from './api';
import { Icon, Notice } from './ui';

type CameraSession = { media: MediaStream | null; starting: boolean; error: string; start: () => Promise<void>; stop: () => void };
const CameraContext = createContext<CameraSession | null>(null);

// Keep the device open while moving from resume collection to interview questions.
export function InterviewRoomLayout() {
  const [media, setMedia] = useState<MediaStream | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');
  const stream = useRef<MediaStream | null>(null);
  const session = useRef(0);
  const pending = useRef(false);
  const release = useCallback(() => {
    session.current++; pending.current = false;
    stream.current?.getTracks().forEach(track => track.stop());
    stream.current = null;
  }, []);
  const stop = useCallback(() => { release(); setMedia(null); setStarting(false) }, [release]);
  const start = useCallback(async () => {
    if (pending.current || stream.current) return;
    pending.current = true; setError(''); setStarting(true);
    const current = ++session.current;
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error('摄像头需要 HTTPS 或本机 localhost。');
      const next = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 640 }, height: { ideal: 480 } }, audio: false });
      if (current !== session.current) { next.getTracks().forEach(track => track.stop()); return }
      stream.current = next; setMedia(next);
    } catch (err) {
      if (current === session.current) {
        const name = err && typeof err === 'object' && 'name' in err ? err.name : '';
        setError(name === 'NotAllowedError' ? '摄像头权限未获允许，可在浏览器设置中开启，也可以继续文字面试。' : name === 'NotFoundError' ? '未找到摄像头，你仍可继续文字面试。' : errorMessage(err));
      }
    } finally {
      if (current === session.current) { pending.current = false; setStarting(false) }
    }
  }, []);
  useEffect(() => {
    let mounted = true;
    queueMicrotask(() => { if (mounted) void start() });
    return () => { mounted = false; release() };
  }, [start, release]);
  return <CameraContext.Provider value={{ media, starting, error, start, stop }}><Outlet /></CameraContext.Provider>;
}

export function CameraPanel({ interviewId, enabled, active, finished = false }: { interviewId?: string; enabled: boolean; active: boolean; finished?: boolean }) {
  const session = useContext(CameraContext);
  const stop = session?.stop;
  useEffect(() => { if (finished) stop?.() }, [finished, stop]);
  if (finished) return null;
  if (!session) throw new Error('CameraPanel requires InterviewRoomLayout');
  return <CameraPreview {...session} interviewId={interviewId} enabled={enabled} active={active} />;
}

function CameraPreview({ media, starting, error: cameraError, start, stop, interviewId, enabled, active }: CameraSession & { interviewId?: string; enabled: boolean; active: boolean }) {
  const camera = !!media;
  const [feedback, setFeedback] = useState('');
  const [error, setError] = useState('');
  const video = useRef<HTMLVideoElement>(null);
  const [advisingFor, setAdvisingFor] = useState<string | null>(null);
  const advising = enabled && !!interviewId && advisingFor === interviewId;
  const request = useRef<AbortController | null>(null);
  useEffect(() => {
    const element = video.current;
    if (!element) return;
    element.srcObject = media;
    if (media) void element.play().catch(() => { /* The browser can resume playback after interaction. */ });
    return () => { element.srcObject = null };
  }, [media]);
  useEffect(() => {
    if (!camera || !active || !advising || !interviewId) return;
    let stopped = false;
    const capture = async () => {
      if (!video.current || !video.current.videoWidth || request.current) return;
      const canvas = document.createElement('canvas');
      canvas.width = 640; canvas.height = Math.round(video.current.videoHeight / video.current.videoWidth * 640);
      canvas.getContext('2d')?.drawImage(video.current, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise<Blob | null>(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.7));
      if (!blob || !media || stopped || request.current) return;
      const controller = new AbortController(); request.current = controller;
      const form = new FormData(); form.append('file', blob, 'practice.jpg');
      try {
        const result = await api<{ feedback: string | Record<string, unknown>; available: boolean }>(`/interviews/${interviewId}/vision`, { method: 'POST', body: form, signal: controller.signal });
        if (controller.signal.aborted) return;
        if (!result.available) { setError('仪态反馈暂不可用，请稍后重试。'); return }
        setFeedback(typeof result.feedback === 'string' ? result.feedback : Object.values(result.feedback).filter(value => typeof value === 'string').join('；'));
        setError('');
      } catch (err) { if (!controller.signal.aborted) setError(errorMessage(err)) }
      finally { if (request.current === controller) request.current = null }
    };
    const timer = window.setInterval(() => void capture(), 5000);
    return () => { stopped = true; window.clearInterval(timer); request.current?.abort(); request.current = null };
  }, [camera, media, active, advising, interviewId]);
  return <section className="card camera-card"><div className="section-heading"><h3><Icon name="camera" size={18} />摄像头</h3>{camera ? <button className="text-button" onClick={() => { setAdvisingFor(null); stop() }}>关闭摄像头</button> : <button className="text-button" disabled={starting} onClick={() => void start()}>{starting ? '正在开启…' : '开启摄像头'}</button>}</div><video ref={video} muted playsInline className={camera ? 'camera-preview' : 'camera-preview hidden'} aria-label="摄像头预览" />{!camera && <p className="muted small">{starting ? '请在浏览器中允许使用摄像头。' : '摄像头未开启，你仍可继续面试。'}</p>}<p className="small muted">{advising ? '每 5 秒向视觉模型发送一帧画面提供仪态建议，可能产生费用，不计入答题得分。' : '当前仅在本机预览，不上传摄像头画面。'}</p>{enabled && interviewId && <button className="text-button" disabled={!camera} onClick={() => { setAdvisingFor(advising ? null : interviewId); setError('') }}>{advising ? '暂停视觉建议' : '开启视觉建议'}</button>}{enabled && interviewId && !advising && <p className="small muted">开启后将定期发送画面给已配置的视觉模型，仅保存文字建议。</p>}{feedback && <p className="camera-feedback">{feedback}</p>}{(cameraError || error) && <Notice>{cameraError || error}</Notice>}</section>;
}
