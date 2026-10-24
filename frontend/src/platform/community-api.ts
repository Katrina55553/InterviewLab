import { useCallback, useEffect, useState } from 'react';
import { api, errorMessage } from './api';

/** Hide responses from obsolete filters/accounts and cancel requests on navigation. */
export function useCommunityResource<T>(path: string | null) {
  const [snapshot, setSnapshot] = useState<{ path: string | null; data: T | null; error: string; refreshing: boolean }>({ path: null, data: null, error: '', refreshing: false });
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    void api<T>(path, { signal: controller.signal }).then(data => {
      if (!controller.signal.aborted) setSnapshot({ path, data, error: '', refreshing: false });
    }).catch(error => {
      if (!controller.signal.aborted) setSnapshot(previous => ({ path, data: previous.path === path ? previous.data : null, error: errorMessage(error), refreshing: false }));
    });
    return () => controller.abort();
  }, [path, revision]);
  const refresh = useCallback(() => { setSnapshot(previous => ({ ...previous, refreshing: true })); setRevision(value => value + 1) }, []);
  const update = useCallback((change: (data: T) => T) => setSnapshot(previous => previous.data ? { ...previous, data: change(previous.data) } : previous), []);
  const current = snapshot.path === path;
  return { data: current ? snapshot.data : null, error: current ? snapshot.error : '', loading: Boolean(path && (!current || snapshot.refreshing)), refresh, update };
}
