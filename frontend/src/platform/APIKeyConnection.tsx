import { useEffect, useRef, useState } from 'react';
import { redactSensitiveText } from 'model-connection-kit';
import type { AuthStatus, ModelSelection, ModelSettingsAdapter, ProbeStatus } from 'model-connection-kit';
import type { AIProvider } from './modelAdapter';

interface Props {
  providers: AIProvider[];
  value: ModelSelection | null;
  adapter: ModelSettingsAdapter;
  onSave: (selection: ModelSelection) => Promise<void>;
}

export function APIKeyConnection({ providers, value, adapter, onSave }: Props) {
  const initial = value?.connectionType === 'builtin' && value.authMethod === 'api-key' ? value : null;
  const [providerId, setProviderId] = useState(initial?.providerId ?? 'deepseek');
  const provider = providers.find(item => item.id === providerId) ?? providers[0];
  const [modelId, setModelId] = useState<string | null>(initial?.modelId ?? null);
  const model = modelId ?? provider?.defaultModel ?? '';
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [probe, setProbe] = useState<ProbeStatus | null>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [hasDraftKey, setHasDraftKey] = useState(false);
  const keyInput = useRef<HTMLInputElement>(null);
  const operation = useRef<AbortController | null>(null);
  const authCheck = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    authCheck.current = controller;
    if (provider) void adapter.getAuthStatus(provider.id, controller.signal).then(status => {
      if (!controller.signal.aborted) setAuth(status);
    }).catch(() => {
      if (!controller.signal.aborted) setError('认证状态读取失败，请重新选择服务商。');
    });
    return () => controller.abort();
  }, [adapter, provider]);
  useEffect(() => () => operation.current?.abort(), []);

  function changeProvider(id: string) {
    setProviderId(id); setModelId(null); setAuth(null); setProbe(null); setError(''); setHasDraftKey(false);
    if (keyInput.current) keyInput.current.value = '';
  }

  async function run(action: 'connect' | 'disconnect' | 'test' | 'save') {
    if (!provider || busy || operation.current) return;
    const apiKey = keyInput.current?.value.trim() ?? '';
    if (action === 'connect' && !apiKey) { setError('请填写 API Key'); return }
    if ((action === 'test' || action === 'save') && !model.trim()) { setError('请填写模型 ID'); return }
    const controller = new AbortController();
    authCheck.current?.abort();
    operation.current = controller;
    setBusy(action); setError('');
    const selection: ModelSelection = { connectionType: 'builtin', authMethod: 'api-key', providerId: provider.id, modelId: model.trim() };
    try {
      if (action === 'connect') {
        const result = await adapter.connect({ connectionType: 'builtin', authMethod: 'api-key', providerId: provider.id, apiKey }, { signal: controller.signal });
        if (!controller.signal.aborted) {
          setAuth(result.auth); setProbe(null); setHasDraftKey(false);
          if (keyInput.current) keyInput.current.value = '';
        }
      } else if (action === 'disconnect') {
        const result = await adapter.disconnect(provider.id, controller.signal);
        if (!controller.signal.aborted) { setAuth(result); setProbe(null) }
      } else if (action === 'test') {
        const result = await adapter.testConnection(selection, { signal: controller.signal });
        if (!controller.signal.aborted) setProbe(result);
      } else {
        await onSave(selection);
      }
    } catch (reason) {
      if (!controller.signal.aborted) setError(redactSensitiveText(reason instanceof Error ? reason.message : '操作失败，请重试。', [apiKey]));
    } finally {
      if (!controller.signal.aborted) setBusy('');
      if (operation.current === controller) operation.current = null;
    }
  }

  const configured = auth?.state === 'configured';
  const canUseModel = configured && !hasDraftKey && !!model.trim();
  return <div className="ai-api-key-connection">
    <label>服务商<select aria-label="服务商" value={provider?.id ?? ''} disabled={!!busy || !providers.length} onChange={event => changeProvider(event.target.value)}>
      {!providers.length && <option value="">暂无可用的 API Key 服务商</option>}
      {providers.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select></label>
    <label>服务地址<input aria-label="服务地址" value={provider?.baseUrl ?? ''} readOnly /><span className="ai-field-hint">根据服务商自动填入。</span></label>
    <label>API Key<input aria-label="API Key" type="password" autoComplete="off" ref={keyInput} disabled={!!busy || !provider} onChange={event => setHasDraftKey(!!event.target.value)} /></label>
    <label>模型 ID<input aria-label="模型 ID" value={model} placeholder="填写服务商提供的模型 ID" list={provider?.id === 'deepseek' ? 'ai-deepseek-models' : undefined} disabled={!!busy || !provider} onChange={event => { setModelId(event.target.value); setProbe(null) }} />
      <span className="ai-field-hint">使用该服务商账户可调用的模型 ID。</span>
    </label>
    {provider?.id === 'deepseek' && <datalist id="ai-deepseek-models"><option value="deepseek-chat" /><option value="deepseek-reasoner" /></datalist>}
    <p className="mck-status" role="status">{configured ? '认证已配置 · 个人连接' : '尚未连接此服务商'}</p>
    <p className="mck-billing-note">连接测试可能产生费用，使用所选服务商的账户额度。</p>
    {probe && <p className="mck-probe" role="status">{probe.message ?? (probe.state === 'reachable' ? '模型连接正常。' : '连接测试失败，请重试。')}</p>}
    {error && <p className="ai-connection-error" role="alert">{error}</p>}
    <footer>
      <button type="button" disabled={!!busy || !provider || !hasDraftKey} onClick={() => void run('connect')}>{busy === 'connect' ? '连接中…' : '连接'}</button>
      <button type="button" disabled={!!busy || !configured} onClick={() => void run('disconnect')}>{busy === 'disconnect' ? '断开中…' : '断开认证'}</button>
      <button type="button" disabled={!!busy || !canUseModel} onClick={() => void run('test')}>{busy === 'test' ? '测试中…' : '测试连接'}</button>
      <button type="button" disabled={!!busy || !canUseModel} onClick={() => void run('save')}>{busy === 'save' ? '保存中…' : '保存'}</button>
    </footer>
  </div>;
}
