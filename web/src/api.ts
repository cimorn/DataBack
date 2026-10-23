import { useEffect, useState } from 'react';
import type { Collection, LedgerState } from './types';
import { createLatestLoader } from './latestLoad';
import { privateRequest } from './request';
import { sessionController } from './sessionClient';
import { visibilityFence } from './visibilityFence';

export function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const proof=visibilityFence.privacyToken();
  return visibilityFence.guard(() => privateRequest<T>(sessionController, path, method, body,fetch,proof?{'X-Databack-Privacy':proof}:{}));
}

export function useLedger() {
  const [state, setState] = useState<LedgerState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  useEffect(() => sessionController.subscribe(() => {
    if (sessionController.getSnapshot().status !== 'authenticated') { setState(null); setError(''); }
  }), []);
  // Only the newest refresh may commit any part of the load lifecycle.
  const [refresh] = useState(() => createLatestLoader(() => request<LedgerState>('state'), {
    start: () => { setLoading(true); setError(''); },
    success: setState,
    failure: reason => setError(reason instanceof Error ? reason.message : '读取台账失败，请重试。'),
    settled: () => setLoading(false),
  }));
  useEffect(() => { void refresh(); }, [refresh]);
  const save = async (collection: Collection, data: unknown, id?: string) => {
    await request(id ? `${collection}/${id}` : collection, id ? 'PUT' : 'POST', data);
    await refresh();
  };
  const remove = async (collection: Collection, id: string) => {
    const receipt = await request<{deleted: boolean; trash_id?: string}>(`${collection}/${id}`, 'DELETE');
    await refresh();
    return receipt;
  };
  const reorder = async (parentId: string | null, orderedIds: string[]) => {
    await request('assets/reorder', 'POST', {parent_id: parentId, ordered_ids: orderedIds});
    await refresh();
  };
  return { state, loading, error, refresh, save, remove, reorder };
}
