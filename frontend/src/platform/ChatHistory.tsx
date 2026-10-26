import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api, errorMessage } from './api';
import type { Chat } from './types';
import { Icon, Notice } from './ui';
import './chat-history.css';

type HistoryAction = { type: 'rename' | 'delete'; chat: Chat };

function HistoryDialog({ action, onClose, onUpdate, onDelete }: {
  action: HistoryAction; onClose: () => void; onUpdate: (chat: Chat) => void; onDelete: (id: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const lock = useRef(false);
  const [title, setTitle] = useState(action.chat.title);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const inputId = useId();
  const renaming = action.type === 'rename';
  useEffect(() => {
    const node = dialog.current!;
    node.showModal();
    if (input.current) { input.current.focus(); input.current.select() }
    return () => node.close();
  }, []);
  const submit = async () => {
    if (lock.current || (renaming && !title.trim())) return;
    lock.current = true; setSaving(true); setError('');
    try {
      if (renaming) onUpdate(await api<Chat>(`/chats/${action.chat.id}`, { method: 'PATCH', body: { title: title.trim() } }));
      else { await api(`/chats/${action.chat.id}`, { method: 'DELETE' }); onDelete(action.chat.id) }
      onClose();
    } catch (err) { setError(errorMessage(err)) }
    finally { lock.current = false; setSaving(false) }
  };
  return <dialog ref={dialog} className="chat-history-dialog" aria-label={renaming ? '重命名对话' : '删除对话'} onCancel={event => { event.preventDefault(); if (!lock.current) onClose() }} onClick={event => { if (!lock.current && event.target === event.currentTarget) onClose() }}>
    <form onSubmit={event => { event.preventDefault(); void submit() }}>
      <h2>{renaming ? '重命名对话' : '删除这个对话？'}</h2>
      {renaming ? <><label htmlFor={inputId}>对话名称</label><input ref={input} id={inputId} value={title} maxLength={255} disabled={saving} onChange={event => setTitle(event.target.value)} /></> : <p>“{action.chat.title}”及其中的全部消息将被永久删除。</p>}
      {error && <Notice>{error}</Notice>}
      <div className="chat-history-dialog-actions">
        <button type="button" className="btn" disabled={saving} onClick={onClose}>取消</button>
        <button className={`btn ${renaming ? 'primary' : 'chat-delete-confirm'}`} disabled={saving || (renaming && !title.trim())}>{saving ? '正在处理…' : renaming ? '保存' : '确认删除'}</button>
      </div>
    </form>
  </dialog>;
}

export function ChatHistory({ chats, activeId, processing, onSelect, onUpdate, onDelete }: {
  chats: Chat[]; activeId?: string; processing: boolean; onSelect: (id: string) => void;
  onUpdate: (chat: Chat) => void; onDelete: (id: string) => void;
}) {
  const [menu, setMenu] = useState<{ id: string; left: number; top: number } | null>(null);
  const [action, setAction] = useState<HistoryAction | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const lock = useRef(false);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const menuNode = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const selected = chats.find(chat => chat.id === menu?.id);
  const ordered = [...chats].sort((a, b) => Number(Boolean(b.is_pinned)) - Number(Boolean(a.is_pinned)) || (b.created_at ?? '').localeCompare(a.created_at ?? ''));
  const closeMenu = (restoreFocus = true) => { setMenu(null); if (restoreFocus) trigger.current?.focus() };
  useEffect(() => {
    if (!menu) return;
    menuNode.current?.querySelector<HTMLButtonElement>('button')?.focus();
    const dismiss = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!menuNode.current?.contains(target) && !trigger.current?.contains(target)) setMenu(null);
    };
    const close = () => setMenu(null);
    document.addEventListener('pointerdown', dismiss);
    document.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    return () => {
      document.removeEventListener('pointerdown', dismiss);
      document.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
    };
  }, [menu]);
  const togglePin = async (chat: Chat) => {
    if (lock.current) return;
    closeMenu(); lock.current = true; setWorking(true); setError('');
    try { onUpdate(await api<Chat>(`/chats/${chat.id}`, { method: 'PATCH', body: { is_pinned: !chat.is_pinned } })) }
    catch (err) { setError(errorMessage(err)) }
    finally { lock.current = false; setWorking(false) }
  };
  const beginAction = (type: HistoryAction['type'], chat: Chat) => { closeMenu(); setError(''); setAction({ type, chat }) };
  return <>
    {error && <Notice>{error}</Notice>}
    <div className="chat-sessions">
      {ordered.map(chat => <div key={chat.id} className={`chat-session${chat.id === activeId ? ' active' : ''}`}>
        <button className="chat-session-link" aria-current={chat.id === activeId ? 'page' : undefined} title={chat.title} onClick={() => { closeMenu(false); onSelect(chat.id) }}>
          <Icon name={chat.is_pinned ? 'pin' : 'chat'} size={16} /><span>{chat.title || '新的分析'}</span>
        </button>
        <button className="chat-session-more" aria-label={`管理对话：${chat.title}`} aria-haspopup="menu" aria-expanded={menu?.id === chat.id} aria-controls={menu?.id === chat.id ? menuId : undefined} disabled={working} onClick={event => {
          if (menu?.id === chat.id) { closeMenu(); return }
          trigger.current = event.currentTarget;
          const rect = event.currentTarget.getBoundingClientRect();
          setMenu({ id: chat.id, left: Math.max(8, Math.min(rect.right - 176, window.innerWidth - 184)), top: Math.max(8, rect.bottom + 154 > window.innerHeight ? rect.top - 154 : rect.bottom + 6) });
        }}><Icon name="more" size={18} /></button>
      </div>)}
    </div>
    {menu && selected && createPortal(<div ref={menuNode} id={menuId} className="chat-history-menu" role="menu" aria-label="对话管理" style={{ left: menu.left, top: menu.top }} onKeyDown={event => {
      if (event.key === 'Escape' || event.key === 'Tab') { if (event.key === 'Escape') event.preventDefault(); closeMenu(); return }
      const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')];
      const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
      const next = event.key === 'ArrowDown' ? (index + 1) % buttons.length : event.key === 'ArrowUp' ? (index - 1 + buttons.length) % buttons.length : event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : -1;
      if (next >= 0) { event.preventDefault(); buttons[next].focus() }
    }}>
      <button role="menuitem" onClick={() => beginAction('rename', selected)}><Icon name="pencil" size={17} />重命名</button>
      <button role="menuitem" onClick={() => void togglePin(selected)}><Icon name="pin" size={17} />{selected.is_pinned ? '取消置顶' : '置顶'}</button>
      <div className="chat-history-menu-divider" />
      <button role="menuitem" className="chat-history-delete" disabled={processing && selected.id === activeId} onClick={() => beginAction('delete', selected)}><Icon name="trash" size={17} />删除</button>
    </div>, document.body)}
    {action && <HistoryDialog key={`${action.type}-${action.chat.id}`} action={action} onClose={() => setAction(null)} onUpdate={onUpdate} onDelete={onDelete} />}
  </>;
}
