import {useEffect, useRef, useState} from 'react';
import {Download, RotateCcw, Save} from 'lucide-react';
import {request} from '../api';
import {formatTime} from '../selectors';
import {createRefreshSafeAction} from '../recoveryActions';
import {FormError, Modal} from './ui';

interface TrashEntry {id: string; collection: string; name: string; deleted_at: string; expires_at: string}
interface Snapshot {id: string; created_at: string; reason: string; counts: {assets: number; records: number}}
interface SnapshotList {items: Snapshot[]; enabled: boolean; error?: string}
const reasonLabels: Record<string, string> = {automatic: '自动', daily: '自动', startup: '自动', manual: '手动', pre_restore: '恢复前', before_restore: '恢复前'};
type RecoveryAction = ReturnType<typeof createRefreshSafeAction<unknown>>;
type PendingAction = {label: string; action: RecoveryAction; refresh: () => Promise<void>};

function downloadJson(data: unknown, filename: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'}));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function RecoveryPanels({privacyOpen = false, onUnlockPrivacy, onRefresh}: {
  privacyOpen?: boolean; onUnlockPrivacy?: () => void; onRefresh: () => Promise<void>;
}) {
  const [trashOpen, setTrashOpen] = useState(false);
  const [snapshotsOpen, setSnapshotsOpen] = useState(false);
  const [trash, setTrash] = useState<TrashEntry[]>([]);
  const [snapshots, setSnapshots] = useState<SnapshotList>({items: [], enabled: true});
  const [trashLoaded, setTrashLoaded] = useState(false);
  const [snapshotsLoaded, setSnapshotsLoaded] = useState(false);
  const [loadingTrash, setLoadingTrash] = useState(false);
  const [loadingSnapshots, setLoadingSnapshots] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [restore, setRestore] = useState<Snapshot | null>(null);
  const [retry, setRetry] = useState<PendingAction | null>(null);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {mounted.current = true; return () => {mounted.current = false;};}, []);
  async function loadTrash() {
    setLoadingTrash(true);
    try {
      const result = await request<{items: TrashEntry[]}>('trash');
      if (mounted.current) {setTrash(result.items); setTrashLoaded(true);}
    } finally {if (mounted.current) setLoadingTrash(false);}
  }
  async function loadSnapshots() {
    if (!privacyOpen) return;
    setLoadingSnapshots(true);
    try {
      const result = await request<SnapshotList>('snapshots');
      if (mounted.current) {setSnapshots(result);setSnapshotsLoaded(true);}
    } finally {if (mounted.current) setLoadingSnapshots(false);}
  }
  useEffect(() => {
    if (trashOpen && !trashLoaded) void loadTrash().catch(reason => {if(mounted.current)setError(reason instanceof Error ? reason.message : '读取回收站失败。');});
  }, [trashOpen, trashLoaded]);
  useEffect(() => {
    if (snapshotsOpen && privacyOpen && !snapshotsLoaded) void loadSnapshots().catch(reason => {if(mounted.current)setError(reason instanceof Error ? reason.message : '读取备份失败。');});
  }, [snapshotsOpen, privacyOpen, snapshotsLoaded]);
  async function perform(job: PendingAction) {
    if (inFlight.current) return;
    inFlight.current = true;setBusy(true);setError('');setMessage('');
    try {
      await job.action.run(job.refresh);
      if (mounted.current) {setRetry(null);setRestore(null);setMessage(job.label);}
    } catch (reason) {
      if (mounted.current) {
        setRetry(job.action.committed() ? job : null);
        setError(job.action.committed() ? '操作已完成，读取结果失败，请重试读取。' : reason instanceof Error ? reason.message : '操作失败，请重试。');
      }
    } finally {inFlight.current = false;if(mounted.current)setBusy(false);}
  }
  const unavailable = busy || Boolean(retry);
  const refreshAll = async () => {await loadSnapshots();await loadTrash();await onRefresh();};
  async function download(snapshot: Snapshot) {
    if (inFlight.current) return;
    inFlight.current = true;setBusy(true);setError('');setMessage('');
    try {const data = await request<unknown>(`snapshots/${encodeURIComponent(snapshot.id)}`); if(mounted.current) downloadJson(data, `databack-${snapshot.created_at.replace(/[^0-9T]/g, '-').slice(0,19)}.json`);}
    catch (reason) {if(mounted.current)setError(reason instanceof Error ? reason.message : '下载失败，请重试。');}
    finally {inFlight.current=false;if(mounted.current)setBusy(false);}
  }
  return <div className="recovery-panels"><FormError message={error}/>{message ? <div className="success-message" role="status">{message}</div> : null}
    {retry && !restore ? <button className="button secondary recovery-retry" disabled={busy} onClick={() => void perform(retry)}>重试读取</button> : null}
    <section className="data-section surface recovery-section">
      <div className="recovery-heading"><button className="text-button section-toggle" aria-expanded={trashOpen} onClick={() => setTrashOpen(current => !current)}><h2>回收站</h2></button><small className="muted">保留 30 天</small></div>
      {trashOpen ? <div className="recovery-content">
        {loadingTrash ? <p className="muted">正在读取…</p> : !trashLoaded ? <button className="text-button" onClick={() => void loadTrash().catch(reason => setError(reason instanceof Error ? reason.message : '读取失败。'))}>重试读取</button> : !trash.length ? <p className="muted">回收站为空</p> : <div className="table-scroll"><table className="recovery-table"><thead><tr><th>名称</th><th>删除时间</th><th>保留至</th><th>操作</th></tr></thead><tbody>{trash.map(item => <tr key={item.id}><td>{item.name}</td><td>{formatTime(item.deleted_at)}</td><td>{formatTime(item.expires_at)}</td><td><button className="text-button" disabled={unavailable} aria-label={`恢复 ${item.name}`} onClick={() => void perform({label: '已恢复', action: createRefreshSafeAction(() => request(`trash/${encodeURIComponent(item.id)}/restore`, 'POST', {})), refresh: async () => {await loadTrash();await onRefresh();}})}><RotateCcw size={15}/>恢复</button></td></tr>)}</tbody></table></div>}
      </div> : null}
    </section>
    <section className="data-section surface recovery-section">
      <div className="recovery-heading"><button className="text-button section-toggle" aria-expanded={snapshotsOpen} onClick={() => setSnapshotsOpen(current => !current)}><h2>自动备份</h2></button><small className="muted">每天一份 · 保留 30 份</small></div>
      {snapshotsOpen && !privacyOpen ? <button className="button secondary" disabled={!onUnlockPrivacy} onClick={onUnlockPrivacy}>输入隐私密码</button> : snapshotsOpen && privacyOpen ? <div className="recovery-content">
        {snapshots.error ? <FormError message={snapshots.error}/> : null}<div className="recovery-toolbar"><button className="button secondary compact" disabled={unavailable || loadingSnapshots || !snapshotsLoaded} onClick={() => void perform({label: '备份已保存', action: createRefreshSafeAction(() => request('snapshots', 'POST', {})), refresh: loadSnapshots})}><Save size={16}/>立即备份</button></div>
        {loadingSnapshots ? <p className="muted">正在读取…</p> : !snapshotsLoaded ? <button className="text-button" onClick={() => void loadSnapshots().catch(reason => setError(reason instanceof Error ? reason.message : '读取失败。'))}>重试读取</button> : !snapshots.items.length ? <p className="muted">暂无备份</p> : <div className="table-scroll"><table className="recovery-table"><thead><tr><th>备份时间</th><th>来源</th><th>资料</th><th>记录</th><th>操作</th></tr></thead><tbody>{snapshots.items.map(item => <tr key={item.id}><td>{formatTime(item.created_at)}</td><td>{reasonLabels[item.reason] ?? '备份'}</td><td>{item.counts.assets}</td><td>{item.counts.records}</td><td><div className="row-actions"><button className="icon-button" disabled={unavailable} aria-label={`下载备份 ${formatTime(item.created_at)}`} onClick={() => void download(item)}><Download size={16}/></button><button className="text-button" disabled={unavailable} aria-label={`恢复台账 ${formatTime(item.created_at)}`} onClick={() => {setRestore(item);setError('');}}><RotateCcw size={15}/>恢复</button></div></td></tr>)}</tbody></table></div>}
      </div> : null}
    </section>
    {restore && privacyOpen ? <Modal title="恢复台账" onClose={() => {setRestore(null);setError('');}} busy={busy}><div className="form-body">
      <FormError message={error}/><p>{formatTime(restore.created_at)} · {restore.counts.assets} 份资料 · {restore.counts.records} 条记录</p><p>将替换当前台账，恢复前会自动保存一份备份。</p>
      <div className="form-actions"><button className="button secondary" disabled={busy || Boolean(retry)} onClick={() => setRestore(null)}>取消</button><button className="button danger" disabled={busy} onClick={() => void perform(retry ?? {label: '台账已恢复', action: createRefreshSafeAction(() => request(`snapshots/${encodeURIComponent(restore.id)}/restore`, 'POST', {})), refresh: refreshAll})}>{busy ? '正在恢复…' : retry ? '重试读取' : '确认替换并恢复'}</button></div>
    </div></Modal> : null}
  </div>;
}
