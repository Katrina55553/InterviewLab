import { useEffect, useRef, useState } from 'react';
import type { ModelSelection, ModelSettingsAdapter, ModelSummary, ProbeStatus } from 'model-connection-kit';
import { api, errorMessage } from './api';
import { useResource } from './hooks';
import type { AIProvider } from './modelAdapter';
import { Combobox, Select } from './Select';
import { Icon, Loading, Notice } from './ui';

export interface VisionConnectionState {
  selection: ModelSelection | null;
  enabled: boolean;
  version: number;
  verified: boolean;
  tested_at: string | null;
}
type ImageProbe = ProbeStatus & { probe_token?: string };

export function VisionSettings({ providers, adapter }: { providers: AIProvider[]; adapter: ModelSettingsAdapter }) {
  const { data, error, refresh, setData } = useResource<VisionConnectionState>('/settings/ai/vision');
  const [success, setSuccess] = useState('');
  return <section className="mck-panel ai-vision-settings" aria-labelledby="ai-vision-title">
    <header><p className="mck-eyebrow">VISUAL FEEDBACK</p><h2 id="ai-vision-title"><Icon name="camera" size={18} />视觉模型</h2><p>单独选择能识别图片的模型，用于光线、镜头位置和姿态建议。</p></header>
    {error && <Notice>{error}<button className="text-button" onClick={() => void refresh()}>重新加载视觉设置</button></Notice>}
    {success && <Notice tone="success">{success}</Notice>}
    {!data ? !error && <Loading text="正在读取视觉设置…" /> : <VisionForm key={data.version} data={data} providers={providers} adapter={adapter} onSaved={saved => {
      setData(saved); setSuccess(saved.enabled ? '视觉模型已保存，将用于新建的面试。' : '已停用新面试的视觉建议。');
    }} />}
  </section>;
}

function VisionForm({ data, providers, adapter, onSaved }: {
  data: VisionConnectionState; providers: AIProvider[]; adapter: ModelSettingsAdapter; onSaved: (state: VisionConnectionState) => void;
}) {
  const [enabled, setEnabled] = useState(data.enabled);
  const [providerId, setProviderId] = useState(data.selection?.providerId ?? providers.find(item => item.id === 'openai-api')?.id ?? providers[0]?.id ?? '');
  const [model, setModel] = useState(data.selection?.modelId ?? '');
  const [models, setModels] = useState<ModelSummary[]>([]);
  const [configured, setConfigured] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [catalogError, setCatalogError] = useState('');
  const [probe, setProbe] = useState<ImageProbe | null>(null);
  const operation = useRef<AbortController | null>(null);
  const provider = providers.find(item => item.id === providerId);
  const legacy = data.selection?.connectionType === 'custom' && data.selection.providerId === providerId ? data.selection : null;
  const selection: ModelSelection = legacy ? { ...legacy, modelId: model.trim() } : {
    connectionType: 'builtin', authMethod: provider?.auth.oauth ? 'oauth' : 'api-key', providerId, modelId: model.trim(),
  };
  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      setLoading(true);
      try {
        const auth = await adapter.getAuthStatus(providerId, controller.signal);
        if (controller.signal.aborted) return;
        const connected = auth.state === 'configured';
        setConfigured(connected);
        if (connected && !providerId.startsWith('custom:')) {
          try {
            const catalog = await adapter.listModels({ providerId, refresh: true, signal: controller.signal });
            if (!controller.signal.aborted) setModels(catalog);
          } catch (reason) {
            if (!controller.signal.aborted) setCatalogError(errorMessage(reason));
          }
        }
      } catch (reason) {
        if (!controller.signal.aborted) setError(errorMessage(reason));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    if (providerId) void load();
    return () => controller.abort();
  }, [adapter, providerId, refresh]);
  useEffect(() => () => operation.current?.abort(), []);
  const run = async (action: 'test' | 'save') => {
    if (operation.current) return;
    const controller = new AbortController(); operation.current = controller;
    setBusy(action); setError('');
    try {
      if (action === 'test') {
        setProbe(null);
        const result = await api<ImageProbe>('/settings/ai/vision/probe', { method: 'POST', body: selection, signal: controller.signal });
        if (!controller.signal.aborted) setProbe(result);
      } else {
        const saved = await api<VisionConnectionState>('/settings/ai/vision', { method: 'PUT', signal: controller.signal,
          body: { enabled, selection: selection.modelId && selection.providerId ? selection : null,
            expected_version: data.version, ...(enabled ? { probe_token: probe?.probe_token } : {}) } });
        if (!controller.signal.aborted) onSaved(saved);
      }
    } catch (reason) {
      if (!controller.signal.aborted) { setError(errorMessage(reason)); if (action === 'save') setProbe(null) }
    } finally {
      if (!controller.signal.aborted) setBusy('');
      if (operation.current === controller) operation.current = null;
    }
  };
  const canTest = configured && !!model.trim() && !loading;
  const providerOptions = providers.map(item => ({ value: item.id, label: item.name }));
  if (legacy) providerOptions.push({ value: legacy.providerId, label: legacy.custom.displayName || '已有自定义连接' });
  return <div className="ai-vision-form">
    <label className="ai-vision-toggle"><input type="checkbox" checked={enabled} disabled={!!busy} onChange={event => setEnabled(event.target.checked)} />为新面试启用视觉建议</label>
    {data.enabled && <p className="mck-status">{data.verified ? `已保存：${data.selection?.modelId}` : '认证已变更或断开，请重新测试并保存。'}</p>}
    <p className="ai-field-hint">复用上方已连接的服务商认证，文字模型选择保持独立。尚未连接时，请先在上方连接该服务商。</p>
    <label>视觉服务商<Select aria-label="视觉服务商" value={providerId} options={providerOptions} disabled={!!busy || !providers.length} onValueChange={value => {
      setProviderId(value); setModel(''); setModels([]); setProbe(null); setError(''); setCatalogError(''); setConfigured(false); setLoading(true);
    }} /></label>
    <div className="ai-model-field">
      <div className="ai-model-field-heading"><label htmlFor="ai-vision-model">视觉模型名称</label><button type="button" className="text-button" disabled={!!busy || loading} onClick={() => { setProbe(null); setError(''); setCatalogError(''); setRefresh(value => value + 1) }}>刷新认证与模型</button></div>
      <Combobox id="ai-vision-model" aria-label="视觉模型名称" value={model} options={models.map(item => ({ value: item.id, label: item.name }))} placeholder="选择或填写支持图片的模型 ID" disabled={!!busy || loading} onValueChange={value => {
        const matches = models.filter(item => item.name === value);
        setModel(models.some(item => item.id === value) ? value : matches.length === 1 ? matches[0].id : value); setProbe(null);
      }} />
    </div>
    {!loading && !configured && <Notice tone="info">此服务商尚未连接，请在上方完成认证后刷新。</Notice>}
    {catalogError && <p className="ai-catalog-message">{catalogError} 可手动填写模型 ID。</p>}
    <p className="mck-billing-note">图片测试使用固定色块，不访问摄像头，可能消耗账户额度。模型列表仅供选择，需通过图片测试才能保存启用。</p>
    <p className="ai-field-hint">保存后，在新面试中点击「开启视觉建议」才会每 5 秒向所选模型发送一帧画面。可能产生费用，仅保存文字反馈，不保存画面，不参与评分。已有面试沿用原配置。</p>
    {probe && <p className="mck-probe" role="status">{probe.message}</p>}
    {error && <Notice>{error}</Notice>}
    <footer><button type="button" disabled={!!busy || !canTest} onClick={() => void run('test')}>{busy === 'test' ? '正在识别测试图片…' : '测试图片识别'}</button><button type="button" disabled={!!busy || (enabled && (!canTest || !probe?.probe_token))} onClick={() => void run('save')}>{busy === 'save' ? '保存中…' : '保存视觉设置'}</button></footer>
  </div>;
}
