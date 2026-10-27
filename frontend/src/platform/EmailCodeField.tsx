import { useEffect, useId, useRef, useState } from 'react';
import { api, errorMessage } from './api';
import { Notice } from './ui';

interface EmailCodeResponse { message: string; expires_in: number; resend_after: number }

export function EmailCodeField({ email, code, onCodeChange, enabled, purpose, currentPassword }: {
  email: string; code: string; onCodeChange: (code: string) => void; enabled: boolean;
  purpose: 'register' | 'reset' | 'bind'; currentPassword?: string;
}) {
  const id = useId();
  const [sending, setSending] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [deadline, setDeadline] = useState(0);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, []);
  useEffect(() => {
    if (!deadline) return;
    const timer = window.setInterval(() => {
      const remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      setSeconds(remaining);
      if (!remaining) window.clearInterval(timer);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [deadline]);
  const send = async () => {
    if (sending || seconds || !enabled) return;
    setSending(true); setError(''); setMessage(''); onCodeChange('');
    try {
      const result = await api<EmailCodeResponse>(purpose === 'bind' ? '/auth/email/bind/code' : '/auth/email/code', {
        method: 'POST', body: purpose === 'bind' ? { email, current_password: currentPassword } : { email, purpose },
      });
      if (mounted.current) { setMessage(result.message); setSeconds(result.resend_after); setDeadline(Date.now() + result.resend_after * 1000) }
    } catch (err) { if (mounted.current) setError(errorMessage(err)) }
    finally { if (mounted.current) setSending(false) }
  };
  return <div className="email-code-field">
    <label htmlFor={id}>邮箱验证码</label>
    <div className="email-code-row"><input id={id} name="code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" minLength={6} maxLength={6} required disabled={!enabled || sending} value={code} onChange={event => onCodeChange(event.target.value.replace(/\D/g, '').slice(0, 6))} placeholder="6 位验证码" />
      <button className="btn" type="button" onClick={() => void send()} disabled={!enabled || sending || seconds > 0 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || (purpose === 'bind' && !currentPassword)}>{sending ? '发送中…' : seconds ? `${seconds} 秒后重发` : '发送验证码'}</button></div>
    {message && <Notice tone="info">{message}</Notice>}{error && <Notice>{error}</Notice>}
  </div>;
}
