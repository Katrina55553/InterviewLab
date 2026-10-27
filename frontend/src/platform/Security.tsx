import { useState } from 'react';
import type { FormEvent } from 'react';
import { api, errorMessage } from './api';
import { useAuth } from './auth';
import { EmailCodeField } from './EmailCodeField';
import { useResource } from './hooks';
import { Loading, Notice } from './ui';
import './security.css';

export function SecurityPage() {
  const { data, error: loadError, refresh } = useResource<{ email: string | null }>('/auth/security');
  const { data: capabilities, error: capabilitiesError, refresh: refreshCapabilities } = useResource<{ email_enabled: boolean }>('/auth/capabilities');
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [bindingPassword, setBindingPassword] = useState('');
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState<'password' | 'email' | null>(null);
  const [passwordError, setPasswordError] = useState('');
  const [emailError, setEmailError] = useState('');
  const [success, setSuccess] = useState('');
  const emailEnabled = capabilities?.email_enabled === true;
  const changePassword = async (event: FormEvent) => {
    event.preventDefault(); setPasswordError(''); setSuccess('');
    if (newPassword !== confirmation) { setPasswordError('两次输入的密码不一致。'); return }
    if (newPassword === currentPassword) { setPasswordError('新密码不能与当前密码相同。'); return }
    setBusy('password');
    try {
      await api('/auth/password/change', { method: 'POST', body: { current_password: currentPassword, new_password: newPassword } });
      setCurrentPassword(''); setNewPassword(''); setConfirmation(''); setBindingPassword(''); setCode('');
      useAuth.setState({ user: null, loading: false, error: '', notice: '密码已修改，所有设备已退出，请使用新密码登录。' });
    } catch (err) { setPasswordError(errorMessage(err)) }
    finally { setBusy(null) }
  };
  const bindEmail = async (event: FormEvent) => {
    event.preventDefault(); setEmailError(''); setSuccess('');
    if (!emailEnabled) return;
    setBusy('email');
    try {
      await api('/auth/email/bind', { method: 'POST', body: { email: email.trim().toLowerCase(), code, current_password: bindingPassword } });
      setBindingPassword(''); setCode(''); setEmail('');
      setSuccess('邮箱已绑定，可用于登录和找回密码。');
      await refresh();
    } catch (err) { setEmailError(errorMessage(err)) }
    finally { setBusy(null) }
  };
  return <div className="security-page"><header className="page-heading"><span className="eyebrow">ACCOUNT SECURITY</span><h1>账户安全</h1><p className="muted">管理登录密码和用于找回账号的邮箱。</p></header>
    {loadError && <Notice>{loadError}<button className="text-button" onClick={() => void refresh()}>重新加载</button></Notice>}
    {success && <Notice tone="success">{success}</Notice>}
    <div className="security-grid"><section className="card" aria-labelledby="change-password-title"><h2 id="change-password-title">修改密码</h2><p className="muted small">修改后所有设备将退出登录，请重新登录后继续。</p>
      <form className="settings-form" aria-label="修改密码" onSubmit={event => void changePassword(event)}>
        <label>当前密码<input name="current_password" type="password" autoComplete="current-password" minLength={8} maxLength={128} required disabled={!!busy} value={currentPassword} onChange={event => setCurrentPassword(event.target.value)} /></label>
        <label>新密码<input name="new_password" type="password" autoComplete="new-password" minLength={8} maxLength={128} required disabled={!!busy} value={newPassword} onChange={event => setNewPassword(event.target.value)} placeholder="8–128 个字符" /></label>
        <label>确认新密码<input name="confirmation" type="password" autoComplete="new-password" minLength={8} maxLength={128} required disabled={!!busy} value={confirmation} onChange={event => setConfirmation(event.target.value)} /></label>
        {passwordError && <Notice>{passwordError}</Notice>}<button className="btn primary" disabled={!!busy}>{busy === 'password' ? '正在修改…' : '修改密码'}</button>
      </form></section>
      <section className="card" aria-labelledby="security-email-title"><h2 id="security-email-title">找回密码邮箱</h2>
        {!data ? !loadError && <Loading text="正在读取账户信息…" /> : data.email ? <><p className="security-bound-email">{data.email}</p><span className="badge badge-green">已验证并绑定</span><p className="muted small">可使用此邮箱登录，或在登录页通过验证码重置密码。</p></> : <>
          <p className="muted small">你的账号尚未绑定邮箱。验证后可使用邮箱登录，也能在忘记密码时找回账号。</p>
          {!emailEnabled && <Notice tone="info">{capabilitiesError ? <>邮箱服务状态读取失败。<button type="button" className="text-button" onClick={() => void refreshCapabilities()}>重新加载</button></> : capabilities ? '邮箱服务暂未启用，请联系管理员。' : '正在检查邮箱服务…'}</Notice>}
          <form className="settings-form" aria-label="绑定邮箱" onSubmit={event => void bindEmail(event)}>
            <label>验证当前密码<input name="binding_password" type="password" autoComplete="current-password" minLength={8} maxLength={128} required disabled={!!busy || !emailEnabled} value={bindingPassword} onChange={event => { setBindingPassword(event.target.value); setCode('') }} /></label>
            <label>绑定邮箱<input name="email" type="email" autoComplete="email" maxLength={254} required disabled={!!busy || !emailEnabled} value={email} onChange={event => { setEmail(event.target.value); setCode(''); setEmailError('') }} /></label>
            <EmailCodeField key={email.trim().toLowerCase()} email={email.trim().toLowerCase()} code={code} onCodeChange={setCode} enabled={emailEnabled && !busy} purpose="bind" currentPassword={bindingPassword} />
            {emailError && <Notice>{emailError}</Notice>}<button className="btn primary" disabled={!!busy || !emailEnabled}>{busy === 'email' ? '正在绑定…' : '验证并绑定邮箱'}</button>
          </form></>}
      </section></div></div>;
}
