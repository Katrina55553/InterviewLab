import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import { api, ApiError, errorMessage, requestId } from './api';
import { CameraPanel } from './Camera';
import { newerSnapshot, useInterview, useResource } from './hooks';
import { useSpeechInput, useSpeechOutput } from './media';
import type { Capabilities, Interview, InterviewCommand } from './types';
import { DemoBadge, Icon, Loading, Notice, RichText, Status } from './ui';

function restoreCommand(id: string): InterviewCommand | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(`meeting:command:${id}`) ?? 'null');
    return value && typeof value.request_id === 'string' && typeof value.version === 'number' && ['start', 'answer', 'skip', 'rephrase', 'end'].includes(value.type) ? value : null;
  } catch { return null }
}

export function InterviewPage() {
  const { id = '' } = useParams();
  return <InterviewSession key={id} id={id} />;
}

function InterviewSession({ id }: { id: string }) {
  const location = useLocation();
  const navigate = useNavigate();
  const autoStarted = useRef(false);
  const { data, error, refresh, setData, connection } = useInterview(id);
  const capabilities = useResource<Capabilities>(`/media/capabilities?interview_id=${id}`);
  const [answer, setAnswer] = useState('');
  const [command, setCommand] = useState<InterviewCommand | null>(() => restoreCommand(id));
  const [sending, setSending] = useState(false);
  const [actionError, setActionError] = useState('');
  const [confirmEnd, setConfirmEnd] = useState(false);
  const lock = useRef(false);
  const transcriptPrefix = useRef('');
  const draftEdited = useRef(false);
  const scrollEnd = useRef<HTMLDivElement>(null);
  const speech = useSpeechInput(id, text => {
    if (!draftEdited.current) setAnswer(`${transcriptPrefix.current}${transcriptPrefix.current ? '\n' : ''}${text}`);
  });
  const output = useSpeechOutput(id);
  const { stop: stopSpeech } = speech;
  const { stop: stopOutput } = output;
  useEffect(() => () => { stopSpeech(false); stopOutput() }, [data?.current_question?.id, stopSpeech, stopOutput]);
  const done = ['completed', 'reporting'].includes(data?.status ?? '');
  const terminalCommand = ['failed', 'unknown'].includes(data?.pending_command?.status ?? '');
  const serverPending = data?.pending_command?.status === 'running';
  const commandPending = !!command && (data?.version ?? 0) <= command.version && !terminalCommand;
  const busy = sending || serverPending || commandPending;
  useEffect(() => { scrollEnd.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [data?.messages?.length]);
  useEffect(() => {
    if (command && data && data.version > command.version) {
      sessionStorage.removeItem(`meeting:command:${id}`);
      const timer = window.setTimeout(() => { if (command.type === 'answer') setAnswer(''); setCommand(null); setActionError('') }, 0);
      return () => window.clearTimeout(timer);
    }
  }, [command, data, id]);
  const submit = useCallback(async (type: InterviewCommand['type']) => {
    if (!data || lock.current || busy) return;
    if (type === 'answer' && !answer.trim()) return;
    stopSpeech(false); stopOutput(); setConfirmEnd(false);
    const payload: InterviewCommand = { request_id: requestId(), type, expected_question_id: data.current_question?.id ?? null, version: data.version, ...(type === 'answer' ? { content: answer.trim() } : {}) };
    lock.current = true; setSending(true); setActionError(''); setCommand(payload);
    sessionStorage.setItem(`meeting:command:${id}`, JSON.stringify(payload));
    try {
      const result = await api<Interview>(`/interviews/${id}/commands`, { method: 'POST', body: payload });
      setData(current => newerSnapshot(current, result));
      if (!result.pending_command) { setCommand(null); sessionStorage.removeItem(`meeting:command:${id}`) }
      if (type === 'answer' && result.version > payload.version) setAnswer('');
    } catch (err) {
      if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
        setCommand(null); sessionStorage.removeItem(`meeting:command:${id}`);
        setActionError(errorMessage(err));
      } else setActionError('请求结果尚未确认。正在同步服务器状态，请保留当前页面；系统不会重复提交答案。');
      void refresh();
    } finally { lock.current = false; setSending(false) }
  }, [data, busy, answer, stopSpeech, stopOutput, id, setData, refresh]);
  useEffect(() => {
    if (location.state?.startInterview !== true || autoStarted.current || !data) return;
    const timer = window.setTimeout(() => {
      autoStarted.current = true;
      // Consume the entry action before sending: refreshing is a recovery read.
      navigate(location.pathname, { replace: true, state: null });
      if (!busy && !command && !data.pending_command && data.status === 'ready' && !data.messages.length) void submit('start');
    }, 0);
    return () => window.clearTimeout(timer);
  }, [location.state, location.pathname, navigate, data, busy, command, submit]);
  if (!data) return error ? <Notice>{error}<button className="text-button" onClick={() => void refresh()}>重新加载</button></Notice> : <Loading text="正在恢复你的面试…" />;
  const question = data.current_question;
  const latestEvaluation = data.evaluations?.at(-1);
  const recoverDraft = () => {
    const saved = data.pending_command?.content ?? command?.content;
    if (saved) setAnswer(saved);
    setCommand(null); setActionError(''); sessionStorage.removeItem(`meeting:command:${id}`);
  };
  return <><div className="interview-heading"><div><Link className="back-link" to="/history">← 面试记录</Link><h1>专注此刻，认真表达。</h1><div className="inline-meta"><Status value={data.status} /><span className="live-indicator" />{connection}<DemoBadge demo={data.demo} /></div></div><div className="interview-controls">{done ? <Link className="btn primary" to={`/interviews/${id}/report`}>查看面试报告<Icon name="arrow" size={16} /></Link> : <button className="btn subtle" disabled={busy} onClick={() => setConfirmEnd(true)}>结束并复盘</button>}</div></div>{error && <Notice>{error}</Notice>}{actionError && !terminalCommand && <Notice tone="info">{actionError}<button className="text-button" onClick={() => void refresh()}>刷新状态</button></Notice>}{data.error && <Notice>{data.error}</Notice>}{terminalCommand && <Notice tone="info">{data.pending_command?.status === 'unknown' ? '上次请求的结果无法确认。请检查对话记录，确认后可重新提交。' : `上次请求未完成：${data.pending_command?.error ?? '请稍后重试。'}`}<button className="text-button" onClick={recoverDraft}>恢复回答草稿</button></Notice>}{commandPending && !sending && !serverPending && <Notice tone="info">当前请求尚未得到服务器确认。请先刷新状态，确认本轮未完成后可恢复草稿继续操作。<button className="text-button" onClick={() => void refresh()}>同步服务器状态</button><button className="text-button" onClick={recoverDraft}>确认后恢复草稿</button></Notice>}{confirmEnd && <div className="card end-confirm" role="alertdialog" aria-label="结束本次面试"><div><strong>结束本次练习？</strong><p className="muted small">报告将根据已完成的回答生成，主动跳过的主问题记零分；提前结束时，未提问的问题不计分。</p></div><button className="btn" onClick={() => setConfirmEnd(false)}>继续练习</button><button className="btn primary" onClick={() => void submit('end')}>结束并生成报告</button></div>}<div className="interview-grid"><section className="card conversation-card"><div className="conversation-title"><span className="eyebrow">THE CONVERSATION</span><span className="small muted">文字 / 语音 · 随时保存进度</span></div><div className="conversation" aria-live="polite">{!data.messages?.length ? <div className="interview-welcome"><span className="welcome-icon"><Icon name="spark" size={30} /></span><h2>准备好分享你的经历了吗？</h2><p className="muted">面试官将围绕简历提问，并根据你的回答深入交流。<br />不用急，按自己的节奏来。</p><button className="btn primary" disabled={busy || done} onClick={() => void submit('start')}>{busy ? '正在准备首个问题…' : '开始模拟面试'}<Icon name="arrow" size={17} /></button></div> : data.messages.map(message => <div key={message.id} className={`message ${message.role === 'user' ? 'message-user' : 'message-assistant'}`}><span className="message-label">{message.role === 'user' ? '我' : '面试官'}</span><div className="message-body"><RichText text={message.content} /></div></div>)}{busy && <div className="thinking" role="status"><span className="spinner" />{command?.type === 'end' ? '正在准备报告…' : '面试官正在思考…'}</div>}{done && <Notice tone="success">{data.status === 'reporting' ? '本次练习已结束，正在整理你的面试报告。' : '本次练习已完成。到报告中看看这一次的收获。'}</Notice>}<div ref={scrollEnd} /></div>{!done && !!data.messages?.length && <div className="answer-composer"><label htmlFor="interview-answer">你的回答</label><textarea id="interview-answer" value={answer} maxLength={5000} onChange={event => { draftEdited.current = true; setAnswer(event.target.value) }} disabled={busy || speech.recording} placeholder="用具体的情境、你的行动和最终结果，讲清楚这段经历…" rows={5} /><div className="composer-meta"><div className="media-actions"><button className={`icon-button ${speech.recording ? 'recording' : ''}`} aria-label={speech.recording ? '结束语音输入' : '开始语音输入'} title={capabilities.data?.asr ? '语音识别后可编辑，再提交' : '语音转写尚未配置'} disabled={!capabilities.data?.asr || busy} onClick={() => { if (speech.recording) speech.stop(); else { draftEdited.current = false; transcriptPrefix.current = answer; void speech.start() } }}><Icon name="mic" size={18} />{speech.recording ? '结束录音' : '语音输入'}</button><span className="small muted">{speech.recording ? '正在转写…停止后可编辑' : `${answer.length} / 5000`}</span></div><button className="btn primary" disabled={busy || !answer.trim() || answer.length > 5000 || speech.recording || !question} onClick={() => void submit('answer')}>提交回答<Icon name="arrow" size={16} /></button></div>{answer.length > 5000 && <Notice>回答超过 5000 字，请停止录音后精简内容再提交。</Notice>}{speech.error && <Notice>{speech.error}</Notice>}<div className="answer-secondary"><button className="text-button muted" disabled={busy || !question} onClick={() => void submit('rephrase')}>换个问法</button><button className="text-button muted" disabled={busy || !question} onClick={() => void submit('skip')}>跳过这道题</button><span className="small muted">回答前，给自己一点思考时间。</span></div></div>}</section><aside className="interview-aside"><CameraPanel key={id} interviewId={id} active={!busy} enabled={!!capabilities.data?.vision} finished={done} /><section className="card current-question"><span className="eyebrow">STAY WITH THE QUESTION</span><div className="section-heading"><h3>{question?.is_followup ? '深入追问' : '当前问题'}</h3>{question?.main_index !== undefined && <span className="badge">第 {question.main_index + 1} 题</span>}</div><p>{question?.text ?? (done ? '练习结束，去看看你的报告吧。' : '开始面试后，问题将在这里显示。')}</p>{question && <button className="text-button" disabled={!capabilities.data?.tts} title={!capabilities.data?.tts ? '朗读服务尚未配置' : ''} onClick={() => output.speaking ? output.stop() : void output.speak(question.text)}><Icon name="volume" size={17} />{output.speaking ? '停止等待或朗读' : '朗读问题'}</button>}{output.error && <Notice>{output.error}</Notice>}</section>{latestEvaluation && <section className="card"><span className="eyebrow">A MOMENT OF REFLECTION</span><h3>上一轮反馈</h3><div className="evaluation-score">{typeof latestEvaluation.score === 'number' ? latestEvaluation.score : '—'}<small>/ 100</small></div><p className="small leading-relaxed">{typeof latestEvaluation.feedback === 'string' ? latestEvaluation.feedback : '反馈已保存，将在报告中汇总。'}</p></section>}<div className="practice-note"><Icon name="spark" size={18} /><p>把回答当作一次分享：<br />讲清背景、行动和结果，<br />比堆砌术语更有说服力。</p></div></aside></div></>;
}
