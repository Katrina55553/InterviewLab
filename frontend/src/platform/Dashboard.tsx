import { useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, errorMessage } from './api';
import { useResource } from './hooks';
import { formatDate } from './format';
import type { Interview, Resume } from './types';
import { Empty, Icon, Loading, Notice, Status } from './ui';

export function Dashboard() {
  const resumes = useResource<Resume[]>('/resumes', 5000);
  const interviews = useResource<Interview[]>('/interviews', 5000);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const upload = async (file?: File) => {
    if (!file || busy) return;
    const extension = file.name.split('.').pop()?.toLowerCase();
    if (!['pdf', 'docx', 'txt'].includes(extension ?? '')) { setError('请选择 PDF、DOCX 或 TXT 简历文件。'); return }
    if (file.size > 10 * 1024 * 1024) { setError('文件大小不能超过 10 MB。'); return }
    setBusy(true); setError('');
    const form = new FormData(); form.append('file', file);
    try { const resume = await api<Resume>('/resumes', { method: 'POST', body: form }); navigate(`/resumes/${resume.id}`) }
    catch (err) { setError(errorMessage(err)) }
    finally { setBusy(false); if (input.current) input.current.value = '' }
  };
  const completed = interviews.data?.filter(item => item.status === 'completed').length ?? 0;
  return <><section className="dashboard-heading"><div><span className="eyebrow">A LITTLE BETTER, EVERY SESSION</span><h1>下一份机会，<br /><em>从充分准备开始。</em></h1><p className="muted">让简历成为对话的起点，让每一次练习都有收获。</p></div><div className="hero-note"><span className="note-number">“</span><p>好的回答，<br />来自认真梳理过的经历。</p><span className="small muted">YOUR INTERVIEW JOURNAL</span></div></section><section className="stat-row"><div><Icon name="file" /><strong>{resumes.data?.length ?? '—'}</strong><span>我的简历</span></div><div><Icon name="chat" /><strong>{interviews.data?.length ?? '—'}</strong><span>面试练习</span></div><div><Icon name="check" /><strong>{completed}</strong><span>完成复盘</span></div><Link to="/chat" className="stat-action">和 AI 一起梳理思路<Icon name="arrow" /></Link></section><div className="dashboard-grid"><section className="card upload-card"><div className="section-heading"><div><span className="eyebrow">START SOMETHING GOOD</span><h2>开启新的练习</h2></div><span className="step-index">01</span></div><input ref={input} className="sr-only" type="file" accept=".pdf,.docx,.txt" aria-label="上传简历文件" onChange={event => void upload(event.target.files?.[0])} /><button className={`dropzone ${dragging ? 'dragging' : ''}`} disabled={busy} onClick={() => input.current?.click()} onDragOver={event => { event.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); void upload(event.dataTransfer.files[0]) }}><span className="upload-icon"><Icon name="upload" size={26} /></span><strong>{busy ? '正在上传你的简历…' : '将简历拖到这里'}</strong><span>{busy ? '上传完成后将自动解析并准备题目' : '或点击选择文件'}</span><small>PDF / DOCX / TXT · 最大 10 MB</small></button>{error && <Notice>{error}</Notice>}<div className="flow-mini"><span><b>1</b>解析与评分</span><i /><span><b>2</b>专属题目</span><i /><span><b>3</b>面试与复盘</span></div></section><section className="card"><div className="section-heading"><div><span className="eyebrow">YOUR MATERIALS</span><h2>最近的简历</h2></div><span className="small muted">保存在你的账号中</span></div>{resumes.error && <Notice>{resumes.error}</Notice>}{!resumes.data && !resumes.error ? <Loading /> : !resumes.data?.length ? <Empty title="一份好简历，一场好对话">上传第一份简历，开始准备。</Empty> : <div className="resume-list">{resumes.data.slice(0, 5).map(resume => <Link className="resume-row" key={resume.id} to={`/resumes/${resume.id}`}><span className="file-icon"><Icon name="file" /></span><div><strong>{resume.filename}</strong><small>{formatDate(resume.created_at)}</small></div><Status value={resume.status} /><Icon name="arrow" size={16} /></Link>)}</div>}</section></div><section className="card recent-card"><div className="section-heading"><div><span className="eyebrow">KEEP THE MOMENTUM</span><h2>最近的面试</h2></div><Link className="text-button" to="/history">全部记录 <Icon name="arrow" size={16} /></Link></div>{interviews.error && <Notice>{interviews.error}</Notice>}<InterviewList items={interviews.data?.slice(0, 4) ?? []} loading={!interviews.data && !interviews.error} /></section></>;
}

export function InterviewList({ items, loading = false }: { items: Interview[]; loading?: boolean }) {
  if (loading) return <Loading />;
  if (!items.length) return <Empty title="还没有面试记录">完成简历准备后，开启第一次模拟面试。</Empty>;
  return <div className="interview-list">{items.map((item, index) => <Link className="interview-row" key={item.id} to={item.status === 'completed' ? `/interviews/${item.id}/report` : `/interviews/${item.id}`}><span className="row-number">{String(index + 1).padStart(2, '0')}</span><div><strong>模拟面试 <span className="mono muted">#{item.id.slice(0, 6)}</span></strong><small>{formatDate(item.created_at)}</small></div><Status value={item.status} /><span className="row-action">{item.status === 'completed' ? '查看报告' : '继续练习'}<Icon name="arrow" size={16} /></span></Link>)}</div>;
}

export function HistoryPage() {
  const interviews = useResource<Interview[]>('/interviews', 5000);
  const [filter, setFilter] = useState('all');
  const items = interviews.data?.filter(item => filter === 'all' || (filter === 'completed' ? item.status === 'completed' : item.status !== 'completed')) ?? [];
  return <><div className="page-heading"><span className="eyebrow">YOUR INTERVIEW JOURNAL</span><h1>每一次练习，都算数。</h1><p className="muted">回到你的回答，发现变化，积累下一次的自信。</p></div><section className="card"><div className="section-heading"><div className="tabs" aria-label="面试记录筛选">{[['all', '全部记录'], ['active', '未完成'], ['completed', '已完成']].map(([key, label]) => <button key={key} className={filter === key ? 'active' : ''} onClick={() => setFilter(key)}>{label}</button>)}</div><button className="text-button" onClick={() => void interviews.refresh()}>刷新</button></div>{interviews.error && <Notice>{interviews.error}</Notice>}<InterviewList items={items} loading={!interviews.data && !interviews.error} /></section></>;
}
