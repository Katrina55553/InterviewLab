import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { api, errorMessage } from './api';
import { useAuth } from './auth';
import { EmailCodeField } from './EmailCodeField';
import { useResource } from './hooks';
import { Icon, Notice } from './ui';
import './security.css';

type Mode = 'login' | 'register' | 'reset';

export function AuthPage() {
  const [mode, setMode] = useState<Mode>('login');
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const location = useLocation();
  const { user, authenticate, notice } = useAuth();
  const [success, setSuccess] = useState(notice || (typeof location.state?.message === 'string' ? location.state.message : ''));
  useEffect(() => { if (notice) useAuth.setState({ notice: '' }) }, [notice]);
  const { data: capabilities, error: capabilitiesError, refresh: refreshCapabilities } = useResource<{ email_enabled: boolean }>('/auth/capabilities');
  const navigate = useNavigate();
  const emailEnabled = capabilities?.email_enabled === true;
  const changeMode = (next: Mode) => { setMode(next); setPassword(''); setConfirmation(''); setCode(''); setEmail(''); setError(''); setSuccess('') };
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError(''); setSuccess('');
    if (mode !== 'login' && !emailEnabled) return;
    if (mode !== 'login' && password !== confirmation) { setError('两次输入的密码不一致。'); return }
    if (mode === 'register' && !/^[\p{L}\p{N}_.-]{3,64}$/u.test(username.trim())) { setError('用户名须为 3–64 个字母、数字、汉字或 . _ -，不能包含 @。'); return }
    setBusy(true);
    try {
      if (mode === 'reset') {
        await api('/auth/password/reset', { method: 'POST', body: { email: email.trim().toLowerCase(), code, new_password: password } });
        setMode('login'); setPassword(''); setConfirmation(''); setCode(''); setUsername(email.trim()); setEmail('');
        setSuccess('密码已重置，请使用新密码登录。');
      } else {
        await authenticate(mode, username.trim(), password, mode === 'register' ? { email: email.trim().toLowerCase(), code } : undefined);
        setPassword(''); setConfirmation(''); setCode('');
        navigate('/', { replace: true });
      }
    } catch (err) { setError(errorMessage(err)) }
    finally { setBusy(false) }
  };
  if (user) return <Navigate to="/" replace />;
  return <div className="auth-page bg-noise"><div className="auth-story"><div className="brand"><span className="brand-icon"><Icon name="spark" /></span>知面 <small>InterviewLab</small></div><div className="auth-editorial"><span className="eyebrow">PREPARE WITH PURPOSE</span><h1>把每一次准备，<br />变成<span>更好的回答。</span></h1><p>从一份简历出发，在真实的问答里发现优势，<br />练习表达，看见每一步成长。</p><div className="auth-steps"><span>01 简历解析</span><i /><span>02 模拟面试</span><i /><span>03 复盘提升</span></div></div><p className="muted small">你的下一次面试，从这里开始。</p></div>
    <div className="auth-form-wrap"><form className="auth-form" onSubmit={event => void submit(event)} aria-label={mode === 'login' ? '登录' : mode === 'register' ? '注册' : '找回密码'}>
      <span className="eyebrow">YOUR NEXT CHAPTER</span><h2>{mode === 'login' ? '欢迎回来' : mode === 'register' ? '开始你的练习' : '找回密码'}</h2>
      <p className="muted">{mode === 'login' ? '登录，继续积累属于你的面试经验。' : mode === 'register' ? '验证邮箱，创建属于你的面试练习账号。' : '使用已绑定邮箱的验证码，设置新的登录密码。'}</p>
      {mode !== 'login' && !emailEnabled && <Notice tone="info">{capabilitiesError ? <>邮箱服务状态读取失败。<button type="button" className="text-button" onClick={() => void refreshCapabilities()}>重新加载</button></> : capabilities ? '邮箱服务暂未启用，请联系管理员。已有账号仍可登录。' : '正在检查邮箱服务…'}</Notice>}
      {mode !== 'reset' && <label>{mode === 'login' ? '用户名或邮箱' : '用户名'}<input name="username" autoComplete="username" minLength={3} maxLength={mode === 'login' ? 254 : 64} required disabled={busy} value={username} onChange={event => setUsername(event.target.value)} placeholder={mode === 'login' ? '用户名或已绑定邮箱' : '3–64 个字符，不含 @'} /></label>}
      {mode !== 'login' && <><label>邮箱<input name="email" type="email" autoComplete="email" maxLength={254} required disabled={busy || !emailEnabled} value={email} onChange={event => { setEmail(event.target.value); setCode(''); setError('') }} placeholder={mode === 'register' ? '用于登录和找回密码' : '你的已绑定邮箱'} /></label>
        <EmailCodeField key={`${mode}:${email.trim().toLowerCase()}`} email={email.trim().toLowerCase()} code={code} onCodeChange={setCode} enabled={emailEnabled && !busy} purpose={mode} /></>}
      <label>{mode === 'reset' ? '新密码' : '密码'}<input name="password" type="password" autoComplete={mode === 'login' ? 'current-password' : 'new-password'} minLength={8} maxLength={128} required disabled={busy || (mode !== 'login' && !emailEnabled)} value={password} onChange={event => setPassword(event.target.value)} placeholder="8–128 个字符" /></label>
      {mode !== 'login' && <label>确认密码<input name="confirmation" type="password" autoComplete="new-password" minLength={8} maxLength={128} required disabled={busy || !emailEnabled} value={confirmation} onChange={event => setConfirmation(event.target.value)} placeholder="再次输入密码" /></label>}
      {error && <Notice>{error}</Notice>}{success && <Notice tone="success">{success}</Notice>}
      <button className="btn primary wide" disabled={busy || (mode !== 'login' && !emailEnabled)}>{busy ? '请稍候…' : mode === 'login' ? '登录知面' : mode === 'register' ? '创建账号' : '重置密码'}<Icon name="arrow" size={18} /></button>
      {mode === 'login' && <div className="auth-forgot"><button type="button" className="text-button" disabled={busy} onClick={() => changeMode('reset')}>忘记密码？</button></div>}
      {mode === 'reset' && <p className="small muted">尚未绑定邮箱的旧账号，请联系管理员核验身份后重置密码。</p>}
      <p className="auth-switch">{mode === 'login' ? '还没有账号？' : '已经有账号？'}<button type="button" className="text-button" disabled={busy} onClick={() => changeMode(mode === 'login' ? 'register' : 'login')}>{mode === 'login' ? '创建一个' : '去登录'}</button></p>
    </form></div></div>;
}
