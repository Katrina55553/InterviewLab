import { AdapterError, filterModelsByCapabilities } from 'model-connection-kit';
import type { AuthStatus, ModelSelection, ModelSettingsAdapter, ModelSummary, ProbeStatus, ProviderSummary } from 'model-connection-kit';
import { api } from './api';

export interface AIConnectionState {
  selection: ModelSelection | null;
  version: number;
  has_key: boolean;
  allowed_base_urls: string[];
}

export function createUserModelAdapter(onDisconnect?: () => Promise<unknown>): ModelSettingsAdapter {
  return {
    listProviders: options => api<ProviderSummary[]>('/settings/ai/providers', { signal: options?.signal }),
    listModels: async options => filterModelsByCapabilities(
      await api<ModelSummary[]>(`/settings/ai/models?provider_id=${encodeURIComponent(options.providerId)}`, { signal: options.signal }),
      options.requiredCapabilities,
    ),
    getAuthStatus: (providerId, signal) => api<AuthStatus>(`/settings/ai/auth?provider_id=${encodeURIComponent(providerId)}`, { signal }),
    connect: async (request, options) => {
      if (request.authMethod !== 'api-key' && request.authMethod !== 'none') {
        throw new AdapterError('auth', '当前支持 API Key 和自定义接口，订阅登录暂未接入。');
      }
      return api('/settings/ai/connect', { method: 'POST', body: request, signal: options?.signal });
    },
    testConnection: (selection, options) => api<ProbeStatus>('/settings/ai/probe', { method: 'POST', body: selection, signal: options?.signal }),
    disconnect: async (providerId, signal) => {
      const result = await api<AuthStatus>(`/settings/ai/auth?provider_id=${encodeURIComponent(providerId)}`, { method: 'DELETE', signal });
      await onDisconnect?.();
      return result;
    },
  };
}
