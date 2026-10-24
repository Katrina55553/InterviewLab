import { useCallback, useMemo, useState } from 'react';
import { ModelSettingsPanel } from 'model-connection-kit';
import type { ModelSelection } from 'model-connection-kit';
import 'model-connection-kit/styles.css';
import './ai-settings.css';
import { api } from './api';
import { useResource } from './hooks';
import { createUserModelAdapter } from './modelAdapter';
import type { AIConnectionState, AIProvider, OAuthAuthorization } from './modelAdapter';
import { APIKeyConnection } from './APIKeyConnection';
import { SubscriptionAuthorization } from './SubscriptionAuthorization';
import { Icon, Loading, Notice } from './ui';

type ConnectionMode = 'subscription' | 'api-key';
const connectionMethods: { id: ConnectionMode; label: string }[] = [
  { id: 'subscription', label: 'ChatGPT 订阅登录' }, { id: 'api-key', label: 'API Key' },
];

function ShieldIcon() {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6l-8-3Z" /><path d="m8.5 12 2.5 2.5 4.5-5" /></svg>;
}

export function AISettingsPage() {
  const { data, error, refresh, setData } = useResource<AIConnectionState>('/settings/ai');
  const { data: providers, error: providersError, refresh: refreshProviders } = useResource<AIProvider[]>('/settings/ai/providers');
  const [mode, setMode] = useState<ConnectionMode | null>(null);
  const [authorization, setAuthorization] = useState<OAuthAuthorization | null>(null);
  const [draft, setDraft] = useState<ModelSelection | null>(null);
  const [connectionVersion, setConnectionVersion] = useState(0);
  const adapter = useMemo(() => createUserModelAdapter(async () => { setDraft(null); return refresh() }, setAuthorization), [refresh]);
  const [success, setSuccess] = useState('');
  const connected = useCallback(() => {
    setAuthorization(null);
    setDraft({ connectionType: 'builtin', authMethod: 'oauth', providerId: 'openai', modelId: '' });
    setMode('subscription');
    setConnectionVersion(version => version + 1);
    setSuccess('ChatGPT 订阅已连接。请选择模型并保存，连接测试会使用订阅额度。');
  }, []);
  const save = async (selection: ModelSelection) => {
    if (!data) return;
    setSuccess('');
    const saved = await api<AIConnectionState>('/settings/ai', { method: 'PUT', body: { selection, expected_version: data.version } });
    setData(saved);
    setDraft(null);
    setSuccess('模型选择已保存。新的诊断、面试和后续聊天将使用此连接。');
  };
  const current = draft ?? data?.selection;
  const activeMode = mode ?? (current?.authMethod === 'oauth' ? 'subscription' : 'api-key');
  const providerName = providers?.find(provider => provider.id === data?.selection?.providerId)?.name ?? data?.selection?.providerId;
  return <div className="ai-settings-page">
    <header className="ai-settings-heading">
      <div><span className="eyebrow">MAKE IT YOURS</span><h1>AI 设置<span className="ai-heading-dot">.</span></h1><p>连接你偏好的模型，为每一次准备提供更合适的 AI 助力。</p></div>
      <span className="ai-security-label"><ShieldIcon />个人专属 · 加密存储</span>
    </header>
    {error && <Notice>{error}<button className="text-button" onClick={() => void refresh()}>重新加载</button></Notice>}
    {success && <Notice tone="success">{success}</Notice>}
    {!data ? !error && <Loading text="正在读取模型设置…" /> : <>
      <section className={`ai-current-connection${data.selection ? ' is-configured' : ''}`} aria-labelledby="ai-current-model">
        <div className="ai-model-summary">
          <span className="ai-model-icon"><Icon name="spark" size={25} /></span>
          <div className="ai-model-copy">
            <div className="ai-model-label"><span>当前使用的模型</span><span className={`ai-connection-status${data.selection ? ' is-saved' : ''}`}>{data.selection ? <Icon name="check" size={12} /> : <span className="status-dot" />}{data.selection ? '已保存' : '待配置'}</span></div>
            <h2 id="ai-current-model">{data.selection?.modelId ?? '尚未选择模型'}</h2>
            <p>{data.selection?.connectionType === 'custom' ? data.selection.custom.displayName || data.selection.custom.baseUrl : data.selection ? data.selection.authMethod === 'oauth' ? 'ChatGPT · 订阅连接' : `${providerName} · 个人连接` : '从下方选择服务，连接后保存即可使用。'}</p>
          </div>
        </div>
        <dl className="ai-connection-meta">
          <div><dt>连接方式</dt><dd>{data.selection ? data.selection.connectionType === 'custom' ? '自定义接口' : data.selection.authMethod === 'oauth' ? 'ChatGPT 订阅登录' : 'API Key' : '等待连接'}</dd></div>
          <div><dt>使用额度</dt><dd>{data.selection?.authMethod === 'oauth' ? 'ChatGPT 计划' : '个人服务账户'}</dd></div>
        </dl>
      </section>
      {authorization && <SubscriptionAuthorization key={authorization.session_id} session={authorization} onConnected={connected} onCancel={() => setAuthorization(null)} />}
      <div className="ai-settings-layout">
        <section className="mck-panel ai-model-settings" aria-labelledby="ai-connection-title">
          <header><p className="mck-eyebrow">MODEL CONNECTION</p><h2 id="ai-connection-title">配置模型连接</h2><p>选择连接方式，接入你的 AI 模型服务。</p></header>
          <div className="mck-tabs" role="tablist" aria-label="连接方式" onKeyDown={event => {
            const index = connectionMethods.findIndex(method => method.id === activeMode);
            const count = connectionMethods.length;
            const next = event.key === 'ArrowRight' ? (index + 1) % count : event.key === 'ArrowLeft' ? (index + count - 1) % count : event.key === 'Home' ? 0 : event.key === 'End' ? count - 1 : null;
            if (next === null) return;
            event.preventDefault(); setMode(connectionMethods[next].id);
            event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next].focus();
          }}>{connectionMethods.map(method => <button type="button" role="tab" id={`ai-tab-${method.id}`} aria-controls="ai-connection-content" aria-selected={activeMode === method.id} tabIndex={activeMode === method.id ? 0 : -1} key={method.id} onClick={() => setMode(method.id)}>{method.id === 'subscription' ? <>ChatGPT <span className="ai-tab-label">订阅登录</span></> : method.label}</button>)}</div>
          <div role="tabpanel" id="ai-connection-content" aria-labelledby={`ai-tab-${activeMode}`}>
            {activeMode === 'api-key' ? <>
              {providersError && <Notice>{providersError}<button className="text-button" onClick={() => void refreshProviders()}>重新加载</button></Notice>}
              {!providers ? !providersError && <Loading text="正在读取服务商…" /> : <APIKeyConnection key={`${data.version}:${data.selection?.providerId ?? 'new'}`} providers={providers.filter(provider => provider.auth.apiKey?.interactive)} value={data.selection} adapter={adapter} onSave={save} />}
            </> : <ModelSettingsPanel key={`${activeMode}:${data.version}:${data.selection?.providerId ?? 'new'}:${connectionVersion}`} className="ai-embedded-kit" defaultMode={activeMode} adapter={adapter} value={draft ?? data.selection} onSave={save}
              text={{ noProviders: '暂无支持此认证方式的服务商，请选择其他连接方式。', noModels: '连接后选择可用模型；若列表为空，请重新连接或重试。', subscriptionTitle: '使用 ChatGPT 订阅', subscriptionDescription: '点击「连接」发起 OpenAI 授权。登录完成后选择模型并保存，后续请求使用你的 ChatGPT 计划额度。' }} />}
          </div>
        </section>
        <aside className="ai-settings-aside" aria-label="模型配置帮助">
          <section className="ai-help-card" aria-labelledby="ai-guide-title">
            <div className="ai-help-heading"><Icon name="settings" size={17} /><h2 id="ai-guide-title">连接，从这里开始</h2></div>
            <ol className="ai-setup-steps">
              <li><span>01</span><div><h3>选择连接方式</h3><p>登录 ChatGPT 订阅，或选择服务商使用 API Key。</p></div></li>
              <li><span>02</span><div><h3>完成认证并连接</h3><p>完成订阅授权，或选择 API 服务商，自动填入地址后输入密钥。</p></div></li>
              <li><span>03</span><div><h3>选择模型并保存</h3><p>点击「保存」后，新的任务将使用所选模型。</p></div></li>
            </ol>
          </section>
          <section className="ai-help-card ai-privacy-card" aria-labelledby="ai-privacy-title">
            <div className="ai-help-heading"><ShieldIcon /><h2 id="ai-privacy-title">只属于你的 AI 连接</h2></div>
            <p>API Key 与订阅凭据加密保存，仅供你的账户调用，不会回显。</p>
            <div className="ai-scope"><span>保存后应用于</span><ul><li><Icon name="scan" size={14} />简历诊断</li><li><Icon name="headphones" size={14} />模拟面试</li><li><Icon name="chat" size={14} />后续聊天</li></ul></div>
          </section>
        </aside>
      </div>
      <p className="ai-settings-footnote"><ShieldIcon />连接仅保存认证信息，点击「保存」才会切换当前模型。</p>
    </>}
  </div>;
}
