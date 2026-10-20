import { useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { CameraPanel } from './Camera';
import { api, errorMessage } from './api';
import { useResource } from './hooks';
import { ResumeUpload } from './ResumeUpload';
import type { Interview, Resume } from './types';
import { Icon, Loading, Notice, Status } from './ui';

export function InterviewRoomPage() {
  const [params, setParams] = useSearchParams();
  const selectedId = params.get('resume');
  const resumes = useResource<Resume[]>('/resumes', 5000);
  const select = (id: string) => setParams({ resume: id });
  return <>
    <div className="interview-heading"><div><span className="eyebrow">YOUR INTERVIEW ROOM</span><h1>模拟面试室</h1><div className="inline-meta"><span className="live-indicator" />面试官已就绪 · 等待简历</div></div></div>
    <div className="interview-grid">
      <section className="card conversation-card">
        <div className="conversation-title"><span className="eyebrow">THE CONVERSATION</span><span className="small muted">模拟面试 · 简历收集</span></div>
        <div className="conversation room-conversation">
          <div className="message message-assistant"><span className="message-label">面试官</span><div className="message-body">你好，欢迎参加本次模拟面试。请先上传你的简历，或选择一份已有简历，我会围绕你的经历展开提问。</div></div>
          <div className="room-resume-upload"><ResumeUpload onUploaded={resume => { select(resume.id); void resumes.refresh() }} /></div>
          {resumes.error && <Notice>{resumes.error}<button className="text-button" onClick={() => void resumes.refresh()}>重新加载</button></Notice>}
          {!resumes.data && !resumes.error ? <Loading /> : !!resumes.data?.length && <details className="room-resume-library"><summary>选择已有简历</summary><div className="resume-list">{resumes.data.map(resume => <button type="button" className="resume-row" key={resume.id} aria-pressed={selectedId === resume.id} onClick={() => select(resume.id)}><Icon name="file" /><strong>{resume.filename}</strong><Status value={resume.status} />{selectedId === resume.id && <Icon name="check" />}</button>)}</div></details>}
          {selectedId && <RoomPreparation key={selectedId} id={selectedId} />}
        </div>
        <div className="answer-composer"><label htmlFor="room-answer">你的回答</label><textarea id="room-answer" rows={3} disabled placeholder="请先上传或选择简历，面试官出题后即可回答…" /><div className="composer-meta"><span className="small muted">简历准备完成后开始提问</span><button className="btn primary" disabled>提交回答<Icon name="arrow" size={16} /></button></div></div>
      </section>
      <aside className="interview-aside"><CameraPanel enabled={false} active={false} /><section className="card current-question"><span className="eyebrow">CURRENT STEP</span><h3>简历收集</h3><p className="muted">在对话区上传简历。准备好后，面试官会开始第一道问题。</p></section></aside>
    </div>
  </>;
}

function RoomPreparation({ id }: { id: string }) {
  const resume = useResource<Resume>(`/resumes/${id}`, 2500);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const lock = useRef(false);
  const navigate = useNavigate();
  const run = async (retry: boolean) => {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError('');
    try {
      if (retry) resume.setData(await api<Resume>(`/resumes/${id}/retry`, { method: 'POST' }));
      else {
        const interview = await api<Interview>('/interviews', { method: 'POST', body: { resume_id: id } });
        navigate(`/interviews/${interview.id}`, { state: { startInterview: true } });
      }
    } catch (err) { setError(errorMessage(err)) }
    finally { lock.current = false; setBusy(false) }
  };
  const ready = resume.data?.status === 'ready';
  return <section className="card recent-card" aria-live="polite"><h2>本次面试准备</h2>
    {resume.error && <Notice>{resume.error}<button className="text-button" onClick={() => void resume.refresh()}>重新加载</button></Notice>}
    {error && <Notice>{error}</Notice>}
    {!resume.data && !resume.error && <Loading text="正在读取面试简历…" />}
    {resume.data && <><p>{resume.data.filename} <Status value={resume.data.status} /></p>
      {resume.data.status === 'failed' ? <><Notice>{resume.data.error ?? '简历准备失败，请重试。'}</Notice><button className="btn" disabled={busy} onClick={() => void run(true)}>重新准备</button></> : ready ? <p className="muted">简历已准备就绪，开始后将根据简历生成面试题，并提出第一个问题。</p> : resume.data.status === 'ready' ? <Notice>暂无可用面试题，请重新上传简历。</Notice> : <Loading text="正在解析简历并整理改进建议，请稍候…" />}
    </>}
    <button className="btn primary" disabled={!ready || busy || !!resume.error} onClick={() => void run(false)}>{busy ? '正在准备…' : '开始模拟面试'}<Icon name="arrow" size={17} /></button>
  </section>;
}
