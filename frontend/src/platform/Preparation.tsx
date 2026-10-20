import { useState } from 'react';
import type { ReactNode } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, errorMessage } from './api';
import { useResource } from './hooks';
import type { Resume } from './types';
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
  const retry = async () => {
    setBusy(true); setActionError('');
    try { setData(await api<Resume>(`/resumes/${id}/retry`, { method: 'POST' })) }
    catch (err) { setActionError(errorMessage(err)) }
    finally { setBusy(false) }
  };
  if (!data) return error ? <Notice>{error}<button className="text-button" onClick={() => void refresh()}>重新加载</button></Notice> : <Loading text="正在打开简历…" />;
  const preparation = data.preparation;
  return <><Link className="back-link" to="/resumes">← 返回简历诊断</Link><div className="page-heading compact"><span className="eyebrow">GET TO KNOW YOUR STORY</span><h1>简历诊断</h1><p className="muted">{data.filename} <Status value={data.status} /></p></div>{error && <Notice>{error}</Notice>}{actionError && <Notice>{actionError}</Notice>}{data.status === 'failed' ? <section className="card"><Notice>{data.error ?? '简历准备失败，请重试。'}</Notice><button className="btn primary" disabled={busy} onClick={() => void retry()}>重新准备</button></section> : data.status !== 'ready' ? <section className="card preparing"><Loading text={data.status === 'queued' ? '简历已加入准备队列…' : '正在解析简历并生成诊断结果…'} /><p className="muted">这可能需要一点时间。你可以离开页面，稍后在简历诊断中继续。</p><div className="preparing-steps"><span>解析经历</span><i /><span>评估简历</span><i /><span>整理改进建议</span></div></section> : <><div className="prep-grid"><section className="card score-card"><span className="eyebrow">RESUME REVIEW</span><div className="score-display">{preparation?.resume_score ?? '—'}<span>/ 100</span></div><h2>简历评分</h2><DemoBadge demo={preparation?.demo} /><p className="muted">围绕内容完整性、经历表达与岗位相关性，为你的准备提供参考。</p><ul className="suggestions">{preparation?.suggestions?.map((suggestion, index) => <li key={index}><Icon name="spark" size={16} /><span>{suggestion}</span></li>)}</ul></section><section className="card"><span className="eyebrow">YOUR NEXT STEP</span><h2>按建议完善你的简历</h2><p className="muted">结合评分与解析内容，检查经历表达，补充具体行动和量化成果。</p><p className="muted">想练习如何讲清这些经历？可以带着这份简历进入面试室。</p><Link className="btn" to={`/interviews/new?resume=${encodeURIComponent(id)}`}>用这份简历练习<Icon name="arrow" size={17} /></Link></section></div><section className="card parsed-resume"><div className="section-heading"><div><span className="eyebrow">YOUR RESUME, ORGANIZED</span><h2>解析后的经历</h2></div></div>{data.parsed ? <ParsedValue value={data.parsed} /> : <p className="muted">暂无结构化解析内容。</p>}</section></>}</>;
}
