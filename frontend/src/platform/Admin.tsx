import { useState } from 'react';
import type { FormEvent } from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from './auth';
import { api, errorMessage } from './api';
import { useResource } from './hooks';
import type { Agent, Capabilities, Provider } from './types';
import { Empty, Icon, Notice } from './ui';

const sceneLabels: Record<string, string> = { resume: '简历评分', questions: '面试出题', evaluate: '回答评分', followup: '追问', report: '报告', chat: 'AI 对话', vision: '仪态反馈' };
const tools = ['search_knowledge_base', 'lookup_resume_field', 'verify_code_snippet'];
const blankProvider = { name: '', base_url: '', model: '', api_key: '', enabled: true, vision: false };
const blankAgent = { name: '', scene: '', provider_id: '', prompt: '', tools: [] as string[], enabled: true };

export function AdminPage() {
  const { user } = useAuth();
  const providers = useResource<Provider[]>('/admin/providers');
  const agents = useResource<Agent[]>('/admin/agents');
  const capabilities = useResource<Capabilities>('/admin/capabilities');
  const [tab, setTab] = useState('providers');
  const [providerId, setProviderId] = useState<string | null>(null);
  const [provider, setProvider] = useState({ ...blankProvider });
  const [agentId, setAgentId] = useState<string | null>(null);
  const [agent, setAgent] = useState({ ...blankAgent });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  if (user?.role !== 'admin') return <Navigate to="/" replace />;
  const saveProvider = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError(''); setSuccess('');
    try {
      await api(`/admin/providers${providerId ? `/${providerId}` : ''}`, { method: providerId ? 'PUT' : 'POST', body: { ...provider, api_key: provider.api_key || undefined } });
      setProviderId(null); setProvider({ ...blankProvider }); await providers.refresh(); setSuccess('模型配置已保存。');
    } catch (err) { setError(errorMessage(err)) } finally { setBusy(false) }
  };
  const saveAgent = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError(''); setSuccess('');
    try {
      await api(`/admin/agents${agentId ? `/${agentId}` : ''}`, { method: agentId ? 'PUT' : 'POST', body: { ...agent, scene: agent.scene || null, provider_id: agent.provider_id || null } });
      setAgentId(null); setAgent({ ...blankAgent }); await agents.refresh(); setSuccess('智能体配置已保存。');
    } catch (err) { setError(errorMessage(err)) } finally { setBusy(false) }
  };
  const remove = async (type: 'providers' | 'agents', id: string) => {
    if (!window.confirm(type === 'providers' ? '删除这个模型配置？正在被智能体引用的配置可能无法删除。' : '删除这个智能体？历史对话将保留。')) return;
    setBusy(true); setError(''); setSuccess('');
    try { await api(`/admin/${type}/${id}`, { method: 'DELETE' }); await (type === 'providers' ? providers.refresh() : agents.refresh()); setSuccess('配置已删除。') }
    catch (err) { setError(errorMessage(err)) } finally { setBusy(false) }
  };
  return <><div className="page-heading compact"><span className="eyebrow">MAKE IT YOURS</span><h1>让每个助手，各司其职。</h1><p className="muted">管理模型连接与各场景的智能体，配置仅管理员可见。</p></div><div className="capability-row">{[['演示模式', capabilities.data?.demo_mode], ['语音转写', capabilities.data?.asr], ['问题朗读', capabilities.data?.tts], ['仪态反馈', capabilities.data?.vision]].map(([label, enabled]) => <span key={String(label)} className={`badge ${enabled ? 'badge-green' : ''}`}><span className="status-dot" />{label}{enabled ? ' · 已开启' : ' · 未开启'}</span>)}</div>{(error || providers.error || agents.error || capabilities.error) && <Notice>{error || providers.error || agents.error || capabilities.error}</Notice>}{success && <Notice tone="success">{success}</Notice>}<div className="tabs admin-tabs">{[['providers', '模型连接'], ['agents', '智能体配置']].map(([key, label]) => <button key={key} className={tab === key ? 'active' : ''} onClick={() => { setTab(key); setError(''); setSuccess('') }}>{label}</button>)}</div>{tab === 'providers' ? <div className="admin-grid"><section className="card"><div className="section-heading"><h2>已连接的模型</h2><span className="badge">{providers.data?.length ?? 0}</span></div>{!providers.data?.length ? <Empty title="连接第一个模型">支持 OpenAI 兼容协议的服务。</Empty> : providers.data.map(item => <article className="config-row" key={item.id}><div><h3>{item.name}<span className={`badge ${item.enabled ? 'badge-green' : ''}`}>{item.enabled ? '已启用' : '已停用'}</span></h3><p>{item.model}</p><small className="muted config-url">{item.base_url}</small><small className="muted">密钥：{item.api_key_masked ?? (item.has_key ? '••••••••（已保存）' : '未设置')}{item.vision ? ' · 支持视觉' : ''}</small></div><div className="config-actions"><button className="text-button" disabled={busy} onClick={() => { setProviderId(item.id); setProvider({ name: item.name, base_url: item.base_url, model: item.model, api_key: '', enabled: item.enabled, vision: item.vision }) }}>编辑</button><button className="text-button danger" disabled={busy} onClick={() => void remove('providers', item.id)}>删除</button></div></article>)}</section><form className="card settings-form" onSubmit={saveProvider}><div className="section-heading"><h2>{providerId ? '编辑模型连接' : '添加模型连接'}</h2>{providerId && <button className="text-button" type="button" onClick={() => { setProviderId(null); setProvider({ ...blankProvider }) }}>取消编辑</button>}</div><label>连接名称<input required maxLength={100} value={provider.name} onChange={event => setProvider({ ...provider, name: event.target.value })} placeholder="例如：DeepSeek 主模型" /></label><label>API 地址<input required type="url" value={provider.base_url} onChange={event => setProvider({ ...provider, base_url: event.target.value })} placeholder="https://api.example.com/v1" /></label><label>模型名称<input required value={provider.model} onChange={event => setProvider({ ...provider, model: event.target.value })} placeholder="deepseek-chat" /></label><label>API 密钥<input type="password" autoComplete="new-password" value={provider.api_key} onChange={event => setProvider({ ...provider, api_key: event.target.value })} placeholder={providerId ? '留空保留现有密钥' : '输入服务商提供的 API Key'} /></label><div className="checkbox-row"><label><input type="checkbox" checked={provider.enabled} onChange={event => setProvider({ ...provider, enabled: event.target.checked })} />启用此模型</label><label><input type="checkbox" checked={provider.vision} onChange={event => setProvider({ ...provider, vision: event.target.checked })} />支持图片输入</label></div><p className="small muted">API 密钥由服务器保存，编辑页面只返回脱敏值。</p><button className="btn primary" disabled={busy}><Icon name="check" size={17} />{busy ? '正在保存…' : '保存模型配置'}</button></form></div> : <div className="admin-grid"><section className="card"><div className="section-heading"><h2>场景智能体</h2><span className="badge">{agents.data?.length ?? 0}</span></div>{!agents.data?.length ? <Empty title="创建你的第一个智能体">为不同场景设置模型与提示词。</Empty> : agents.data.map(item => <article className="config-row" key={item.id}><div><h3>{item.name}<span className={`badge ${item.enabled ? 'badge-green' : ''}`}>{item.enabled ? '已启用' : '已停用'}</span></h3><p>{item.scene ? sceneLabels[item.scene] ?? item.scene : '独立对话助手'} · {providers.data?.find(value => value.id === item.provider_id)?.name ?? '默认模型'}</p><small className="muted">{item.tools?.length ? `${item.tools.length} 个工具` : '未绑定工具'}</small></div><div className="config-actions"><button className="text-button" disabled={busy} onClick={() => { setAgentId(item.id); setAgent({ name: item.name, scene: item.scene ?? '', provider_id: item.provider_id ?? '', prompt: item.prompt ?? '', tools: item.tools ?? [], enabled: item.enabled ?? true }) }}>编辑</button><button className="text-button danger" disabled={busy} onClick={() => void remove('agents', item.id)}>删除</button></div></article>)}</section><form className="card settings-form" onSubmit={saveAgent}><div className="section-heading"><h2>{agentId ? '编辑智能体' : '添加智能体'}</h2>{agentId && <button className="text-button" type="button" onClick={() => { setAgentId(null); setAgent({ ...blankAgent }) }}>取消编辑</button>}</div><label>智能体名称<input required value={agent.name} maxLength={100} onChange={event => setAgent({ ...agent, name: event.target.value })} placeholder="例如：项目深挖面试官" /></label><label>使用场景<select value={agent.scene} onChange={event => setAgent({ ...agent, scene: event.target.value })}><option value="">独立对话助手（用户自行选择）</option>{Object.entries(sceneLabels).map(([key, value]) => <option key={key} value={key}>{value}</option>)}</select></label><label>使用模型<select value={agent.provider_id} onChange={event => setAgent({ ...agent, provider_id: event.target.value })}><option value="">使用服务器默认模型</option>{providers.data?.map(item => <option key={item.id} value={item.id}>{item.name}{item.enabled ? '' : '（已停用）'}</option>)}</select></label><label>系统提示词<textarea rows={6} value={agent.prompt} onChange={event => setAgent({ ...agent, prompt: event.target.value })} placeholder="描述助手的角色、回答风格和任务要求…" /></label><fieldset><legend>可用工具</legend>{tools.map(tool => <label className="checkbox-label" key={tool}><input type="checkbox" checked={agent.tools.includes(tool)} onChange={event => setAgent({ ...agent, tools: event.target.checked ? [...agent.tools, tool] : agent.tools.filter(value => value !== tool) })} />{tool}</label>)}</fieldset><label className="checkbox-label"><input type="checkbox" checked={agent.enabled} onChange={event => setAgent({ ...agent, enabled: event.target.checked })} />启用此智能体</label><button className="btn primary" disabled={busy}><Icon name="check" size={17} />{busy ? '正在保存…' : '保存智能体'}</button></form></div>}</>;
}
