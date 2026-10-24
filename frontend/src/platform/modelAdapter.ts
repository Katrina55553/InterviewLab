import { AdapterError, filterModelsByCapabilities } from 'model-connection-kit';
import type { AuthStatus, ModelSelection, ModelSettingsAdapter, ModelSummary, ProbeStatus, ProviderSummary } from 'model-connection-kit';
import { api } from './api';

export interface AIConnectionState {
  selection: ModelSelection | null;
  version: number;
  has_key: boolean;
  allowed_base_urls: string[];
}

export interface AIProvider extends ProviderSummary {
  baseUrl?: string;
  defaultModel?: string;
}

export interface OAuthAuthorization {
  session_id: string;
  authorization_url: string;
  redirect_uri: string;
  expires_in: number;
}

export function createUserModelAdapter(onDisconnect?: () => Promise<unknown>, onAuthorize?: (session: OAuthAuthorization) => void): ModelSettingsAdapter {
  return {
    listProviders: options => api<ProviderSummary[]>('/settings/ai/providers', { signal: options?.signal }),
    listModels: async options => filterModelsByCapabilities(
      await api<ModelSummary[]>(`/settings/ai/models?provider_id=${encodeURIComponent(options.providerId)}`, { signal: options.signal }),
      options.requiredCapabilities,
    ),
    getAuthStatus: (providerId, signal) => api<AuthStatus>(`/settings/ai/auth?provider_id=${encodeURIComponent(providerId)}`, { signal }),
    connect: async (request, options) => {
      if (request.authMethod === 'oauth') {
        if (!onAuthorize) throw new AdapterError('capability', '请从 AI 设置页面发起订阅授权。');
        const session = await api<OAuthAuthorization>('/settings/ai/oauth/start', {
          method: 'POST', body: { providerId: request.providerId }, signal: options?.signal,
        });
        onAuthorize(session);
        return { providerId: request.providerId, auth: { state: 'unconfigured', method: 'oauth' } };
      }
      if (request.authMethod !== 'api-key' && request.authMethod !== 'none') {
        throw new AdapterError('auth', '此认证方式暂不支持。');
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
