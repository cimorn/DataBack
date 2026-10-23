import {useId, useRef, useState} from 'react';
import type {FormEvent} from 'react';
import {request} from '../api';
import {MAX_NOTES_CHARS, MAX_RECORDS_PER_BATCH} from '../inputLimits';
import {buildBulkRecordChanges} from '../recordEditing';
import type {RecordChangeFields, RecordDraft} from '../recordEditing';
import {toDatetimeLocal} from '../selectors';
import type {LedgerState} from '../types';
import {Field, FormError, Modal} from './ui';

export function RecordBulkEditDialog({state, ids, onClose, onSaved}: {
  state: LedgerState; ids: string[]; onClose: () => void; onSaved: () => Promise<void>;
}) {
  const uniqueIds = [...new Set(ids)];
  const [fields, setFields] = useState<RecordChangeFields>({time: false, location: false, notes: false});
  const [draft, setDraft] = useState<RecordDraft>({time: toDatetimeLocal(), location: '', notes: ''});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const committed = useRef(false);
  const pending = useRef(false);
  const locationListId = useId();
  const update = (key: keyof RecordDraft, value: string) => setDraft(current => ({...current, [key]: value}));
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending.current) return;
    pending.current = true; setBusy(true); setError('');
    try {
      if (!committed.current) {
        if (!uniqueIds.length || uniqueIds.length > MAX_RECORDS_PER_BATCH) throw new Error(`请选择 1–${MAX_RECORDS_PER_BATCH} 条记录。`);
        const changes = buildBulkRecordChanges(fields, draft);
        await request('records/bulk', 'PUT', {ids: uniqueIds, changes});
        committed.current = true;
      }
      await onSaved(); onClose();
    } catch (reason) { setError(reason instanceof Error ? reason.message : '修改失败，请重试。'); }
    finally { pending.current = false; setBusy(false); }
  }
  return <Modal title={`批量修改 · ${uniqueIds.length} 条记录`} onClose={onClose} busy={busy}>
    <form className="form-body bulk-record-edit" onSubmit={submit}><FormError message={error}/>
      <fieldset disabled={busy || committed.current} className="record-entry-fields">
        <label className="checkbox-row"><input type="checkbox" checked={fields.time} onChange={event => setFields(current => ({...current, time: event.target.checked}))}/>修改备份时间</label>
        <Field label="备份时间"><input type="datetime-local" required={fields.time} disabled={!fields.time} value={draft.time} onChange={event => update('time', event.target.value)}/></Field>
        <label className="checkbox-row"><input type="checkbox" checked={fields.location} onChange={event => setFields(current => ({...current, location: event.target.checked}))}/>修改备份位置</label>
        <Field label="备份位置"><input required={fields.location} disabled={!fields.location} maxLength={200} list={locationListId} value={draft.location} onChange={event => update('location', event.target.value)}/></Field>
        <datalist id={locationListId}>{state.locations.map(location => <option key={location.id} value={location.name}/>)}</datalist>
        <label className="checkbox-row"><input type="checkbox" checked={fields.notes} onChange={event => setFields(current => ({...current, notes: event.target.checked}))}/>修改备注</label>
        <Field label="备注" hint="留空将清除所选记录的备注"><textarea rows={3} disabled={!fields.notes} maxLength={MAX_NOTES_CHARS} value={draft.notes} onChange={event => update('notes', event.target.value)}/></Field>
      </fieldset>
      <div className="form-actions"><button type="button" className="button secondary" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="button primary" disabled={busy || !uniqueIds.length || (!fields.time && !fields.location && !fields.notes)}>{busy ? '正在保存…' : committed.current ? '重试读取' : '确认修改'}</button></div>
    </form>
  </Modal>;
}
