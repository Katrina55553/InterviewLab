import { useEffect, useRef, useState } from 'react';
import { api, errorMessage } from './api';
import { Icon, Notice } from './ui';

export function CameraPanel({ interviewId, enabled, active }: { interviewId: string; enabled: boolean; active: boolean }) {
  const [camera, setCamera] = useState(false);
  const [starting, setStarting] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [error, setError] = useState('');
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const request = useRef<AbortController | null>(null);
  const session = useRef(0);
  const stop = () => {
    session.current++;
    stream.current?.getTracks().forEach(track => track.stop());
    stream.current = null;
    request.current?.abort();
    request.current = null;
    setCamera(false); setStarting(false);
  };
  const start = async () => {
    setError(''); setStarting(true);
    const current = ++session.current;
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error('摄像头需要 HTTPS 或本机 localhost。');
      const media = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 640 }, height: { ideal: 480 } }, audio: false });
      if (current !== session.current) { media.getTracks().forEach(track => track.stop()); return }
      stream.current = media; setCamera(true); setStarting(false);
      if (video.current) { video.current.srcObject = media; await video.current.play() }
    } catch (err) { if (current === session.current) { setError(errorMessage(err)); stop() } }
  };
  useEffect(() => {
    if (!camera || !active) return;
    let stopped = false;
    const capture = async () => {
      if (!video.current || !video.current.videoWidth || request.current) return;
      const canvas = document.createElement('canvas');
      canvas.width = 640; canvas.height = Math.round(video.current.videoHeight / video.current.videoWidth * 640);
      canvas.getContext('2d')?.drawImage(video.current, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise<Blob | null>(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.7));
      if (!blob || !stream.current || stopped || request.current) return;
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
  }, [camera, active, interviewId]);
  useEffect(() => () => { session.current++; stream.current?.getTracks().forEach(track => track.stop()); request.current?.abort() }, [interviewId]);
  return <section className="card camera-card"><div className="section-heading"><h3><Icon name="camera" size={18} />仪态练习</h3>{camera ? <button className="text-button" onClick={stop}>关闭摄像头</button> : <button className="text-button" disabled={!enabled || !active || starting} onClick={() => void start()}>{starting ? '正在开启…' : '开启摄像头'}</button>}</div><video ref={video} muted playsInline className={camera ? 'camera-preview' : 'camera-preview hidden'} aria-label="摄像头预览" />{!camera && <p className="muted small">{enabled ? '主动开启后，每 5 秒抽取一帧提供表达与仪态建议。' : '管理员尚未启用视觉模型。'}</p>}<p className="small muted">仪态反馈单独展示，不计入答题得分。</p>{feedback && <p className="camera-feedback">{feedback}</p>}{error && <Notice>{error}</Notice>}</section>;
}
