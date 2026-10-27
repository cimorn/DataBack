import { useId, useMemo, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { request } from '../api';
import { toBackupTimestamp, toDatetimeLocal } from '../selectors';
import { buildHierarchy } from '../hierarchy';
import { backupTargets } from '../reminders';
import { createLocationResolver } from '../recordLocations';
import { reuseLatestRecordDraft } from '../recordEditing';
import { MAX_NOTES_CHARS, MAX_RECORDS_PER_BATCH, notesForSave } from '../inputLimits';
import type { BackupRecord, LedgerState, Version } from '../types';
import { Field, FormActions, FormError, Modal } from './ui';

interface RecordRow { id: number; location: string; time: string; notes: string }
const newRow = (id: number): RecordRow => ({id, location: '', time: toDatetimeLocal(), notes: ''});

export function BackupDialog({state, initialAssetId, onClose, onSaved, onChooseTargets}: {
  state: LedgerState; initialAssetId?: string;
  onClose: () => void; onSaved: () => Promise<void>; onChooseTargets: () => void;
}) {
  const tree = useMemo(() => buildHierarchy(state.assets), [state.assets]);
  const eligible = backupTargets(state, tree);
  const [assetId, setAssetId] = useState(eligible.find(asset => asset.id === initialAssetId)?.id ?? eligible[0]?.id ?? '');
  const [rows, setRows] = useState<RecordRow[]>(() => [newRow(1)]);
  const nextId = useRef(2);
  const submitted = useRef(false);
  const saving = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const locationListId = useId();
  const update = (id: number, patch: Partial<RecordRow>) => setRows(current => current.map(row => row.id === id ? {...row, ...patch} : row));
  const previous = reuseLatestRecordDraft(state, assetId);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving.current) return;
    saving.current = true; setBusy(true); setError('');
    try {
      if (!submitted.current) {
        if (rows.length > MAX_RECORDS_PER_BATCH) throw new Error(`单次最多登记 ${MAX_RECORDS_PER_BATCH} 条备份记录。`);
        // Validate all rows before creating any internal version/location IDs.
        const drafts = rows.map((row, index) => {
          if (!row.location.trim()) throw new Error(`第 ${index + 1} 行：请填写备份位置。`);
          if (row.location.trim().length > 200) throw new Error(`第 ${index + 1} 行：填写内容过长。`);
          return {...row, notes: notesForSave(row.notes), backed_up_at: toBackupTimestamp(row.time)};
        });
        const fresh = await request<LedgerState>('state');
        if (!backupTargets(fresh).some(asset => asset.id === assetId)) throw new Error('请先将末级资料设为备份对象。');
        let version = fresh.versions.find(item => item.asset_id === assetId && item.label === '未标注');
        if (!version) {
          try { version = await request<Version>('versions', 'POST', {asset_id: assetId, label: '未标注', notes: ''}); }
          catch (reason) {
            const recovered = await request<LedgerState>('state');
            version = recovered.versions.find(item => item.asset_id === assetId && item.label === '未标注');
            if (!version) throw reason;
          }
        }
        const resolveLocation = createLocationResolver(fresh.locations, request);
        const records = [];
        for (const draft of drafts) {
          const location = await resolveLocation(draft.location);
          records.push({version_id: version.id, location_id: location.id, backed_up_at: draft.backed_up_at, notes: draft.notes.trim()});
        }
        await request('records/batch', 'POST', {records});
        submitted.current = true;
      }
      await onSaved(); onClose();
    } catch (reason) { setError(reason instanceof Error ? reason.message : '登记失败，请重试。'); }
    finally { saving.current = false; setBusy(false); }
  }

  return <Modal title="登记备份" onClose={onClose} busy={busy} wide><form className="form-body record-entry-form" onSubmit={submit}><FormError message={error}/>
    {!eligible.length ? <div className="prerequisite"><p>暂无可登记的备份对象。</p><button type="button" className="button secondary" onClick={onChooseTargets}>前往资料目录</button></div> : <fieldset disabled={busy || submitted.current} className="record-entry-fields">
      <Field label="备份对象"><select required value={assetId} onChange={event => setAssetId(event.target.value)}>{eligible.map(asset => <option key={asset.id} value={asset.id}>{tree.path(asset.id)}</option>)}</select></Field>
      {previous ? <button type="button" className="text-button reuse-last" onClick={() => {
        const value = reuseLatestRecordDraft(state, assetId);
        if (value) setRows(current => current.map((row, index) => index === 0 ? {...row, ...value} : row));
      }}>沿用上次</button> : null}
      <datalist id={locationListId}>{state.locations.map(location => <option key={location.id} value={location.name}/>)}</datalist>
      <div className="table-scroll"><table className="record-entry-table"><thead><tr><th>备份时间</th><th>备份位置</th><th>备注</th><th><span className="sr-only">删除</span></th></tr></thead>
        <tbody>{rows.map((row, index) => <tr key={row.id}>
          <td data-label="备份时间"><input aria-label={`备份时间 ${index + 1}`} type="datetime-local" required value={row.time} onChange={event => update(row.id, {time: event.target.value})}/></td>
          <td data-label="备份位置"><input aria-label={`备份位置 ${index + 1}`} required maxLength={200} list={locationListId} value={row.location} onChange={event => update(row.id, {location: event.target.value})}/></td>
          <td data-label="备注"><input aria-label={`备注 ${index + 1}`} maxLength={MAX_NOTES_CHARS} title={`最多 ${MAX_NOTES_CHARS} 字`} value={row.notes} onChange={event => update(row.id, {notes: event.target.value})}/></td>
          <td><button type="button" className="icon-button" aria-label={`删除第 ${index + 1} 行`} disabled={rows.length === 1} onClick={() => setRows(current => current.filter(item => item.id !== row.id))}><Trash2 size={16}/></button></td>
        </tr>)}</tbody></table></div>
      <button className="text-button" type="button" disabled={rows.length >= MAX_RECORDS_PER_BATCH} title={`单次最多 ${MAX_RECORDS_PER_BATCH} 条`} onClick={() => { if (rows.length >= MAX_RECORDS_PER_BATCH) return; const row = newRow(nextId.current++); setRows(current => [...current, row]); }}><Plus size={16}/>添加一行</button>
    </fieldset>}
    <div className="form-actions"><button type="button" className="button secondary" disabled={busy} onClick={onClose}>取消</button><button className="button primary" type="submit" disabled={busy || !eligible.length}>{busy ? '正在登记…' : submitted.current ? '重试读取' : '确认登记'}</button></div>
  </form></Modal>;
}

export function RecordEditDialog({state, record, onClose, onSaved}: {state: LedgerState; record: BackupRecord; onClose: () => void; onSaved: () => Promise<void>}) {
  const location = state.locations.find(item => item.id === record.location_id);
  const version = state.versions.find(item => item.id === record.version_id);
  const tree = useMemo(() => buildHierarchy(state.assets), [state.assets]);
  const [draft, setDraft] = useState({location: location?.name ?? '', time: toDatetimeLocal(record.backed_up_at), notes: record.notes});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const saving = useRef(false);
  const committed = useRef(false);
  const locationListId = useId();
  const update = (key: keyof typeof draft, value: string) => setDraft(current => ({...current, [key]: value}));
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving.current) return;
    saving.current = true; setBusy(true); setError('');
    try {
      if (!committed.current) {
        const notes = notesForSave(draft.notes, record.notes);
        const backed_up_at = toBackupTimestamp(draft.time, record.backed_up_at);
        const resolveLocation = createLocationResolver(state.locations, request);
        const target = await resolveLocation(draft.location);
        await request(`records/${record.id}`, 'PUT', {version_id: record.version_id, location_id: target.id, backed_up_at, notes});
        committed.current = true;
      }
      await onSaved(); onClose();
    } catch (reason) { setError(reason instanceof Error ? reason.message : '保存失败，请重试。'); }
    finally { saving.current = false; setBusy(false); }
  }
  return <Modal title="编辑备份记录" onClose={onClose} busy={busy}><form className="form-body" onSubmit={submit}><FormError message={error}/>
    <fieldset className="record-entry-fields" disabled={busy || committed.current}>
    <Field label="资料"><input readOnly value={version ? tree.path(version.asset_id) : ''}/></Field>
    <Field label="备份时间"><input type="datetime-local" required value={draft.time} onChange={event => update('time', event.target.value)}/></Field>
    <Field label="备份位置"><input required maxLength={200} list={locationListId} value={draft.location} onChange={event => update('location', event.target.value)}/></Field>
    <datalist id={locationListId}>{state.locations.map(item => <option key={item.id} value={item.name}/>)}</datalist>
    <Field label="备注（选填）" hint={record.notes.length > MAX_NOTES_CHARS ? '原备注可保留，修改后限 2000 字。' : `${draft.notes.length}/${MAX_NOTES_CHARS}`}><textarea maxLength={Math.max(MAX_NOTES_CHARS, record.notes.length)} rows={3} value={draft.notes} onChange={event => update('notes', event.target.value)}/></Field>
    </fieldset><FormActions onCancel={onClose} busy={busy} label={committed.current ? '重试读取' : '保存'}/>
  </form></Modal>;
}
