import { useRef, useState } from 'react';
import { api, errorMessage } from './api';
import type { Resume } from './types';
import { Icon, Notice } from './ui';

export function ResumeUpload({ onUploaded }: { onUploaded: (resume: Resume) => void }) {
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState('');
  const lock = useRef(false);
  const input = useRef<HTMLInputElement>(null);
  const upload = async (file?: File) => {
    if (!file || lock.current) return;
    const extension = file.name.split('.').pop()?.toLowerCase();
    if (!['pdf', 'docx', 'txt'].includes(extension ?? '')) { setError('请选择 PDF、DOCX 或 TXT 简历文件。'); return }
    if (file.size > 10 * 1024 * 1024) { setError('文件大小不能超过 10 MB。'); return }
    lock.current = true; setBusy(true); setError('');
    const form = new FormData(); form.append('file', file);
    try { const resume = await api<Resume>('/resumes', { method: 'POST', body: form }); onUploaded(resume) }
    catch (err) { setError(errorMessage(err)) }
    finally { lock.current = false; setBusy(false); if (input.current) input.current.value = '' }
  };
  return <><input ref={input} className="sr-only" type="file" accept=".pdf,.docx,.txt" aria-label="上传简历文件" onChange={event => void upload(event.target.files?.[0])} /><button className={`dropzone ${dragging ? 'dragging' : ''}`} disabled={busy} onClick={() => input.current?.click()} onDragOver={event => { event.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); void upload(event.dataTransfer.files[0]) }}><span className="upload-icon"><Icon name="upload" size={26} /></span><strong>{busy ? '正在上传你的简历…' : '将简历拖到这里'}</strong><span>{busy ? '上传完成后将自动解析简历' : '或点击选择文件'}</span><small>PDF / DOCX / TXT · 最大 10 MB</small></button>{error && <Notice>{error}</Notice>}</>;
}
