import { useState } from 'react';
import type { ReactNode } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
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
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');
  const retry = async () => {
    setBusy(true); setActionError('');
    try { setData(await api<Resume>(`/resumes/${id}/retry`, { method: 'POST' })) }
    catch (err) { setActionError(errorMessage(err)) }
    finally { setBusy(false) }
  };
  const discuss = async () => {
    setBusy(true); setActionError('');
    try {
      const chat = await api<{ id: string }>('/chats', { method: 'POST', body: { resume_id: id } });
      navigate(`/chat/${chat.id}`);
    } catch (err) { setActionError(errorMessage(err)) }
    finally { setBusy(false) }
  };
  const diagnose = async () => {
    setBusy(true); setActionError('');
    try { setData(await api<Resume>(`/resumes/${id}/diagnosis`, { method: 'POST' })) }
    catch (err) { setActionError(errorMessage(err)) }
    finally { setBusy(false) }
  };
  if (!data) return error ? <Notice>{error}<button className="text-button" onClick={() => void refresh()}>重新加载</button></Notice> : <Loading text="正在打开简历…" />;
  const diagnosis = data.diagnosis;
  const result = diagnosis?.status === 'ready' ? diagnosis.result : null;
  return <>
    <Link className="back-link" to="/resumes">← 返回简历诊断</Link>
    <div className="page-heading compact"><span className="eyebrow">GET TO KNOW YOUR STORY</span><h1>简历诊断</h1><p className="muted">{data.filename} <Status value={data.status} /> <DemoBadge demo={data.demo} /></p></div>
    {error && <Notice>{error}</Notice>}{actionError && <Notice>{actionError}</Notice>}
    {data.status === 'failed' ? <section className="card"><Notice>{data.error ?? '简历解析失败，请重试。'}</Notice><button className="btn primary" disabled={busy} onClick={() => void retry()}>重新解析</button></section>
      : data.status !== 'ready' ? <section className="card preparing"><Loading text={data.status === 'queued' ? '简历已加入解析队列…' : '正在提取简历中的经历和技能…'} /><p className="muted">解析完成后，可以选择开始诊断或直接模拟面试。</p></section>
        : <>
          <div className="prep-grid">
            <section className="card score-card"><span className="eyebrow">RESUME REVIEW</span><h2>简历诊断</h2>
              {result ? <><div className="score-display">{result.resume_score}<span>/ 100</span></div><h3>简历评分</h3><DemoBadge demo={result.demo} /><ul className="suggestions">{result.suggestions.map((suggestion, index) => <li key={index}><Icon name="spark" size={16} /><span>{suggestion}</span></li>)}</ul></>
                : diagnosis?.status === 'queued' || diagnosis?.status === 'processing' ? <Loading text="正在评估简历并整理改进建议，期间可以正常开始模拟面试…" />
                  : <>{diagnosis?.status === 'failed' ? <Notice>{diagnosis.error ?? '诊断失败，可以单独重试，不影响模拟面试。'}</Notice> : <p className="muted">简历已解析。按需生成评分和改进建议，也可以直接开始模拟面试。</p>}<button className="btn primary" disabled={busy} onClick={() => void diagnose()}>{diagnosis?.status === 'failed' ? '重试诊断' : '开始诊断'}</button></>}
            </section>
            <section className="card"><span className="eyebrow">YOUR NEXT STEP</span><h2>选择你的下一步</h2><p className="muted">模拟面试会围绕解析后的经历提问，随时可以开始。</p><Link className="btn primary" to={`/interviews/new?resume=${encodeURIComponent(id)}`}>用这份简历练习<Icon name="arrow" size={17} /></Link><p className="muted">也可以和 AI 讨论简历；已有诊断结果会一并带入对话。</p><button className="btn" disabled={busy} onClick={() => void discuss()}>和 AI 讨论改进建议</button></section>
          </div>
          <section className="card parsed-resume"><div className="section-heading"><div><span className="eyebrow">YOUR RESUME, ORGANIZED</span><h2>解析后的经历</h2></div></div>{data.parsed ? <ParsedValue value={data.parsed} /> : <p className="muted">暂无结构化解析内容。</p>}</section>
        </>}
  </>;
}
