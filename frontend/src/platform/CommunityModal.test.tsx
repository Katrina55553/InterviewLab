import { act, StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import type { Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CommunityModal } from './CommunityModal';

let container: HTMLDivElement;
let root: Root;
let originalOverflow: string;
const renderDialogs = (reader: boolean, report: boolean) => act(async () => {
  root.render(<StrictMode>
    {reader && <CommunityModal key="reader" title="面经详情" onClose={() => {}}>全文</CommunityModal>}
    {report && <CommunityModal key="report" title="举报评论" onClose={() => {}}>举报说明</CommunityModal>}
  </StrictMode>);
});

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  HTMLDialogElement.prototype.showModal = function () { this.open = true };
  HTMLDialogElement.prototype.close = function () { this.open = false };
  originalOverflow = document.body.style.overflow;
  document.body.style.overflow = '';
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount()); container.remove();
  document.body.style.overflow = originalOverflow;
  vi.restoreAllMocks(); vi.unstubAllGlobals();
});

describe('community dialog scroll locks', () => {
  it('restores scrolling when reader and report unmount in the same update', async () => {
    await renderDialogs(true, false); await renderDialogs(true, true);
    expect(document.body.style.overflow).toBe('hidden');
    await renderDialogs(false, false);
    expect(document.body.style.overflow).toBe('');
  });
  it('keeps the remaining dialog locked when the reader is removed before its report', async () => {
    document.body.style.overflow = 'scroll';
    await renderDialogs(true, false); await renderDialogs(true, true);
    await renderDialogs(false, true);
    expect(document.body.style.overflow).toBe('hidden');
    await renderDialogs(false, false);
    expect(document.body.style.overflow).toBe('scroll');
  });
  it('supports cancelling and reopening a report before closing the reader', async () => {
    await renderDialogs(true, false); await renderDialogs(true, true);
    await renderDialogs(true, false);
    expect(document.body.style.overflow).toBe('hidden');
    await renderDialogs(true, true); await renderDialogs(true, false);
    await renderDialogs(false, false);
    expect(document.body.style.overflow).toBe('');
  });
});
