import { useId, useMemo, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { request } from '../api';
import { buildHierarchy } from '../hierarchy';
import { createLocationResolver } from '../recordLocations';
import { reuseLatestRecordDraft } from '../recordEditing';
import { MAX_NOTES_CHARS, notesForSave } from '../inputLimits';
import { filterRecords, formatTime, toBackupTimestamp, toDatetimeLocal } from '../selectors';
import type { Asset, BackupRecord, LedgerState, Version } from '../types';
import { Field, FormError, Modal } from './ui';

function recordDraft(record: BackupRecord | undefined, state: LedgerState) {
  return {time: toDatetimeLocal(record?.backed_up_at), location: state.locations.find(location => location.id === record?.location_id)?.name ?? '', notes: record?.notes ?? ''};
}

export function BackupInfoDialog({asset, state, initialMode = 'edit', onClose, onSaved, onDeleted}: {
  asset: Asset; state: LedgerState; initialMode?: 'edit' | 'new'; onClose: () => void; onSaved: () => Promise<void>; onDeleted?: (trashId?: string) => Promise<void>;
}) {
  const records = useMemo(() => filterRecords(state, {assetId: asset.id}), [state, asset.id]);
  const [selectedId, setSelectedId] = useState(initialMode === 'new' ? '' : records[0]?.id ?? '');
  const [draft, setDraft] = useState(() => recordDraft(initialMode === 'new' ? undefined : records[0], state));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef(false);
  const committed = useRef<'save' | 'delete' | null>(null);
  const deletedTrashId = useRef<string | undefined>(undefined);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const locationListId = useId();
  const canCreate = buildHierarchy(state.assets).isLeaf(asset.id);
  const canEdit = Boolean(selectedId) || canCreate;
  const markingOnSave = !selectedId && asset.backup_target !== true;
  const update = (key: keyof typeof draft, value: string) => setDraft(current => ({...current, [key]: value}));
  const choose = (id: string) => {
    setSelectedId(id); setDraft(recordDraft(records.find(record => record.id === id), state)); setError(''); setConfirmingDelete(false);
  };
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending.current || !canEdit) return;
    pending.current = true; setBusy(true); setError('');
    try {
      if (!committed.current) {
        if (!draft.location.trim()) throw new Error('请填写备份位置。');
        if (draft.location.trim().length > 200) throw new Error('填写内容过长。');
        const original = selectedId ? records.find(record => record.id === selectedId) : undefined;
        if (selectedId && !original) throw new Error('这条记录已移除，请重新打开备份信息。');
        const notes = notesForSave(draft.notes, original?.notes);
        const backed_up_at = toBackupTimestamp(draft.time, original?.backed_up_at);
        const fresh = await request<LedgerState>('state');
        const freshAsset = fresh.assets.find(item => item.id === asset.id);
        if (!freshAsset) throw new Error('资料已移除。');
        let versionId = original?.version_id;
        if (!original) {
          if (!buildHierarchy(fresh.assets).isLeaf(asset.id)) throw new Error('只有末级文件或文件夹可以新增备份。');
          if (!freshAsset.backup_target) {
            if (!markingOnSave) throw new Error('资料已取消备份，请重新打开备份信息。');
          }
          let version = fresh.versions.find(item => item.asset_id === asset.id && item.label === '未标注');
          if (!version) {
            try { version = await request<Version>('versions', 'POST', {asset_id: asset.id, label: '未标注', notes: ''}); }
            catch (reason) {
              version = (await request<LedgerState>('state')).versions.find(item => item.asset_id === asset.id && item.label === '未标注');
              if (!version) throw reason;
            }
          }
          versionId = version.id;
        }
        const location = await createLocationResolver(fresh.locations, request)(draft.location);
        const payload = {version_id: versionId, location_id: location.id, backed_up_at, notes};
        if (original) await request(`records/${original.id}`, 'PUT', payload);
        else await request('records/batch', 'POST', {records: [payload], ...(markingOnSave ? {mark_asset_id: asset.id} : {})});
        committed.current = 'save';
      }
      await onSaved(); onClose();
    } catch (reason) { setError(reason instanceof Error ? reason.message : '保存失败，请重试。'); }
    finally { pending.current = false; setBusy(false); }
  }
  async function remove(event: FormEvent) {
    event.preventDefault();
    if (pending.current || !selectedId || !confirmingDelete) return;
    pending.current = true; setBusy(true); setError('');
    try {
      if (!committed.current) {
        const result = await request<{trash_id?: string}>(`records/${selectedId}`, 'DELETE');
        deletedTrashId.current = result.trash_id;
        committed.current = 'delete';
      }
      await (onDeleted ? onDeleted(deletedTrashId.current) : onSaved()); onClose();
    } catch (reason) { setError(reason instanceof Error ? reason.message : '删除失败，请重试。'); }
    finally { pending.current = false; setBusy(false); }
  }
  const selected = records.find(record => record.id === selectedId);
  const title = confirmingDelete ? '删除备份记录' : `${selectedId ? '编辑备份信息' : '新增备份记录'} · ${asset.name}`;
  return <Modal title={title} busy={busy} onClose={onClose}><form className="form-body backup-info-form" onSubmit={confirmingDelete ? remove : submit}>
    <FormError message={error}/>
    {confirmingDelete ? <>
      <p className="delete-description">删除这条备份记录？</p>
      <p className="muted">{formatTime(selected?.backed_up_at)} · {state.locations.find(location => location.id === selected?.location_id)?.name ?? '—'}</p>
      <div className="form-actions"><button type="button" className="button secondary" disabled={busy || committed.current === 'delete'} onClick={() => { setConfirmingDelete(false); setError(''); }}>取消删除</button><button autoFocus type="submit" className="button danger" disabled={busy}>{busy ? '正在删除…' : committed.current === 'delete' ? '重试读取' : '确认删除'}</button></div>
    </> : <>
    {canEdit ? <fieldset className="backup-info-fields" disabled={busy || Boolean(committed.current)}>
      {records.length ? <Field label="备份记录"><select value={selectedId} onChange={event => choose(event.target.value)}>
        {canCreate ? <option value="">新增备份记录</option> : null}
        {records.map(record => <option key={record.id} value={record.id}>{formatTime(record.backed_up_at)} · {state.locations.find(location => location.id === record.location_id)?.name ?? '—'}</option>)}
      </select></Field> : null}
      {records.length ? <div className="record-editor-actions">
        {canCreate ? <button type="button" className="button secondary compact" disabled={!selectedId} onClick={() => choose('')}><Plus size={16}/>新增记录</button> : null}
        {selectedId ? <button type="button" className="button secondary compact" onClick={() => { setConfirmingDelete(true); setError(''); }}><Trash2 size={16}/>删除记录</button> : null}
      </div> : null}
      {!selectedId && records.length ? <button type="button" className="text-button reuse-last" onClick={() => {
        const value = reuseLatestRecordDraft(state, asset.id); if (value) setDraft(value);
      }}>沿用上次</button> : null}
      <Field label="备份时间"><input autoFocus type="datetime-local" step={60} required value={draft.time} onChange={event => update('time', event.target.value)}/></Field>
      <Field label="备份位置"><input required maxLength={200} list={locationListId} value={draft.location} onChange={event => update('location', event.target.value)} placeholder="例如：移动硬盘、NAS、网盘"/></Field>
      <datalist id={locationListId}>{state.locations.map(location => <option key={location.id} value={location.name}/>)}</datalist>
      <Field label="备注" hint={selected && selected.notes.length > MAX_NOTES_CHARS ? '原备注可保留，修改后限 2000 字。' : `${draft.notes.length}/${MAX_NOTES_CHARS}`}><textarea rows={3} maxLength={Math.max(MAX_NOTES_CHARS, selected?.notes.length ?? 0)} value={draft.notes} onChange={event => update('notes', event.target.value)}/></Field>
    </fieldset> : <p className="muted">暂无备份记录。</p>}
    <div className="form-actions"><button type="button" className="button secondary" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="button primary" disabled={busy || !canEdit}>{busy ? '正在保存…' : committed.current === 'save' ? '重试读取' : markingOnSave ? '保存并设为备份对象' : '保存'}</button></div>
    </>}
  </form></Modal>;
}
