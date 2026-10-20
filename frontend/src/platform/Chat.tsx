import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, checkResponse, errorMessage, readSSE, requestHeaders, requestId } from './api';
import { useResource } from './hooks';
import type { Chat } from './types';
import { Empty, Icon, Loading, Notice, RichText } from './ui';

export function ChatPage() {
  const { id } = useParams();
  return <ChatWorkspace key={id ?? 'new'} id={id} />;
}

function ChatWorkspace({ id }: { id?: string }) {
  const chats = useResource<Chat[]>('/chats');
  const [current, setCurrent] = useState<Chat | null>(null);
  const [content, setContent] = useState('');
  const [streaming, setStreaming] = useState('');
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [attaching, setAttaching] = useState(false);
  const [error, setError] = useState('');
  const [attachment, setAttachment] = useState('');
  const controller = useRef<AbortController | null>(null);
  const lock = useRef(false);
  const uploadInput = useRef<HTMLInputElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  useEffect(() => {
    const abort = new AbortController();
    if (id) api<Chat>(`/chats/${id}`, { signal: abort.signal }).then(value => { if (!abort.signal.aborted) setCurrent(value) }).catch(err => { if (!abort.signal.aborted) setError(errorMessage(err)) });
    return () => { abort.abort(); controller.current?.abort() };
  }, [id]);
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [current?.messages?.length, streaming]);
  const create = async () => {
    setCreating(true); setError('');
    try { const chat = await api<Chat>('/chats', { method: 'POST', body: {} }); navigate(`/chat/${chat.id}`) }
    catch (err) { setError(errorMessage(err)) }
    finally { setCreating(false) }
  };
  const send = async () => {
    if (!id || !content.trim() || lock.current) return;
    const text = content.trim();
    lock.current = true; setBusy(true); setError(''); setStreaming(''); setContent('');
    const abort = new AbortController(); controller.current = abort;
    const temporaryId = requestId();
    setCurrent(value => value ? { ...value, messages: [...(value.messages ?? []), { id: temporaryId, role: 'user', content: text }] } : value);
    let finished = false;
    let streamError = '';
    try {
      const response = await checkResponse(await fetch(`/api/chats/${id}/messages`, { method: 'POST', credentials: 'include', headers: requestHeaders('POST'), body: JSON.stringify({ content: text, request_id: requestId() }), signal: abort.signal }));
      if (!response.body) throw new Error('未收到流式响应。');
      await readSSE(response.body, (event, data) => {
        const type = event === 'message' && typeof data.type === 'string' ? data.type : event;
        if (type === 'delta' && typeof data.content === 'string') setStreaming(previous => previous + data.content);
        if (type === 'done') finished = true;
        if (type === 'error') streamError = String(data.message ?? '对话生成失败。');
      });
      if (streamError) throw new Error(streamError);
      if (!finished) throw new Error('连接中断，回答可能仍在生成。请刷新对话确认结果。');
      const saved = await api<Chat>(`/chats/${id}`, { signal: abort.signal });
      setCurrent(saved); setStreaming(''); setAttachment(''); void chats.refresh();
    } catch (err) {
      if (!abort.signal.aborted) { setError(errorMessage(err)); setContent(text) }
      // Reconcile saved messages; never resubmit an uncertain request automatically.
      if (!abort.signal.aborted) {
        try { setCurrent(await api<Chat>(`/chats/${id}`)); setStreaming('') } catch { /* Keep the visible partial answer. */ }
      }
    } finally { lock.current = false; setBusy(false) }
  };
  const attach = async (file?: File) => {
    if (!id || !file) return;
    if (file.size > 10 * 1024 * 1024) { setError('附件不能超过 10 MB。'); return }
    setAttaching(true); setError('');
    const form = new FormData(); form.append('file', file);
    try { const result = await api<{ filename: string; text_excerpt: string }>(`/chats/${id}/attachments`, { method: 'POST', body: form }); setAttachment(result.filename) }
    catch (err) { setError(errorMessage(err)) }
    finally { setAttaching(false); if (uploadInput.current) uploadInput.current.value = '' }
  };
  const remove = async () => {
    if (!id) return;
    setError('');
    try { await api(`/chats/${id}`, { method: 'DELETE' }); navigate('/chat') }
    catch (err) { setError(errorMessage(err)) }
  };
  return <><div className="card chat-layout"><aside className="chat-sidebar"><button className="btn primary wide" disabled={creating} onClick={() => void create()}><Icon name="plus" size={17} />{creating ? '正在创建…' : '新建分析'}</button><span className="eyebrow sidebar-label">YOUR ANALYSES</span>{chats.error && <Notice>{chats.error}</Notice>}{chats.data?.length ? <div className="chat-sessions">{chats.data.map(chat => <button key={chat.id} className={chat.id === id ? 'active' : ''} onClick={() => navigate(`/chat/${chat.id}`)}><Icon name="chat" size={16} /><span>{chat.title || '新的分析'}</span></button>)}</div> : <p className="muted small">你的分析会保存在这里。</p>}</aside><section className="chat-main"><div className="conversation-title"><h3>{current?.title ?? 'JD分析助手'}</h3>{id && <div className="chat-title-actions"><button className="text-button muted" disabled={busy} onClick={() => { void api<Chat>(`/chats/${id}`).then(value => { setCurrent(value); setStreaming('') }).catch(err => setError(errorMessage(err))) }}>刷新</button><button className="text-button muted" disabled={busy} onClick={() => { if (window.confirm('删除这个对话及其中的全部消息？')) void remove() }}>删除对话</button></div>}</div>{error && <Notice>{error}</Notice>}<div className="chat-messages" aria-live="polite">{!id ? <div className="chat-welcome"><span className="welcome-icon"><Icon name="chat" size={32} /></span><h2>从一个岗位开始。</h2><p className="muted">点击左侧“新建分析”，开始评估岗位要求与简历匹配度。</p><div className="prompt-cards"><span>帮我分析这个岗位的核心要求</span><span>看看我的简历和这个岗位有多匹配</span><span>针对这个 JD，我该如何准备面试？</span></div></div> : !current ? <Loading text="正在读取对话…" /> : !current.messages?.length ? <Empty title="想评估哪个岗位？">发岗位链接、贴 JD 或简历，也可以先说你想评估什么。</Empty> : current.messages.map(message => <div key={message.id} className={`message ${message.role === 'user' ? 'message-user' : 'message-assistant'}`}><span className="message-label">{message.role === 'user' ? '我' : message.role === 'system' ? '参考附件' : '助手'}</span><div className="message-body">{message.role === 'system' ? <details><summary>查看附件摘录</summary><RichText text={message.content} /></details> : <RichText text={message.content} />}</div></div>)}{(streaming || busy) && <div className="message message-assistant"><span className="message-label">助手</span><div className="message-body">{streaming ? <RichText text={streaming} /> : <Loading text="正在思考…" />}</div></div>}<div ref={end} /></div><div className="chat-composer"><label className="sr-only" htmlFor="chat-message">消息内容</label><textarea id="chat-message" value={content} onChange={event => setContent(event.target.value)} disabled={!id || busy} placeholder={id ? '发岗位链接、贴 JD 或简历，也可以先说你想评估什么。' : '先点击“新建分析”，然后发送岗位或简历'} rows={3} maxLength={20000} onKeyDown={event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); void send() } }} /><input ref={uploadInput} type="file" className="sr-only" accept=".pdf,.docx,.txt" aria-label="上传对话附件" onChange={event => void attach(event.target.files?.[0])} /><div className="composer-meta"><button className="text-button muted" disabled={!id || busy || attaching} onClick={() => uploadInput.current?.click()}><Icon name="file" size={17} />{attaching ? '正在上传…' : attachment || '添加参考附件'}</button>{busy ? <button className="btn" onClick={() => { controller.current?.abort(); setBusy(false); setError('已停止接收。已发送的消息仍会保存在对话中，可刷新确认。') }}>停止接收</button> : <button className="btn primary" disabled={!id || !content.trim() || attaching} onClick={() => void send()}>发送<Icon name="arrow" size={16} /></button>}</div><p className="small muted composer-hint">Ctrl / ⌘ + Enter 发送 · AI 内容请结合实际判断</p></div></section></div></>;
}
