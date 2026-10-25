import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, errorMessage } from './api';
import { useResource } from './hooks';
import type { Interview, Report } from './types';
import { DemoBadge, Icon, Loading, Notice, RichText } from './ui';

const dimensions = [['professional', '专业能力'], ['project', '项目经验'], ['analysis', '分析能力'], ['logic', '逻辑表达'], ['completeness', '回答完整性']];
function RetryReport({ id, onDone }: { id: string; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return <><button className="btn" disabled={busy} onClick={async () => {
    setBusy(true); setError('');
    try { await api(`/interviews/${id}/report/retry`, { method: 'POST' }); onDone() }
    catch (err) { setError(errorMessage(err)) }
    finally { setBusy(false) }
  }}>{busy ? '正在重新生成…' : '重试生成报告'}</button>{error && <Notice>{error}</Notice>}</>;
}
function Radar({ values }: { values: Record<string, number | null> }) {
  const point = (index: number, scale: number) => { const angle = index * Math.PI * 2 / 5 - Math.PI / 2; return [150 + Math.cos(angle) * 95 * scale, 135 + Math.sin(angle) * 95 * scale] };
  const polygon = (scale: number) => dimensions.map((_, index) => point(index, scale).join(',')).join(' ');
  return <svg className="radar" viewBox="-12 0 324 280" role="img" aria-label={dimensions.map(([key, label]) => `${label} ${values[key] ?? '无数据'}`).join('，')}>{[0.25, 0.5, 0.75, 1].map(scale => <polygon key={scale} points={polygon(scale)} fill={scale === 1 ? '#faf8f5' : 'none'} stroke="#e7e5e4" />)}{dimensions.map((_, index) => <line key={index} x1={150} y1={135} x2={point(index, 1)[0]} y2={point(index, 1)[1]} stroke="#e7e5e4" />)}<polygon points={dimensions.map(([key], index) => point(index, Math.max(0, Math.min(100, values[key] ?? 0)) / 100).join(',')).join(' ')} fill="#04785725" stroke="#047857" strokeWidth="2" />{dimensions.map(([key, label], index) => <g key={key}><circle cx={point(index, Math.max(0, Math.min(100, values[key] ?? 0)) / 100)[0]} cy={point(index, Math.max(0, Math.min(100, values[key] ?? 0)) / 100)[1]} r="3" fill="#047857" /><text x={point(index, 1.33)[0]} y={point(index, 1.25)[1]} textAnchor="middle" fontSize="14" fill="#57534e">{label}</text></g>)}</svg>;
}
export function ReportPage() {
  const { id = '' } = useParams();
  const interview = useResource<Interview>(`/interviews/${id}`, 3000);
  const report = useResource<Report>(`/interviews/${id}/report`, 3000);
  const data = report.data ?? interview.data?.report;
  if (!data) return <><Link className="back-link" to={`/interviews/${id}`}>← 返回面试</Link><section className="card preparing"><Loading text="正在整理你的面试报告…" /><p className="muted">报告会保留每轮回答和真实评分，你可以稍后在面试记录中查看。</p>{(interview.error || (interview.data?.status === 'completed' && report.error) || interview.data?.error) && <Notice>{interview.error || interview.data?.error || report.error}<button className="text-button" onClick={() => { void interview.refresh(); void report.refresh() }}>重新加载</button></Notice>}{interview.data?.error && <RetryReport id={id} onDone={() => { void interview.refresh(); void report.refresh() }} />}</section></>;
  return <><Link className="back-link" to="/history">← 返回面试记录</Link><div className="page-heading compact report-heading"><div><span className="eyebrow">REFLECT. REFINE. REPEAT.</span><h1>看见优势，也看见下一步。</h1><p className="muted">一次练习，一份更清晰的自己。<DemoBadge demo={data.demo} /></p></div><Link className="btn primary" to="/">再开启一次练习<Icon name="arrow" size={16} /></Link></div><div className="report-grid"><section className="card report-score"><span className="eyebrow">YOUR INTERVIEW REVIEW</span><div className="score-display">{data.overall_score ?? '—'}<span>/ 100</span></div><h2>面试综合得分</h2>{data.overall_score === null && <p className="small muted">暂无已回答或主动跳过的主问题，不生成综合分数。</p>}<div className="report-counts"><div><strong>{data.completion?.answered ?? 0}</strong><span>已作答</span></div><div><strong>{data.completion?.skipped ?? 0}</strong><span>已跳过</span></div><div><strong>{data.completion?.total ?? 0}</strong><span>主问题总数</span></div></div><p className="small muted">主问题独立计分，主动跳过记零分，提前结束时未提问不计分。追问与仪态反馈单独展示。</p></section><section className="card"><div className="section-heading"><h2>能力画像</h2><span className="small muted">五个维度，全面复盘</span></div><Radar values={data.dimensions ?? {}} /><div className="dimension-scores">{dimensions.map(([key, label]) => <span key={key}>{label}<b>{data.dimensions?.[key] ?? '—'}</b></span>)}</div></section><section className="card summary-card"><span className="eyebrow">THE BIGGER PICTURE</span><h2>本次练习小结</h2><RichText text={data.summary ?? ''} /><h3>下次可以这样改进</h3><ul className="suggestions">{data.suggestions?.map((suggestion, index) => <li key={index}><Icon name="spark" size={16} /><span>{suggestion}</span></li>)}</ul></section></div><section className="card review-turns"><div className="section-heading"><div><span className="eyebrow">BACK TO THE DETAILS</span><h2>逐轮回放</h2></div><span className="badge">{data.turns?.length ?? 0} 轮对话</span></div>{!data.turns?.length ? <p className="muted">本次没有已完成的答题记录。</p> : data.turns.map((turn, index) => <details key={index} className="turn-review" open={index === 0}><summary><span className="row-number">{String(index + 1).padStart(2, '0')}</span><strong>{turn.question}</strong>{turn.is_followup && <span className="badge">追问</span>}<span className="turn-score">{turn.score ?? '—'}<small> / 100</small></span></summary><div className="turn-content"><span className="eyebrow">YOUR ANSWER</span><RichText text={turn.answer || '本题已跳过。'} /><div className="turn-feedback"><span className="eyebrow">FEEDBACK</span><RichText text={turn.feedback || '暂无反馈。'} /></div></div></details>)}</section></>;
}
