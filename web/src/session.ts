import type { SessionInfo } from './types.ts';

export interface SessionSnapshot {
  status: 'checking' | 'authenticated' | 'anonymous' | 'logging-in' | 'logging-out' | 'error';
  session: SessionInfo | null;
  error: string;
  logoutUnconfirmed: boolean;
  generation: number;
  loginRetryAt?: number;
}
type SessionRequest = (path: 'session' | 'login' | 'logout', body?: {password: string} | Record<string, never>) => Promise<unknown>;

function sessionInfo(value: unknown): SessionInfo {
  if (!value || typeof value !== 'object') throw new Error('会话响应无效，请重试。');
  const session = value as Partial<SessionInfo>;
  if (typeof session.authenticated !== 'boolean' || typeof session.auth_required !== 'boolean' || !['sqlite', 'mongodb'].includes(session.storage ?? '')) throw new Error('会话响应无效，请重试。');
  return {authenticated: session.authenticated, auth_required: session.auth_required, storage: session.storage!};
}
const errorMessage = (reason: unknown) => reason instanceof Error ? reason.message : '无法确认会话，请重试。';

export function createSessionController(send: SessionRequest, clock: () => number = Date.now) {
  let generation = 0;
  let snapshot: SessionSnapshot = {status: 'checking', session: null, error: '', logoutUnconfirmed: false, generation};
  const listeners = new Set<() => void>();
  function publish(patch: Partial<SessionSnapshot>) {
    snapshot = {...snapshot, ...patch, generation};
    listeners.forEach(listener => listener());
  }
  function start(status: SessionSnapshot['status']) {
    const operation = ++generation;
    publish({status, error: ''});
    return operation;
  }
  function accept(value: unknown) {
    const session = sessionInfo(value);
    publish({session, status: session.authenticated ? 'authenticated' : 'anonymous', error: '', logoutUnconfirmed: false, loginRetryAt: undefined});
  }
  const controller = {
    getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    async bootstrap() {
      const operation = start('checking');
      try { const value = await send('session'); if (operation === generation) accept(value); }
      catch (reason) { if (operation === generation) publish({status: 'error', error: errorMessage(reason)}); }
    },
    async login(password: string): Promise<boolean> {
      // An unsuccessful logout must be retried before allowing this page to reopen data.
      if (snapshot.logoutUnconfirmed || (snapshot.loginRetryAt ?? 0) > clock()) return false;
      publish({loginRetryAt: undefined});
      const operation = start('logging-in');
      try {
        const value = await send('login', {password});
        if (operation !== generation) return false;
        const session = sessionInfo(value);
        if (!session.authenticated) throw new Error('登录未完成，请重试。');
        accept(session);
        return true;
      } catch (reason) {
        if (operation === generation) {
          const limited = reason as {status?: unknown; retryAfter?: unknown} | null;
          const seconds = limited?.status === 429 && typeof limited.retryAfter === 'number' && Number.isFinite(limited.retryAfter) ? Math.max(1, Math.min(3600, limited.retryAfter)) : 0;
          publish({status: 'anonymous', error: errorMessage(reason), loginRetryAt: seconds ? clock() + seconds * 1000 : undefined});
        }
        return false;
      }
    },
    async logout() {
      const operation = start('logging-out');
      try {
        const value = await send('logout', {});
        if (operation !== generation) return;
        const session = sessionInfo(value);
        if (session.authenticated) throw new Error('服务尚未确认退出。');
        accept(session);
      } catch (reason) {
        if (operation === generation) publish({status: 'anonymous', session: snapshot.session ? {...snapshot.session, authenticated: false} : null, logoutUnconfirmed: true, error: `退出请求未完成，服务器会话尚未确认撤销。请重试退出。${errorMessage(reason)}`});
      }
    },
    privateToken() {
      if (snapshot.status !== 'authenticated') throw new Error('请登录后继续，会话尚未确认。');
      return generation;
    },
    isPrivateCurrent: (token: number) => token === generation && snapshot.status === 'authenticated',
    expire(token: number) {
      if (!controller.isPrivateCurrent(token)) return;
      generation++;
      publish({status: 'anonymous', session: snapshot.session ? {...snapshot.session, authenticated: false} : null, error: '登录会话已过期，请重新登录。'});
    },
  };
  return controller;
}
export type SessionController = ReturnType<typeof createSessionController>;
