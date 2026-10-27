import { useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Link, Navigate } from 'react-router-dom';
import { api, ApiError, errorMessage, requestId } from './api';
import { useAuth } from './auth';
import { useCommunityResource } from './community-api';
import { CommunityModal } from './CommunityModal';
import { reportReasons } from './community-data';
import type { CommunityReport, ReportPage, ReportReason, ReportStatus, ReportTarget } from './community-data';
import { Empty, Loading, Notice } from './ui';
import { Select } from './Select';
import './community.css';

const statuses: Record<ReportStatus, string> = { pending: '待审核', resolved: '已下架', dismissed: '已驳回' };
const targetStates = { visible: '当前可见', hidden: '已隐藏', deleted: '已删除' };
const dateTime = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });

export function ReportDialog({ target, onClose, onSubmitted }: { target: ReportTarget; onClose: () => void; onSubmitted: (report: CommunityReport) => void }) {
  const [reason, setReason] = useState<ReportReason>('spam');
  const [details, setDetails] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const request = useRef<{ signature: string; id: string } | null>(null);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (pending.current) return;
    const body = { targetType: target.type, targetId: target.id, reason, details: details.trim() };
    const signature = JSON.stringify(body);
    if (request.current?.signature !== signature) request.current = { signature, id: requestId() };
    pending.current = true; setBusy(true); setError('');
    try { onSubmitted(await api<CommunityReport>('/community/reports', { method: 'POST', body: { ...body, id: request.current.id } })) }
    catch (err) { setError(errorMessage(err)) }
    finally { pending.current = false; setBusy(false) }
  };
  return <CommunityModal title={target.type === 'post' ? '举报面经' : '举报评论'} className="community-report-dialog" busy={busy} onClose={onClose}>
    <form className="community-report-form" onSubmit={event => void submit(event)}><fieldset disabled={busy}>
      <p className="community-report-target">{target.title}</p><p className="muted small">请选择原因并补充具体问题，管理员审核后会在「我的举报」中反馈结果。</p>
      <label>举报原因<Select aria-label="举报原因" value={reason} onValueChange={value => setReason(value as ReportReason)} options={Object.entries(reportReasons).map(([value, label]) => ({ value, label }))} /></label>
      <label htmlFor="community-report-details">补充说明<span>选填，最多 1,000 字</span></label><textarea data-community-focus id="community-report-details" rows={5} maxLength={1000} value={details} onChange={event => setDetails(event.target.value)} placeholder="描述具体问题，方便管理员核实。" />
      {error && <Notice>{error}</Notice>}<footer><button type="button" className="btn" onClick={onClose}>取消</button><button type="submit" className="btn primary">{busy ? '正在提交…' : '提交举报'}</button></footer>
    </fieldset></form>
  </CommunityModal>;
}

function ReportSummary({ report, admin = false }: { report: CommunityReport; admin?: boolean }) {
  return <>
    <div className="community-report-card-heading"><span className={`badge ${report.status === 'resolved' ? 'badge-green' : ''}`}>{statuses[report.status]}</span><span>{report.targetType === 'post' ? '面经' : '评论'} · {targetStates[report.targetState]}</span><time dateTime={report.createdAt}>{dateTime(report.createdAt)}</time></div>
    <h2>{report.targetTitle}</h2><p><strong>举报原因：</strong>{reportReasons[report.reason]}</p>{report.details && <p className="community-report-text">{report.details}</p>}
    {admin && <p className="muted small">举报人：{report.reporter}</p>}
    <details><summary>查看举报时的内容</summary><div className="community-report-evidence">{report.targetContent}</div></details>
    {report.reviewedAt && <div className="community-report-result"><strong>{statuses[report.status]} · {dateTime(report.reviewedAt)}</strong><p className="community-report-text">{report.reviewNote}</p>{admin && <small>审核人：{report.reviewer ?? '—'}</small>}</div>}
    {report.targetState === 'visible' && <Link className="text-button" to={`/community?post=${encodeURIComponent(report.postId)}`}>查看当前内容</Link>}
  </>;
}

function ReportPagination({ data, onChange, busy = false }: { data: ReportPage; onChange: (page: number) => void; busy?: boolean }) {
  const pages = Math.max(1, Math.ceil(data.total / data.pageSize));
  return <nav className="community-report-pagination" aria-label="举报分页"><span>共 {data.total} 条，第 {data.page} / {pages} 页</span><button type="button" className="btn" disabled={busy || data.page <= 1} onClick={() => onChange(data.page - 1)}>上一页</button><button type="button" className="btn" disabled={busy || data.page >= pages} onClick={() => onChange(data.page + 1)}>下一页</button></nav>;
}

export function CommunityReportsPage() {
  const user = useAuth(state => state.user);
  return user ? <MyReports key={user.id} /> : null;
}

function MyReports() {
  const [page, setPage] = useState(1);
  const reports = useCommunityResource<ReportPage>(`/community/reports/mine?page=${page}`);
  return <div className="community-reports-page"><div className="page-heading compact"><h1>我的举报</h1><p className="muted">查看提交记录、审核进度和处理说明。</p><Link className="text-button" to="/community">返回面经社区</Link></div>
    {reports.error && <Notice>{reports.error}<button type="button" className="text-button" onClick={reports.refresh}>重新加载</button></Notice>}
    {reports.loading && !reports.data && <Loading text="正在加载举报记录…" />}
    {reports.data?.items.map(report => <article className="card community-report-card" key={report.id}><ReportSummary report={report} /></article>)}
    {reports.data && !reports.data.total && <Empty title="暂无举报记录">提交举报后，可在这里查看处理结果。</Empty>}
    {reports.data && reports.data.total > 0 && <ReportPagination data={reports.data} onChange={setPage} />}
  </div>;
}

export function CommunityModerationPage() {
  const user = useAuth(state => state.user);
  return user?.role === 'admin' ? <ModerationWorkspace key={user.id} /> : <Navigate to="/community" replace />;
}

function ModerationWorkspace() {
  const [status, setStatus] = useState<ReportStatus | 'all'>('pending');
  const [page, setPage] = useState(1);
  const reports = useCommunityResource<ReportPage>(`/community/admin/reports?status=${status}&page=${page}`);
  const [review, setReview] = useState<{ report: CommunityReport; action: 'hide' | 'dismiss' } | null>(null);
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const startReview = (report: CommunityReport, action: 'hide' | 'dismiss') => { setReview({ report, action }); setNote(''); setError(''); setFeedback('') };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!review || pending.current) return;
    if (!note.trim()) { setError('请填写审核说明。'); return }
    pending.current = true; setBusy(true); setError('');
    try {
      await api<CommunityReport>(`/community/admin/reports/${review.report.id}/review`, { method: 'POST', body: { action: review.action, note: note.trim() } });
      setReview(null); setFeedback(review.action === 'hide' ? '内容已下架，审核结果已记录。' : '举报已驳回，审核结果已记录。'); reports.refresh();
    } catch (err) {
      setError(errorMessage(err));
      if (err instanceof ApiError && err.status === 409) { setReview(null); reports.refresh() }
    } finally { pending.current = false; setBusy(false) }
  };
  return <div className="community-reports-page"><div className="page-heading compact"><h1>社区举报与审核</h1><p className="muted">核实举报时的内容，填写处理说明后下架内容或驳回举报。</p><Link className="text-button" to="/community">返回面经社区</Link></div>
    <div className="community-review-tabs" role="group" aria-label="审核状态">{(['pending', 'resolved', 'dismissed', 'all'] as const).map(value => <button type="button" className={`btn${status === value ? ' primary' : ''}`} aria-pressed={status === value} key={value} disabled={busy} onClick={() => { setStatus(value); setPage(1); setError(''); setFeedback('') }}>{value === 'all' ? '全部举报' : statuses[value]}</button>)}<button type="button" className="text-button" disabled={busy} onClick={reports.refresh}>刷新</button></div>
    {feedback && <Notice tone="success">{feedback}</Notice>}{(reports.error || (error && !review)) && <Notice>{reports.error || error}<button type="button" className="text-button" onClick={reports.refresh}>重新加载</button></Notice>}
    {reports.loading && !reports.data && <Loading text="正在加载举报队列…" />}
    {reports.data?.items.map(report => <article className="card community-report-card" key={report.id}><ReportSummary report={report} admin />{report.status === 'pending' && <div className="community-review-actions"><button type="button" className="btn danger" disabled={busy || report.targetState === 'deleted'} onClick={() => startReview(report, 'hide')}>下架内容</button><button type="button" className="btn" disabled={busy} onClick={() => startReview(report, 'dismiss')}>驳回举报</button>{report.targetState === 'deleted' && <span className="muted small">原内容已删除，仍可审核举报并填写说明。</span>}</div>}</article>)}
    {reports.data && !reports.data.total && <Empty title="暂无相关举报">新的举报会出现在待审核列表中。</Empty>}
    {reports.data && reports.data.total > 0 && <ReportPagination data={reports.data} onChange={setPage} busy={busy} />}
    {review && <CommunityModal title={review.action === 'hide' ? '下架违规内容' : '驳回举报'} className="community-report-dialog" busy={busy} onClose={() => { setReview(null); setError('') }}><form className="community-report-form" onSubmit={event => void submit(event)}><fieldset disabled={busy}>
      <p className="community-report-target">{review.report.targetTitle}</p><p className="muted small">{review.action === 'hide' ? '确认后内容将从社区隐藏，举报人可查看审核说明。' : '确认后保留内容，举报人可查看驳回说明。'}</p>
      <label htmlFor="community-review-note">审核说明<span>必填，最多 1,000 字</span></label><textarea data-community-focus id="community-review-note" required maxLength={1000} rows={5} value={note} onChange={event => setNote(event.target.value)} placeholder="说明核实结果与处理依据。" />
      {error && <Notice>{error}</Notice>}<footer><button type="button" className="btn" onClick={() => { setReview(null); setError('') }}>取消</button><button type="submit" className="btn primary">{busy ? '正在处理…' : review.action === 'hide' ? '确认下架' : '确认驳回'}</button></footer>
    </fieldset></form></CommunityModal>}
  </div>;
}
