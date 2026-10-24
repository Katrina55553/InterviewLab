import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { api, errorMessage } from './api';
import type { OAuthAuthorization } from './modelAdapter';
import { Icon, Notice } from './ui';

export function SubscriptionAuthorization({ session, onConnected, onCancel }: {
  session: OAuthAuthorization;
  onConnected: () => void;
  onCancel: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const finished = useRef(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [ended, setEnded] = useState(false);

  useEffect(() => {
    const field = input.current;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;
    finished.current = false;
    const deadline = Date.now() + session.expires_in * 1000;
    const poll = async () => {
      try {
        const result = await api<{ status: string }>(`/settings/ai/oauth/${session.session_id}`, { signal: controller.signal });
        if (stopped || finished.current) return;
        if (result.status === 'completed') {
          finished.current = true;
          onConnected();
          return;
        }
        if (!['pending', 'exchanging'].includes(result.status) || Date.now() >= deadline) {
          setError('授权已结束或过期，请重新点击「连接」发起登录。');
          setEnded(true);
          return;
        }
      } catch (cause) {
        if (stopped) return;
        setError(errorMessage(cause));
      }
      if (!stopped) timer = setTimeout(() => void poll(), 1500);
    };
    void poll();
    return () => { stopped = true; controller.abort(); clearTimeout(timer); if (field) field.value = '' };
  }, [session, onConnected]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const callback = input.current?.value.trim();
    if (!callback) return;
    if (input.current) input.current.value = '';
    setBusy(true); setError('');
    try {
      await api(`/settings/ai/oauth/${session.session_id}/complete`, { method: 'POST', body: { callback_url: callback } });
      if (!finished.current) { finished.current = true; onConnected() }
    } catch (cause) { if (!finished.current) setError(errorMessage(cause)) }
    finally { setBusy(false) }
  };
  const cancel = async () => {
    setBusy(true);
    try { await api(`/settings/ai/oauth/${session.session_id}`, { method: 'DELETE' }); onCancel() }
    catch (cause) { setError(errorMessage(cause)) }
    finally { setBusy(false) }
  };

  return <section className="ai-oauth-authorization" aria-labelledby="ai-oauth-title">
    <div className="ai-oauth-heading"><span className="ai-model-icon"><Icon name="spark" /></span><div><h2 id="ai-oauth-title">连接你的 ChatGPT 订阅</h2><p>在 OpenAI 页面登录，并同意让知面使用你的 ChatGPT 计划。</p></div></div>
    {!ended && <a className="btn primary" href={session.authorization_url} target="_blank" rel="noopener noreferrer">Continue with ChatGPT<Icon name="arrow" size={15} /></a>}
    <p className="ai-oauth-note">授权完成后会自动更新。若浏览器未能返回，请将授权结束后的完整回调地址粘贴到下方。</p>
    <form onSubmit={event => void submit(event)}><label htmlFor="ai-oauth-callback">授权回调地址</label><div className="ai-oauth-input-row"><input ref={input} id="ai-oauth-callback" type="password" autoComplete="off" placeholder={session.redirect_uri} disabled={busy || ended} required /><button className="btn" disabled={busy || ended} type="submit">{busy ? '处理中…' : '完成授权'}</button></div></form>
    {error && <Notice>{error}</Notice>}
    <div className="ai-oauth-footer"><span><Icon name="clock" size={13} />授权链接 10 分钟内有效；登录不会自动切换当前模型。</span><button className="text-button" type="button" disabled={busy} onClick={() => void cancel()}>取消授权</button></div>
  </section>;
}
