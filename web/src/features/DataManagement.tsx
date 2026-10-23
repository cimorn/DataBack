import { useRef, useState } from 'react';
import { Download, Upload } from 'lucide-react';
import { request } from '../api';
import { toDatetimeLocal } from '../selectors';
import type { LedgerState } from '../types';
import { createLatestImportReader } from '../importData';
import type { ExportData } from '../importData';
import { FormError, PageHeading } from '../components/ui';
import { RecoveryPanels } from '../components/RecoveryPanels';

export function DataManagement({ onRefresh, privacyOpen = false, onUnlockPrivacy }: { state: LedgerState; onRefresh: () => Promise<void>; privacyOpen?: boolean; onUnlockPrivacy?: () => void }) {
  const [readLatest] = useState<ReturnType<typeof createLatestImportReader>>(() => createLatestImportReader());
  const selectionId = useRef(0);
  const [preview, setPreview] = useState<ExportData | null>(null); const [filename, setFilename] = useState('');
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [message, setMessage] = useState('');
  async function exportFile() {
    setBusy(true); setError(''); setMessage('');
    try {
      const data = await request<ExportData>('export');
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = `databack-${toDatetimeLocal().slice(0, 10)}.json`; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000); setMessage('台账 JSON 已导出。');
    } catch (reason) { setError(reason instanceof Error ? reason.message : '导出失败，请重试。'); }
    finally { setBusy(false); }
  }
  async function readFile(file?: File) {
    if (!file) return;
    const currentId = ++selectionId.current;
    setError(''); setMessage(''); setPreview(null); setFilename(file.name);
    try {
      const data = await readLatest(file);
      if (currentId === selectionId.current && data) setPreview(data);
    } catch (reason) { if (currentId === selectionId.current) setError(reason instanceof Error ? reason.message : '读取文件失败。'); }
  }
  async function importFile() {
    if (!preview) return;
    setBusy(true); setError(''); setMessage('');
    try { const result = await request<{ added: Record<keyof LedgerState, number> }>('import', 'POST', preview); setPreview(null); setMessage(`导入完成：新增 ${result.added.assets} 份资料、${result.added.locations} 个位置、${result.added.records} 条备份记录。`); await onRefresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '导入失败，数据未写入。'); }
    finally { setBusy(false); }
  }
  return <><PageHeading title="数据管理" /><FormError message={error} />{message ? <div className="success-message" role="status">{message}</div> : null}
    <section className="data-section surface"><h2>导出台账</h2><button className="button primary" disabled={busy} onClick={() => void exportFile()}><Download size={18} />导出 JSON</button></section>
    <section className="data-section surface"><div><h2>导入台账</h2><label className={`button secondary file-picker ${busy ? 'disabled' : ''}`}><Upload size={18} />选择 JSON 文件<input type="file" accept=".json,application/json" disabled={busy} onChange={e => { void readFile(e.target.files?.[0]); e.target.value = ''; }} /></label></div></section>
    {preview ? <section className="import-preview surface"><h2>导入预览</h2><p className="path">{filename}</p><table><thead><tr><th>资料</th><th>位置</th><th>备份记录</th></tr></thead><tbody><tr><td>{preview.assets.length}</td><td>{preview.locations.length}</td><td>{preview.records.length}</td></tr></tbody></table><div className="form-actions"><button className="button secondary" disabled={busy} onClick={() => setPreview(null)}>取消导入</button><button className="button primary" disabled={busy} onClick={() => void importFile()}>{busy ? '正在导入…' : '确认合并导入'}</button></div></section> : null}
    <RecoveryPanels key={privacyOpen ? 'private' : 'public'} privacyOpen={privacyOpen} onUnlockPrivacy={onUnlockPrivacy} onRefresh={onRefresh}/>
  </>;
}
