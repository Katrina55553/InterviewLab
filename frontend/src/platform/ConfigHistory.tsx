import { useState } from 'react';
import { api, errorMessage } from './api';
import { useResource } from './hooks';
import { Notice } from './ui';

interface Revision {
  id: string; version: number; deleted: boolean; created_at: string; key_changed: boolean;
  changes: Record<string, { before: unknown; after: unknown }>;
}

export function ConfigHistory({ kind, id, version, onRestored }: { kind: string; id: string; version: number; onRestored: () => void }) {
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const history = useResource<{ items: Revision[]; has_more: boolean }>(`/admin/revisions/${kind}/${id}?offset=${offset}`);
  const restore = async (target: number) => {
    setBusy(true); setError('');
    try {
      await api(`/admin/revisions/${kind}/${id}/restore`, { method: 'POST', body: { version: target, expected_version: version } });
      await history.refresh(); onRestored();
    } catch (err) { setError(errorMessage(err)) } finally { setBusy(false) }
  };
  return <section className="card"><h2>配置修订记录</h2>{(history.error || error) && <Notice>{error || history.error}</Notice>}{history.data?.items.map(item => <article className="config-row" key={item.id}><div><h3>版本 {item.version}{item.deleted ? ' · 已删除' : ''}</h3><p>{new Date(item.created_at).toLocaleString()}</p>{item.key_changed && <p>密钥已变更</p>}<pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(item.changes, null, 2)}</pre></div><button className="text-button" disabled={busy || item.version === version || item.deleted} onClick={() => void restore(item.version)}>恢复为新版本</button></article>)}<button className="text-button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>上一页</button><button className="text-button" disabled={!history.data?.has_more} onClick={() => setOffset(offset + 20)}>下一页</button></section>;
}
