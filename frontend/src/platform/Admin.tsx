import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link, Navigate } from 'react-router-dom';
import { ConfigHistory } from './ConfigHistory';
import { useAuth } from './auth';
import { api, errorMessage } from './api';
import { useResource } from './hooks';
import type { AdminCapabilities, Agent } from './types';
import { Empty, Icon, Loading, Notice } from './ui';
import { Select } from './Select';

const sceneLabels: Record<string, string> = { parse: '简历解析', resume: '简历诊断', questions: '面试出题', evaluate: '回答评分', followup: '追问', report: '报告', chat: 'JD分析', vision: '仪态反馈' };
const tools = ['lookup_resume_field', 'verify_code_snippet'];
const blankAgent = { name: '', scene: '', prompt: '', tools: [] as string[], enabled: true };

export function AdminPage() {
  const { user } = useAuth();
  return user?.role === 'admin' ? <AgentSettings /> : <Navigate to="/" replace />;
}

function AgentSettings() {
  const agents = useResource<Agent[]>('/admin/agents');
  const capabilities = useResource<AdminCapabilities>('/admin/capabilities');
  const [revision, setRevision] = useState<{ id: string; version: number } | null>(null);
  const [agentVersion, setAgentVersion] = useState(1);
  const [agentId, setAgentId] = useState<string | null>(null);
  const [agent, setAgent] = useState({ ...blankAgent });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const resetForm = () => { setAgentId(null); setAgent({ ...blankAgent }) };
  const refresh = async () => { await Promise.all([agents.refresh(), capabilities.refresh()]) };
  const saveAgent = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError(''); setSuccess('');
    try {
      await api(`/admin/agents${agentId ? `/${agentId}` : ''}`, {
        method: agentId ? 'PUT' : 'POST',
        body: { ...agent, ...(agentId ? { expected_version: agentVersion } : {}), scene: agent.scene || null },
      });
      resetForm(); setRevision(null); await refresh(); setSuccess('智能体配置已保存。');
    } catch (err) { setError(errorMessage(err)) } finally { setBusy(false) }
  };
  const remove = async (item: Agent) => {
    if (!window.confirm('删除这个智能体？历史记录将保留；仍绑定它的对话和面试将无法继续调用。')) return;
    setBusy(true); setError(''); setSuccess('');
    try {
      await api(`/admin/agents/${item.id}?expected_version=${item.version}`, { method: 'DELETE' });
      if (agentId === item.id) resetForm();
      setRevision(null); await refresh(); setSuccess('智能体已删除。');
    } catch (err) { setError(errorMessage(err)) } finally { setBusy(false) }
  };
  const statuses = [
    { label: '演示模式', enabled: capabilities.data?.demo_mode, on: '已开启', off: '未开启' },
    { label: '语音转写', enabled: capabilities.data?.asr, on: '已配置', off: '未配置' },
    { label: '问题朗读', enabled: capabilities.data?.tts, on: '已配置', off: '未配置' },
    { label: '仪态反馈策略', enabled: capabilities.data?.scenes.vision.enabled, on: '允许使用', off: '已停用' },
  ];

  return <>
    <div className="page-heading compact">
      <span className="eyebrow">SCENES & AGENTS</span>
      <h1>场景与智能体管理</h1>
      <p className="muted">管理各场景的提示词、工具与启用策略。模型和 API Key 由每位用户在<Link to="/settings/ai">「AI 设置」</Link>中配置。</p>
    </div>
    <div className="capability-row">{statuses.map(({ label, enabled, on, off }) => <span key={label} className={`badge ${!capabilities.error && enabled ? 'badge-green' : ''}`}>
      <span className="status-dot" />{label} · {capabilities.error ? '状态未知' : enabled === undefined ? '读取中…' : enabled ? on : off}
    </span>)}</div>
    <p className="small muted">仪态反馈还需用户启用支持图片输入的个人模型。提示词与工具修改用于新的诊断、面试及后续聊天；停用会阻止绑定该智能体的后续调用。</p>
    {(error || agents.error || capabilities.error) && <Notice>{error || agents.error || capabilities.error}</Notice>}
    {success && <Notice tone="success">{success}</Notice>}
    {revision && <>
      <ConfigHistory key={revision.id} kind="agent" {...revision} onRestored={() => { setRevision(null); resetForm(); void refresh() }} />
      <button className="text-button" onClick={() => setRevision(null)}>关闭历史</button>
    </>}
    <div className="admin-grid">
      <section className="card">
        <div className="section-heading"><h2>场景智能体</h2><span className="badge">{agents.data?.length ?? 0}</span></div>
        {!agents.data ? (agents.error ? <Notice>{agents.error}</Notice> : <Loading text="正在读取智能体…" />)
          : !agents.data.length ? <Empty title="创建你的第一个智能体">为不同场景设置提示词、工具与启用策略。未配置的场景使用内置默认设置。</Empty>
            : agents.data.map(item => <article className="config-row" key={item.id}>
              <div>
                <h3>{item.name}<span className={`badge ${item.enabled ? 'badge-green' : ''}`}>{item.enabled ? '已启用' : '已停用'}</span></h3>
                <p>{item.scene ? sceneLabels[item.scene] ?? item.scene : '独立对话助手'}</p>
                <small className="muted">{item.tools?.length ? `${item.tools.length} 个工具` : '未绑定工具'}</small>
              </div>
              <div className="config-actions">
                <button className="text-button" disabled={busy} onClick={() => setRevision({ id: item.id, version: item.version })}>历史</button>
                <button className="text-button" disabled={busy} onClick={() => {
                  setAgentId(item.id); setAgentVersion(item.version);
                  setAgent({ name: item.name, scene: item.scene ?? '', prompt: item.prompt ?? '', tools: item.tools ?? [], enabled: item.enabled ?? true });
                  setError(''); setSuccess('');
                }}>编辑</button>
                <button className="text-button danger" disabled={busy} onClick={() => void remove(item)}>删除</button>
              </div>
            </article>)}
      </section>
      <form className="card settings-form" onSubmit={saveAgent}>
        <div className="section-heading"><h2>{agentId ? '编辑智能体' : '添加智能体'}</h2>{agentId && <button className="text-button" disabled={busy} type="button" onClick={resetForm}>取消编辑</button>}</div>
        <label>智能体名称<input required value={agent.name} maxLength={100} onChange={event => setAgent({ ...agent, name: event.target.value })} placeholder="例如：项目深挖面试官" /></label>
        <label>使用场景<Select aria-label="使用场景" value={agent.scene} onValueChange={scene => setAgent({ ...agent, scene })} options={[{ value: '', label: '独立对话助手' }, ...Object.entries(sceneLabels).map(([value, label]) => ({ value, label }))]} /></label>
        <label>系统提示词<textarea rows={6} value={agent.prompt} onChange={event => setAgent({ ...agent, prompt: event.target.value })} placeholder="描述助手的角色、回答风格和任务要求…" /></label>
        <fieldset><legend>可用工具</legend>{tools.map(tool => <label className="checkbox-label" key={tool}><input type="checkbox" checked={agent.tools.includes(tool)} onChange={event => setAgent({ ...agent, tools: event.target.checked ? [...agent.tools, tool] : agent.tools.filter(value => value !== tool) })} />{tool}</label>)}</fieldset>
        <label className="checkbox-label"><input type="checkbox" checked={agent.enabled} onChange={event => setAgent({ ...agent, enabled: event.target.checked })} />启用此智能体</label>
        <button className="btn primary" disabled={busy}><Icon name="check" size={17} />{busy ? '正在保存…' : '保存智能体'}</button>
      </form>
    </div>
  </>;
}
