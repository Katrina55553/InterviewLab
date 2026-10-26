import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, checkResponse, errorMessage, readSSE, requestHeaders, requestId } from './api';
import { useResource } from './hooks';
import type { Chat } from './types';
import { ChatHistory } from './ChatHistory';
import { Empty, Icon, Loading, Notice, RichText } from './ui';

export function ChatPage() {
  const { id } = useParams();
  return <ChatWorkspace id={id} />;
}

function ChatWorkspace({ id }: { id?: string }) {
  const chats = useResource<Chat[]>('/chats');
  const [current, setCurrent] = useState<Chat | null>(null);
  const [content, setContent] = useState('');
  const [streaming, setStreaming] = useState('');
  const [busy, setBusy] = useState(false);
  const [attaching, setAttaching] = useState(false);
  const [error, setError] = useState('');
  const [attachment, setAttachment] = useState('');
  const controller = useRef<AbortController | null>(null);
  const createdChat = useRef<string | null>(null);
  const lock = useRef(false);
  const composer = useRef<HTMLTextAreaElement>(null);
  const uploadInput = useRef<HTMLInputElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const loading = Boolean(id && current?.id !== id);
  useEffect(() => {
    // Promoting the draft to its saved URL must keep the active SSE request alive.
    if (id && createdChat.current === id) { createdChat.current = null; return }
    const abort = new AbortController();
    controller.current?.abort(); controller.current = null; lock.current = false;
    const load = id ? api<Chat>(`/chats/${id}`, { signal: abort.signal }) : Promise.resolve(null);
    load.then(value => {
      if (abort.signal.aborted) return;
      if (uploadInput.current) uploadInput.current.value = '';
      setCurrent(value); setContent(''); setStreaming(''); setBusy(false); setAttaching(false); setError(''); setAttachment('');
    }).catch(err => { if (!abort.signal.aborted) { setCurrent(null); setBusy(false); setAttaching(false); setError(errorMessage(err)) } });
    return () => abort.abort();
  }, [id]);
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [current?.messages?.length, streaming]);
  const create = () => {
    if (id) navigate('/chat');
    else { setContent(''); setError(''); composer.current?.focus() }
  };
  const ensureChat = async (signal: AbortSignal) => {
    if (id) return id;
    const chat = await api<Chat>('/chats', { method: 'POST', body: {}, signal });
    signal.throwIfAborted();
    createdChat.current = chat.id;
    setCurrent({ ...chat, messages: [] });
    chats.setData(value => [chat, ...(value ?? [])]);
    navigate(`/chat/${chat.id}`, { replace: true });
    return chat.id;
  };
  const send = async () => {
    if (!content.trim() || lock.current || loading) return;
    const text = content.trim();
    lock.current = true; setBusy(true); setError(''); setStreaming(''); setContent('');
    const abort = new AbortController(); controller.current = abort;
    let chatId = id;
    let submitted = false;
    let finished = false;
    let streamError = '';
    try {
      chatId = await ensureChat(abort.signal);
      abort.signal.throwIfAborted();
      const temporaryId = requestId();
      setCurrent(value => value ? { ...value, messages: [...(value.messages ?? []), { id: temporaryId, role: 'user', content: text }] } : value);
      submitted = true;
      const response = await checkResponse(await fetch(`/api/chats/${chatId}/messages`, { method: 'POST', credentials: 'include', headers: requestHeaders('POST'), body: JSON.stringify({ content: text, request_id: requestId() }), signal: abort.signal }));
      if (!response.body) throw new Error('未收到流式响应。');
      await readSSE(response.body, (event, data) => {
        if (abort.signal.aborted) return;
        const type = event === 'message' && typeof data.type === 'string' ? data.type : event;
        if (type === 'delta' && typeof data.content === 'string') setStreaming(previous => previous + data.content);
        if (type === 'done') finished = true;
        if (type === 'error') streamError = String(data.message ?? '对话生成失败。');
      });
      if (streamError) throw new Error(streamError);
      if (!finished) throw new Error('连接中断，回答可能仍在生成。请刷新对话确认结果。');
      const saved = await api<Chat>(`/chats/${chatId}`, { signal: abort.signal });
      abort.signal.throwIfAborted();
      setCurrent(saved); setStreaming(''); setAttachment(''); void chats.refresh();
    } catch (err) {
      if (!submitted && abort.signal.aborted && controller.current === abort) setContent(text);
      if (!abort.signal.aborted) { setError(errorMessage(err)); setContent(text) }
      // Reconcile saved messages; never resubmit an uncertain request automatically.
      if (chatId && !abort.signal.aborted) {
        try { const saved = await api<Chat>(`/chats/${chatId}`, { signal: abort.signal }); abort.signal.throwIfAborted(); setCurrent(saved); setStreaming('') } catch { /* Keep the visible partial answer. */ }
      }
    } finally { if (controller.current === abort) { lock.current = false; setBusy(false) } }
  };
  const attach = async (file?: File) => {
    if (!file || lock.current || loading) return;
    if (file.size > 10 * 1024 * 1024) { setError('附件不能超过 10 MB。'); return }
    lock.current = true; setAttaching(true); setError('');
    const abort = new AbortController(); controller.current = abort;
    const form = new FormData(); form.append('file', file);
    try {
      const chatId = await ensureChat(abort.signal);
      abort.signal.throwIfAborted();
      const result = await api<{ filename: string; text_excerpt: string }>(`/chats/${chatId}/attachments`, { method: 'POST', body: form, signal: abort.signal });
      abort.signal.throwIfAborted(); setAttachment(result.filename);
      const saved = await api<Chat>(`/chats/${chatId}`, { signal: abort.signal });
      abort.signal.throwIfAborted(); setCurrent(saved);
    }
    catch (err) { if (!abort.signal.aborted) setError(errorMessage(err)) }
    finally { if (controller.current === abort) { lock.current = false; setAttaching(false); if (uploadInput.current) uploadInput.current.value = '' } }
  };
  const refresh = async () => {
    if (!id) return;
    const abort = new AbortController(); controller.current = abort;
    try {
      const saved = await api<Chat>(`/chats/${id}`, { signal: abort.signal });
      if (controller.current === abort && !abort.signal.aborted) { setCurrent(saved); setStreaming('') }
    } catch (err) { if (controller.current === abort && !abort.signal.aborted) setError(errorMessage(err)) }
  };
  return <div className="card chat-layout">
    <aside className="chat-sidebar">
      <button className="btn primary wide" disabled={busy || attaching} onClick={create}><Icon name="plus" size={17} />新建分析</button>
      <span className="eyebrow sidebar-label">YOUR ANALYSES</span>
      {chats.error && <Notice>{chats.error}</Notice>}
      {chats.data?.length ? <ChatHistory chats={chats.data} activeId={id} processing={busy || attaching} onSelect={chatId => navigate(`/chat/${chatId}`)} onUpdate={chat => {
        chats.setData(value => value?.map(item => item.id === chat.id ? { ...item, ...chat } : item) ?? null);
        setCurrent(value => value?.id === chat.id ? { ...value, ...chat } : value);
      }} onDelete={chatId => {
        chats.setData(value => value?.filter(chat => chat.id !== chatId) ?? null);
        if (chatId === id) navigate('/chat');
      }} /> : <p className="muted small">你的分析会保存在这里。</p>}
    </aside>
    <section className="chat-main">
      <div className="conversation-title">
        <h3>{loading ? 'JD分析助手' : current?.title ?? 'JD分析助手'}</h3>
        {id && <div className="chat-title-actions">
          <button className="text-button muted" disabled={busy || attaching || loading} onClick={() => void refresh()}>刷新</button>
        </div>}
      </div>
      {error && <Notice>{error}</Notice>}
      <div className={`chat-messages${!loading && !current?.messages?.length && !busy && !streaming ? ' chat-messages-empty' : ''}`} aria-live="polite">
        {loading ? <Loading text="正在读取对话…" /> : !current ? <div className="chat-welcome">
          <span className="welcome-icon"><Icon name="chat" size={32} /></span>
          <h2>从一个岗位开始。</h2>
          <p className="muted">直接输入岗位要求、粘贴 JD 或简历，发送后自动保存对话。</p>
          <div className="prompt-cards"><span>帮我分析这个岗位的核心要求</span><span>看看我的简历和这个岗位有多匹配</span><span>针对这个 JD，我该如何准备面试？</span></div>
        </div> : !current.messages?.length ? !busy && <Empty title="想评估哪个岗位？">发岗位链接、贴 JD 或简历，也可以先说你想评估什么。</Empty> : current.messages.map(message => <div key={message.id} className={`message ${message.role === 'user' ? 'message-user' : 'message-assistant'}`}>
          <span className="message-label">{message.role === 'user' ? '我' : message.role === 'system' ? '参考附件' : '助手'}</span>
          <div className="message-body">{message.role === 'system' ? <details><summary>查看附件摘录</summary><RichText text={message.content} /></details> : <RichText text={message.content} />}</div>
        </div>)}
        {!loading && (streaming || busy) && <div className="message message-assistant"><span className="message-label">助手</span><div className="message-body">{streaming ? <RichText text={streaming} /> : <Loading text="正在思考…" />}</div></div>}
        <div ref={end} />
      </div>
      <div className="chat-composer">
        <label className="sr-only" htmlFor="chat-message">消息内容</label>
        <textarea ref={composer} id="chat-message" value={content} onChange={event => setContent(event.target.value)} disabled={busy || attaching || loading} placeholder="发岗位链接、贴 JD 或简历，也可以先说你想评估什么。" rows={3} maxLength={20000} onKeyDown={event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); void send() } }} />
        <input ref={uploadInput} type="file" className="sr-only" accept=".pdf,.docx,.txt" aria-label="上传对话附件" onChange={event => void attach(event.target.files?.[0])} />
        <div className="composer-meta">
          <button className="text-button muted" disabled={busy || attaching || loading} onClick={() => uploadInput.current?.click()}><Icon name="file" size={17} />{attaching ? '正在上传…' : attachment || '添加参考附件'}</button>
          {busy ? <button className="btn" onClick={() => { controller.current?.abort(); setBusy(false); setError('已停止接收。已发送的消息仍会保存在对话中，可刷新确认。') }}>停止接收</button> : <button className="btn primary" disabled={!content.trim() || attaching || loading} onClick={() => void send()}>发送<Icon name="arrow" size={16} /></button>}
        </div>
        <p className="small muted composer-hint">Ctrl / ⌘ + Enter 发送 · AI 内容请结合实际判断</p>
      </div>
    </section>
  </div>;
}
