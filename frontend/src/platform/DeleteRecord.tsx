import { useRef, useState } from 'react';
import { api, errorMessage } from './api';
import { Icon, Notice } from './ui';

export function DeleteRecordButton({ kind, id, name, onDeleted }: {
  kind: 'resume' | 'interview'; id: string; name: string; onDeleted: (id: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const lock = useRef(false);
  const label = kind === 'resume' ? '简历' : '面试';
  const remove = async () => {
    if (lock.current) return;
    const description = kind === 'resume'
      ? '简历原文件、解析和诊断结果将永久删除。如有关联面试，请先在面试记录中删除相关面试。'
      : '本次面试的回答、报告和语音文件将永久删除。';
    if (!window.confirm(`删除“${name}”？\n${description}`)) return;
    lock.current = true; setBusy(true); setError('');
    try {
      await api(`/${kind === 'resume' ? 'resumes' : 'interviews'}/${id}`, { method: 'DELETE' });
      if (kind === 'interview') sessionStorage.removeItem(`meeting:command:${id}`);
      onDeleted(id);
    } catch (err) { setError(errorMessage(err)) }
    finally { lock.current = false; setBusy(false) }
  };
  return <div className="record-delete">
    <button className="text-button danger record-delete-button" aria-label={`删除${label}：${name}`} disabled={busy} onClick={() => void remove()}><Icon name="trash" size={16} />{busy ? '删除中…' : '删除'}</button>
    {error && <Notice>{error}</Notice>}
  </div>;
}
