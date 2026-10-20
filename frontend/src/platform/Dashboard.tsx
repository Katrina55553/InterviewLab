import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ResumeUpload } from './ResumeUpload';
import { useResource } from './hooks';
import { formatDate } from './format';
import type { Interview, Resume } from './types';
import { Empty, Icon, Loading, Notice, Status } from './ui';

export function Dashboard() {
  const resumes = useResource<Resume[]>('/resumes', 5000);
  const interviews = useResource<Interview[]>('/interviews', 5000);
  const navigate = useNavigate();
  const completed = interviews.data?.filter(item => item.status === 'completed').length ?? 0;
  return <><section className="dashboard-heading"><div><span className="eyebrow">A LITTLE BETTER, EVERY SESSION</span><h1>下一份机会，<br /><em>从充分准备开始。</em></h1><p className="muted">让简历成为对话的起点，让每一次练习都有收获。</p></div><div className="hero-note"><span className="note-number">“</span><p>好的回答，<br />来自认真梳理过的经历。</p><span className="small muted">YOUR INTERVIEW JOURNAL</span></div></section><section className="stat-row"><div><Icon name="file" /><strong>{resumes.data?.length ?? '—'}</strong><span>我的简历</span></div><div><Icon name="chat" /><strong>{interviews.data?.length ?? '—'}</strong><span>面试练习</span></div><div><Icon name="check" /><strong>{completed}</strong><span>完成复盘</span></div><Link to="/chat" className="stat-action">和 AI 一起梳理思路<Icon name="arrow" /></Link></section><section className="card recent-card"><div className="section-heading"><div><span className="eyebrow">YOUR INTERVIEW ROOM</span><h2>模拟面试</h2><p className="muted">先进入面试室，再上传或选择简历，准备好后开始对话。</p></div><Link className="btn primary" to="/interviews/new">进入面试室<Icon name="arrow" size={17} /></Link></div></section><div className="dashboard-grid"><section className="card upload-card"><div className="section-heading"><div><span className="eyebrow">START SOMETHING GOOD</span><h2>简历诊断</h2></div><span className="step-index">01</span></div><ResumeUpload onUploaded={resume => navigate(`/resumes/${resume.id}`)} /><div className="flow-mini"><span><b>1</b>解析与评分</span><i /><span><b>2</b>发现问题</span><i /><span><b>3</b>改进建议</span></div></section><section className="card"><div className="section-heading"><div><span className="eyebrow">YOUR MATERIALS</span><h2>最近的简历</h2></div><span className="small muted">保存在你的账号中</span></div>{resumes.error && <Notice>{resumes.error}</Notice>}{!resumes.data && !resumes.error ? <Loading /> : !resumes.data?.length ? <Empty title="一份好简历，一场好对话">上传第一份简历，开始准备。</Empty> : <div className="resume-list">{resumes.data.slice(0, 5).map(resume => <Link className="resume-row" key={resume.id} to={`/resumes/${resume.id}`}><span className="file-icon"><Icon name="file" /></span><div><strong>{resume.filename}</strong><small>{formatDate(resume.created_at)}</small></div><Status value={resume.status} /><Icon name="arrow" size={16} /></Link>)}</div>}</section></div><section className="card recent-card"><div className="section-heading"><div><span className="eyebrow">KEEP THE MOMENTUM</span><h2>最近的面试</h2></div><Link className="text-button" to="/history">全部记录 <Icon name="arrow" size={16} /></Link></div>{interviews.error && <Notice>{interviews.error}</Notice>}<InterviewList items={interviews.data?.slice(0, 4) ?? []} loading={!interviews.data && !interviews.error} /></section></>;
}

export function InterviewList({ items, loading = false }: { items: Interview[]; loading?: boolean }) {
  if (loading) return <Loading />;
  if (!items.length) return <Empty title="还没有面试记录">进入面试室，上传或选择简历，开启第一次模拟面试。</Empty>;
  return <div className="interview-list">{items.map((item, index) => <Link className="interview-row" key={item.id} to={item.status === 'completed' ? `/interviews/${item.id}/report` : `/interviews/${item.id}`}><span className="row-number">{String(index + 1).padStart(2, '0')}</span><div><strong>模拟面试 <span className="mono muted">#{item.id.slice(0, 6)}</span></strong><small>{formatDate(item.created_at)}</small></div><Status value={item.status} /><span className="row-action">{item.status === 'completed' ? '查看报告' : '继续练习'}<Icon name="arrow" size={16} /></span></Link>)}</div>;
}

export function HistoryPage() {
  const interviews = useResource<Interview[]>('/interviews', 5000);
  const [filter, setFilter] = useState('all');
  const items = interviews.data?.filter(item => filter === 'all' || (filter === 'completed' ? item.status === 'completed' : item.status !== 'completed')) ?? [];
  return <><div className="page-heading"><span className="eyebrow">YOUR INTERVIEW JOURNAL</span><h1>每一次练习，都算数。</h1><p className="muted">回到你的回答，发现变化，积累下一次的自信。</p></div><section className="card"><div className="section-heading"><div className="tabs" aria-label="面试记录筛选">{[['all', '全部记录'], ['active', '未完成'], ['completed', '已完成']].map(([key, label]) => <button key={key} className={filter === key ? 'active' : ''} onClick={() => setFilter(key)}>{label}</button>)}</div><button className="text-button" onClick={() => void interviews.refresh()}>刷新</button></div>{interviews.error && <Notice>{interviews.error}</Notice>}<InterviewList items={items} loading={!interviews.data && !interviews.error} /></section></>;
}
