import { create } from 'zustand';
import { api, ApiError } from './api';
import type { User } from './types';

interface AuthState {
  user: User | null; loading: boolean; error: string;
  refresh: () => Promise<void>;
  authenticate: (mode: 'login' | 'register', username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}
export const useAuth = create<AuthState>((set) => ({
  user: null, loading: true, error: '',
  refresh: async () => {
    try { set({ user: await api<User>('/auth/me'), loading: false, error: '' }) }
    catch (error) { set({ user: null, loading: false, error: error instanceof ApiError && error.status === 401 ? '' : '暂时无法连接服务，请重试。' }) }
  },
  authenticate: async (mode, username, password) => { set({ user: await api<User>(`/auth/${mode}`, { method: 'POST', body: { username, password } }), error: '' }) },
  logout: async () => { await api('/auth/logout', { method: 'POST' }); set({ user: null }) },
}));
