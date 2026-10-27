import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { LogIn, RefreshCw } from 'lucide-react';
import { BrandIcon } from './BrandIcon';
import type { SessionSnapshot } from '../session';
import { Field, FormError } from './ui';
import { MAX_PASSWORD_CHARS } from '../inputLimits';

export function SessionGate({snapshot, onLogin, onRetry, onLogout}: {
  snapshot: SessionSnapshot; onLogin: (password: string) => Promise<boolean>; onRetry: () => Promise<void>; onLogout: () => Promise<void>;
}) {
  const [password, setPassword] = useState('');
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!snapshot.loginRetryAt || snapshot.loginRetryAt <= Date.now()) return;
    setNow(Date.now());
    const timer = setInterval(() => { const current = Date.now(); setNow(current); if (current >= snapshot.loginRetryAt!) clearInterval(timer); }, 1000);
    return () => clearInterval(timer);
  }, [snapshot.loginRetryAt]);
  const retrySeconds = Math.max(0, Math.ceil(((snapshot.loginRetryAt ?? 0) - now) / 1000));
  const checking = snapshot.status === 'checking';
  const busy = checking || snapshot.status === 'logging-in' || snapshot.status === 'logging-out';
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || retrySeconds) return;
    if (await onLogin(password)) setPassword('');
  }
  return <main className="session-page"><section className="session-card surface" aria-labelledby="session-title">
    <div className="session-brand"><BrandIcon /><strong>DataBack</strong></div>
    <h1 id="session-title">{checking ? '正在确认会话…' : snapshot.status === 'logging-out' ? '正在退出登录…' : snapshot.status === 'error' ? '连接暂时不可用' : '登录'}</h1>
    <FormError message={snapshot.error} />
    {snapshot.logoutUnconfirmed ? <button className="button secondary" type="button" disabled={busy} onClick={() => void onLogout()}><RefreshCw size={18} />重试退出</button> : snapshot.status === 'error' ? <button className="button secondary" type="button" onClick={() => void onRetry()}><RefreshCw size={18} />重试连接</button> : !checking && snapshot.status !== 'logging-out' ? <form onSubmit={submit} className="session-form">
      <Field label="登录密码"><input autoFocus type="password" autoComplete="current-password" maxLength={MAX_PASSWORD_CHARS} required value={password} disabled={busy} onChange={event => setPassword(event.target.value)} /></Field>
      <button className="button primary" type="submit" disabled={busy || retrySeconds > 0}><LogIn size={18} />{busy ? '正在登录…' : retrySeconds ? `请稍后重试（${retrySeconds} 秒）` : '登录'}</button>
    </form> : null}
  </section></main>;
}
