import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { Icon } from './ui';

const scrollLocks = new WeakMap<HTMLElement, { count: number; overflow: string }>();

function lockBodyScroll(body: HTMLElement) {
  let lock = scrollLocks.get(body);
  if (!lock) {
    lock = { count: 0, overflow: body.style.overflow };
    scrollLocks.set(body, lock);
  }
  const activeLock = lock;
  activeLock.count += 1;
  body.style.overflow = 'hidden';
  return () => {
    activeLock.count -= 1;
    if (activeLock.count === 0) {
      body.style.overflow = activeLock.overflow;
      scrollLocks.delete(body);
    }
  };
}

export function CommunityModal({ title, className = '', busy = false, onClose, children }: { title: string; className?: string; busy?: boolean; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    // Readers and report forms can unmount together or in either order.
    const releaseScroll = lockBodyScroll(document.body);
    dialog.showModal();
    dialog.querySelector<HTMLElement>('[data-community-focus]')?.focus();
    return () => { dialog.close(); releaseScroll() };
  }, []);
  return <dialog ref={ref} className={`community-dialog ${className}`} aria-label={title} onCancel={event => { event.preventDefault(); if (!busy) onClose() }} onClick={event => { if (!busy && event.target === event.currentTarget) onClose() }}>
    <div className="community-dialog-inner"><header className="community-dialog-header"><span><Icon name="book" size={18} />{title}</span><button type="button" className="community-icon-button" aria-label="关闭弹窗" disabled={busy} onClick={onClose}><Icon name="close" /></button></header>{children}</div>
  </dialog>;
}
