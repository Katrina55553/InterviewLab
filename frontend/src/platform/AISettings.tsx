import { useMemo, useState } from 'react';
import { ModelSettingsPanel } from 'model-connection-kit';
import type { ModelSelection } from 'model-connection-kit';
import 'model-connection-kit/styles.css';
import './ai-settings.css';
import { api } from './api';
import { useResource } from './hooks';
import { createUserModelAdapter } from './modelAdapter';
import type { AIConnectionState } from './modelAdapter';
import { Icon, Loading, Notice } from './ui';

const initialSelection: ModelSelection = { connectionType: 'builtin', authMethod: 'api-key', providerId: 'deepseek', modelId: 'deepseek-chat' };

function ShieldIcon() {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6l-8-3Z" /><path d="m8.5 12 2.5 2.5 4.5-5" /></svg>;
}

export function AISettingsPage() {
  const { data, error, refresh, setData } = useResource<AIConnectionState>('/settings/ai');
  const adapter = useMemo(() => createUserModelAdapter(refresh), [refresh]);
  const [success, setSuccess] = useState('');
  const save = async (selection: ModelSelection) => {
    if (!data) return;
    setSuccess('');
    const saved = await api<AIConnectionState>('/settings/ai', { method: 'PUT', body: { selection, expected_version: data.version } });
    setData(saved);
    setSuccess('模型选择已保存。新的诊断、面试和后续聊天将使用此连接。');
  };
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
            <p>{data.selection?.connectionType === 'custom' ? data.selection.custom.displayName || data.selection.custom.baseUrl : data.selection ? 'DeepSeek · 个人连接' : '从下方选择服务，连接后保存即可使用。'}</p>
          </div>
        </div>
        <dl className="ai-connection-meta">
          <div><dt>连接方式</dt><dd>{data.selection ? data.selection.connectionType === 'custom' ? '自定义接口' : 'API Key' : '等待连接'}</dd></div>
          <div><dt>使用额度</dt><dd>个人服务账户</dd></div>
        </dl>
      </section>
      <div className="ai-settings-layout">
        <ModelSettingsPanel key={`${data.version}:${data.selection?.providerId ?? 'new'}`} adapter={adapter} value={data.selection ?? initialSelection} defaultMode="api-key" onSave={save}
          text={{ eyebrow: 'MODEL CONNECTION', title: '配置模型连接', description: '选择连接方式，接入你的 AI 模型服务。', noProviders: '此认证方式暂未接入，请使用 API Key 或自定义接口。', subscriptionDescription: '订阅登录暂未接入，请切换到 API Key 或自定义接口。', corsHint: '请填写已支持的服务地址与实际模型 ID；请求由服务器安全转发。', contextWindowLabel: '上下文窗口（Token）', maxTokensLabel: '输出上限（Token）' }} />
        <aside className="ai-settings-aside" aria-label="模型配置帮助">
          <section className="ai-help-card" aria-labelledby="ai-guide-title">
            <div className="ai-help-heading"><Icon name="settings" size={17} /><h2 id="ai-guide-title">连接，从这里开始</h2></div>
            <ol className="ai-setup-steps">
              <li><span>01</span><div><h3>选择连接方式</h3><p>使用服务商 API Key，或接入 OpenAI 兼容接口。</p></div></li>
              <li><span>02</span><div><h3>填写凭据并连接</h3><p>填入密钥，点击「连接」。可测试模型是否可用。</p></div></li>
              <li><span>03</span><div><h3>选择模型并保存</h3><p>点击「保存」后，新的任务将使用所选模型。</p></div></li>
            </ol>
          </section>
          <section className="ai-help-card ai-privacy-card" aria-labelledby="ai-privacy-title">
            <div className="ai-help-heading"><ShieldIcon /><h2 id="ai-privacy-title">只属于你的 AI 连接</h2></div>
            <p>API Key 加密保存，仅供你的账户调用。已保存的密钥不会回显。</p>
            <div className="ai-scope"><span>保存后应用于</span><ul><li><Icon name="scan" size={14} />简历诊断</li><li><Icon name="headphones" size={14} />模拟面试</li><li><Icon name="chat" size={14} />后续聊天</li></ul></div>
          </section>
          <details className="ai-supported-services"><summary><span><Icon name="book" size={16} />支持的自定义服务地址</span><Icon name="chevron" size={14} /></summary><div className="ai-supported-content"><p>可使用以下服务地址，其他地址请联系管理员添加。</p>{data.allowed_base_urls.length ? <ul>{data.allowed_base_urls.map(url => <li key={url}><code>{url}</code></li>)}</ul> : <p>暂未配置自定义服务地址。</p>}</div></details>
        </aside>
      </div>
      <p className="ai-settings-footnote"><ShieldIcon />连接仅保存认证信息，点击「保存」才会切换当前模型。</p>
    </>}
  </div>;
}
