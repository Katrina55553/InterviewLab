import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorMessage, websocketUrl } from './api';
import type { Interview, InterviewEvent } from './types';

export function useResource<T>(path: string, pollMs = 0, merge?: (current: T | null, next: T) => T) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  const mounted = useRef(true);
  const pathRef = useRef(path);
  useEffect(() => { pathRef.current = path }, [path]);
  const refresh = useCallback(async () => {
    const requestedPath = path;
    try {
      const value = await api<T>(requestedPath);
      if (mounted.current && pathRef.current === requestedPath) { setData(current => merge ? merge(current, value) : value); setError('') }
      return value;
    } catch (err) {
      if (mounted.current && pathRef.current === requestedPath) setError(errorMessage(err));
      return null;
    }
  }, [path, merge]);
  useEffect(() => {
    mounted.current = true;
    queueMicrotask(() => { if (mounted.current) void refresh() });
    const timer = pollMs ? window.setInterval(() => { if (!document.hidden) void refresh() }, pollMs) : undefined;
    return () => { mounted.current = false; window.clearInterval(timer) };
  }, [refresh, pollMs]);
  return { data, error, refresh, setData };
}

export function newerSnapshot(current: Interview | null, next: Interview): Interview {
  return !current || next.id !== current.id || next.version >= current.version ? next : current;
}

export function useInterview(id: string) {
  const resource = useResource<Interview>(`/interviews/${id}`, 3000, newerSnapshot);
  const [connection, setConnection] = useState('连接中');
  const { setData } = resource;
  useEffect(() => {
    let stopped = false;
    let socket: WebSocket | null = null;
    let timer: number | undefined;
    let cursor = 0;
    let attempts = 0;
    const connect = () => {
      if (stopped) return;
      socket = new WebSocket(websocketUrl(`/ws/interviews/${id}?after=${cursor}`));
      socket.onopen = () => { attempts = 0; setConnection('实时同步') };
      socket.onmessage = event => {
        try {
          const incoming = JSON.parse(event.data) as InterviewEvent;
          if (!incoming.sequence || incoming.sequence <= cursor) return;
          cursor = incoming.sequence;
          const snapshot = incoming.payload?.snapshot;
          if (snapshot) setData(current => newerSnapshot(current, snapshot));
        } catch { /* Ignore non-event heartbeats; REST polling reconciles persisted state. */ }
      };
      socket.onclose = () => {
        if (stopped) return;
        setConnection('重连中 · 已启用自动同步');
        timer = window.setTimeout(connect, Math.min(15000, 1000 * 2 ** Math.min(attempts++, 4)));
      };
      socket.onerror = () => socket?.close();
    };
    connect();
    return () => { stopped = true; window.clearTimeout(timer); socket?.close() };
  }, [id, setData]);
  return { ...resource, connection };
}
