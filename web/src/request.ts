import type { SessionController } from './session.ts';

export class RequestError extends Error {
  readonly status: number;
  readonly retryAfter: number;
  constructor(message: string, status: number, retryAfter = 0) { super(message); this.status = status; this.retryAfter = retryAfter; }
}
type Fetcher = typeof fetch;

export async function publicRequest<T>(path: string, method = 'GET', body?: unknown, fetcher: Fetcher = fetch, extraHeaders:Record<string,string> = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetcher(`/api/${path}`, {
      method,
      credentials: 'same-origin',
      headers: method === 'GET' ? Object.keys(extraHeaders).length?extraHeaders:undefined : {'Content-Type': 'application/json',...extraHeaders},
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch { throw new Error('无法连接 DataBack 服务。请确认服务已启动、网络连接正常，然后重试。'); }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const header = response.headers.get('Retry-After');
    const delay = header && /^\d+$/.test(header) ? Number(header) : data?.retry_after;
    const retryAfter = response.status === 429 ? typeof delay === 'number' && Number.isFinite(delay) && delay > 0 ? Math.min(3600, Math.ceil(delay)) : 60 : 0;
    throw new RequestError(typeof data?.error === 'string' ? data.error : `操作失败（${response.status}），请重试。`, response.status, retryAfter);
  }
  return data as T;
}

export async function privateRequest<T>(session: SessionController, path: string, method = 'GET', body?: unknown, fetcher: Fetcher = fetch, extraHeaders:Record<string,string> = {}): Promise<T> {
  const token = session.privateToken();
  try {
    const data = await publicRequest<T>(path, method, body, fetcher,extraHeaders);
    if (!session.isPrivateCurrent(token)) throw new Error('会话已变更，请登录后重试。');
    return data;
  } catch (reason) {
    if (!session.isPrivateCurrent(token)) throw new Error('会话已变更，请登录后重试。');
    if (reason instanceof RequestError && reason.status === 401) session.expire(token);
    throw reason;
  }
}
