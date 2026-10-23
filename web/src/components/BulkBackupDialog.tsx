import { useId, useMemo, useState } from 'react';
import type { FormEvent } from 'react';
import { request } from '../api';
import { collectBulkTargets, createBulkSubmission, parseBackupTable, previewBulkRecords } from '../bulkRecords';
import type { BulkInputRow, BulkPreview, BulkSharedFields } from '../bulkRecords';
import { buildHierarchy } from '../hierarchy';
import { MAX_NOTES_CHARS, MAX_RECORDS_PER_BATCH } from '../inputLimits';
import { backupTargets } from '../reminders';
import { toDatetimeLocal } from '../selectors';
import type { LedgerState } from '../types';
import { Field, FormError, Modal } from './ui';

export function BulkBackupDialog({state, initialAssetIds = [], initialMode = 'selection', onClose, onSaved}: {
  state: LedgerState; initialAssetIds?: string[]; initialMode?: 'selection' | 'paste';
  onClose: () => void; onSaved: () => Promise<void>;
}) {
  const tree = useMemo(() => buildHierarchy(state.assets), [state.assets]);
  const eligible = useMemo(() => backupTargets(state, tree).sort((a, b) => tree.path(a.id).localeCompare(tree.path(b.id), 'zh-CN')), [state, tree]);
  const [mode, setMode] = useState(initialMode);
  const [selectedIds, setSelectedIds] = useState(() => collectBulkTargets(state, initialAssetIds).map(asset => asset.id));
  const [shared, setShared] = useState<BulkSharedFields>(() => ({time: toDatetimeLocal(), location: '', notes: ''}));
  const [search, setSearch] = useState('');
  const [pasted, setPasted] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [attempted, setAttempted] = useState(false);
  const [submission] = useState(() => createBulkSubmission(request));
  const locationListId = useId();
  const selected = new Set(selectedIds);
  const filtered = eligible.filter(asset => tree.path(asset.id).toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const preview = useMemo<BulkPreview>(() => {
    try {
      const rows: BulkInputRow[] = mode === 'paste' ? parseBackupTable(pasted) : selectedIds.map(assetId => ({assetId, path: tree.path(assetId), ...shared}));
      return previewBulkRecords(state, rows);
    } catch (reason) {
      return {rows: [], errors: [reason instanceof Error ? reason.message : '表格格式有误。']};
    }
  }, [mode, pasted, selectedIds, shared, state, tree]);
  const frozen = busy || submission.committed;
  const showErrors = attempted || (mode === 'paste' && Boolean(pasted.trim())) || preview.rows.length > MAX_RECORDS_PER_BATCH;
  const close = () => { if (!submission.pending) onClose(); };
  const changeShared = (key: keyof BulkSharedFields, value: string) => {
    setShared(current => ({...current, [key]: value})); setError('');
  };
  const toggle = (id: string) => {
    setSelectedIds(current => current.includes(id) ? current.filter(value => value !== id) : [...current, id]); setError('');
  };

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submission.pending) return;
    setAttempted(true); setError('');
    if (!submission.committed && preview.errors.length) {
      setError('请先修正预览中的问题。');
      return;
    }
    setBusy(true);
    try {
      const rows = preview.rows.map(row => ({assetId: row.assetId ?? undefined, path: row.path, time: row.time, location: row.location, notes: row.notes, rowNumber: row.rowNumber}));
      if (await submission.submit(rows, onSaved)) onClose();
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '请重试。';
      setError(submission.committed ? `记录已登记，读取失败。${message}` : message);
    } finally { setBusy(false); }
  }

  return <Modal title="批量登记备份" onClose={close} busy={busy} wide>
    <form className="form-body bulk-backup-form" onSubmit={submit}>
      <FormError message={error}/>
      <fieldset className="bulk-backup-fields" disabled={frozen}>
        <div className="bulk-mode-switch" role="group" aria-label="登记方式">
          <button type="button" aria-pressed={mode === 'selection'} onClick={() => {setMode('selection'); setError(''); setAttempted(false);}}>选择资料</button>
          <button type="button" aria-pressed={mode === 'paste'} onClick={() => {setMode('paste'); setError(''); setAttempted(false);}}>粘贴表格</button>
        </div>
        {mode === 'selection' ? <>
          <div className="bulk-picker-heading"><strong>备份对象</strong><span className={selectedIds.length > MAX_RECORDS_PER_BATCH ? 'danger-text' : 'muted'}>已选 {selectedIds.length} 项 · 单次最多 {MAX_RECORDS_PER_BATCH} 项</span></div>
          {eligible.length ? <>
            <Field label="筛选资料"><input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索相对路径"/></Field>
            <div className="bulk-picker-actions"><button type="button" className="text-button" onClick={() => {setSelectedIds(current => [...new Set([...current, ...filtered.map(asset => asset.id)])]); setError('');}}>全选当前列表</button><button type="button" className="text-button" onClick={() => {setSelectedIds([]); setError('');}}>清空选择</button></div>
            <div className="bulk-asset-list" role="group" aria-label="可登记的备份对象">
              {filtered.length ? filtered.map(asset => <label key={asset.id} className="bulk-asset-option"><input type="checkbox" checked={selected.has(asset.id)} onChange={() => toggle(asset.id)}/><span>{tree.path(asset.id)}</span></label>) : <p className="muted bulk-empty">没有匹配的资料。</p>}
            </div>
          </> : <p className="muted bulk-empty">暂无可登记的备份对象。</p>}
          <div className="bulk-shared-fields">
            <Field label="备份时间"><input type="datetime-local" step={1} value={shared.time} onChange={event => changeShared('time', event.target.value)}/></Field>
            <Field label="备份位置"><input maxLength={200} list={locationListId} value={shared.location} onChange={event => changeShared('location', event.target.value)} placeholder="例如：移动硬盘、NAS、网盘"/></Field>
            <Field label="备注" hint={`${shared.notes.length}/${MAX_NOTES_CHARS}`}><textarea rows={2} maxLength={MAX_NOTES_CHARS} value={shared.notes} onChange={event => changeShared('notes', event.target.value)}/></Field>
          </div>
          <datalist id={locationListId}>{state.locations.map(location => <option key={location.id} value={location.name}/>)}</datalist>
        </> : <Field label="表格内容" hint={`路径 → 备份时间 → 备份位置 → 备注；支持 Excel / CSV，可带表头，最多 ${MAX_RECORDS_PER_BATCH} 条。`}>
          <textarea className="bulk-paste-input" rows={6} spellCheck={false} value={pasted} onChange={event => {setPasted(event.target.value); setError('');}} placeholder={'路径\t备份时间\t备份位置\t备注\n资料/照片\t2026-10-22 09:30\t移动硬盘\t已核对'}/>
        </Field>}
      </fieldset>
      <section className="bulk-preview" aria-label="登记预览">
        <div className="bulk-picker-heading"><strong>预览</strong><span className="muted">待登记 {preview.rows.length} 条</span></div>
        {showErrors && !submission.committed && preview.errors.length ? <div className="bulk-validation" role="alert">{preview.errors.map((message, index) => <p key={index}>{message}</p>)}</div> : null}
        {preview.rows.length ? <div className="table-scroll"><table className="bulk-preview-table"><thead><tr><th>资料路径</th><th>备份时间</th><th>备份位置</th><th>备注</th></tr></thead><tbody>{preview.rows.map((row, index) => <tr key={index} className={showErrors && row.error ? 'bulk-row-invalid' : undefined}>
          <td data-label="资料路径"><span className="path">{row.path || '—'}</span></td>
          <td data-label="备份时间">{row.time.replace('T', ' ') || '—'}</td>
          <td data-label="备份位置">{row.location || '—'}</td>
          <td data-label="备注"><span className="bulk-preview-notes">{row.notes || '—'}</span></td>
        </tr>)}</tbody></table></div> : <p className="muted bulk-empty">{mode === 'paste' ? '粘贴后查看预览。' : '选择资料后查看预览。'}</p>}
      </section>
      <div className="form-actions"><button type="button" className="button secondary" disabled={busy} onClick={close}>取消</button><button type="submit" className="button primary" disabled={busy || (!submission.committed && !preview.rows.length)}>{busy ? submission.committed ? '正在读取…' : '正在登记…' : submission.committed ? '重试读取' : '确认登记'}</button></div>
    </form>
  </Modal>;
}
