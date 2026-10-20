import { useState } from 'react';
import type { ReactNode } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, errorMessage } from './api';
import { useResource } from './hooks';
import type { Interview, Resume } from './types';
import { DemoBadge, Icon, Loading, Notice, Status } from './ui';

const fieldNames: Record<string, string> = { name: '姓名', contact: '联系方式', email: '邮箱', phone: '电话', education: '教育经历', work_experience: '工作经历', experience: '工作经历', projects: '项目经历', skills: '技能', summary: '个人概述', basic_info: '基本信息', personal_info: '个人信息', school: '学校', university: '院校', company: '公司', major: '专业', degree: '学历', title: '名称 / 职位', position: '职位', description: '描述', responsibilities: '职责', achievements: '成果', start_date: '开始时间', end_date: '结束时间', duration: '时间', technologies: '技术栈', highlights: '亮点', certifications: '证书', awards: '荣誉', target_position: '目标职位', raw_text: '简历正文' };
function ParsedValue({ value }: { value: unknown }): ReactNode {
  if (value === null || value === undefined || value === '') return <span className="muted">未提供</span>;
  if (Array.isArray(value)) return <div className="parsed-items">{value.map((item, index) => <div key={index}><ParsedValue value={item} /></div>)}</div>;
  if (typeof value === 'object') return <dl className="parsed-fields">{Object.entries(value).map(([key, item]) => <div key={key}><dt>{fieldNames[key] ?? key}</dt><dd><ParsedValue value={item} /></dd></div>)}</dl>;
  return <span>{String(value)}</span>;
}

export function PreparationPage() {
  const { id = '' } = useParams();
  const { data, error, refresh, setData } = useResource<Resume>(`/resumes/${id}`, 2500);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');
  const navigate = useNavigate();
  const start = async () => {
    setBusy(true); setActionError('');
    try { const interview = await api<Interview>('/interviews', { method: 'POST', body: { resume_id: id } }); navigate(`/interviews/${interview.id}`) }
    catch (err) { setActionError(errorMessage(err)); setBusy(false) }
  };
  const retry = async () => {
    setBusy(true); setActionError('');
    try { setData(await api<Resume>(`/resumes/${id}/retry`, { method: 'POST' })) }
    catch (err) { setActionError(errorMessage(err)) }
    finally { setBusy(false) }
  };
  if (!data) return error ? <Notice>{error}<button className="text-button" onClick={() => void refresh()}>重新加载</button></Notice> : <Loading text="正在打开简历…" />;
  const preparation = data.preparation;
  return <><Link className="back-link" to="/">← 返回工作台</Link><div className="page-heading compact"><span className="eyebrow">GET TO KNOW YOUR STORY</span><h1>从你的经历，开始准备。</h1><p className="muted">{data.filename} <Status value={data.status} /></p></div>{error && <Notice>{error}</Notice>}{actionError && <Notice>{actionError}</Notice>}{data.status === 'failed' ? <section className="card"><Notice>{data.error ?? '简历准备失败，请重试。'}</Notice><button className="btn primary" disabled={busy} onClick={() => void retry()}>重新准备</button></section> : data.status !== 'ready' ? <section className="card preparing"><Loading text={data.status === 'queued' ? '简历已加入准备队列…' : '正在解析简历、评分并准备面试题…'} /><p className="muted">这可能需要一点时间。你可以离开页面，稍后在工作台继续。</p><div className="preparing-steps"><span>解析经历</span><i /><span>评估简历</span><i /><span>生成专属问题</span></div></section> : <><div className="prep-grid"><section className="card score-card"><span className="eyebrow">RESUME REVIEW</span><div className="score-display">{preparation?.resume_score ?? '—'}<span>/ 100</span></div><h2>简历准备度</h2><DemoBadge demo={preparation?.demo} /><p className="muted">围绕内容完整性、经历表达与岗位相关性，为你的准备提供参考。</p><ul className="suggestions">{preparation?.suggestions?.map((suggestion, index) => <li key={index}><Icon name="spark" size={16} /><span>{suggestion}</span></li>)}</ul></section><section className="card"><div className="section-heading"><div><span className="eyebrow">YOUR PRACTICE PLAN</span><h2>专属面试题</h2></div><span className="badge">{preparation?.questions?.length ?? 0} 道主问题</span></div><p className="muted small">面试官会根据你的回答进一步追问。先梳理思路，再开始对话。</p><ol className="question-plan">{preparation?.questions?.map((question, index) => <li key={question.id}><span>{String(index + 1).padStart(2, '0')}</span><div>{question.category && <small>{question.category}</small>}<p>{question.text}</p></div></li>)}</ol><div className="card-bottom"><span className="muted small">准备好后，你可以随时开始。</span><button className="btn primary" disabled={busy || !preparation?.questions?.length} onClick={() => void start()}>{busy ? '正在创建…' : '进入模拟面试'}<Icon name="arrow" size={17} /></button></div></section></div><section className="card parsed-resume"><div className="section-heading"><div><span className="eyebrow">YOUR RESUME, ORGANIZED</span><h2>解析后的经历</h2></div></div>{data.parsed ? <ParsedValue value={data.parsed} /> : <p className="muted">暂无结构化解析内容。</p>}</section></>}</>;
}
