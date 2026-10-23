import { useState } from 'react';
import type { FormEvent } from 'react';
import type { Asset, Collection, LedgerState, Location } from '../types';
import { AssetDialog } from './AssetForm';
import { Field, FormActions, FormError, Modal } from './ui';
import { displayPath, pathForSave } from '../pathDisplay';
import { MAX_NOTES_CHARS, notesForSave } from '../inputLimits';

export const locationKinds: Record<Location['kind'], string> = { local: '本机磁盘', external: '移动硬盘', cloud: '网盘', nas: 'NAS', other: '其他' };
export type EntityForm = { kind: 'assets'; entry?: Asset; initialKind?: Asset['kind']; parentId?: string | null } | { kind: 'locations'; entry?: Location };

export function EntityDialog({ form, state, onClose, onSave, onBulkSaved }: { form: EntityForm; state: LedgerState; onClose: () => void; onSave: (collection: Collection, data: unknown, id?: string) => Promise<void>; onBulkSaved?: () => Promise<void> }) {
  if (form.kind === 'assets') return <AssetDialog state={state} entry={form.entry} initialKind={form.initialKind} parentId={form.parentId} onClose={onClose} onSave={(data, id) => onSave('assets', data, id)} onBulkSaved={onBulkSaved} />;
  return <OtherEntityDialog form={form} onClose={onClose} onSave={onSave} />;
}
function OtherEntityDialog({ form, onClose, onSave }: { form: Exclude<EntityForm, {kind: 'assets'}>; onClose: () => void; onSave: (collection: Collection, data: unknown, id?: string) => Promise<void> }) {
  const [draft, setDraft] = useState<Record<string, string>>(() => ({name: form.entry?.name ?? '', kind: form.entry?.kind ?? 'external', base_path: displayPath(form.entry?.base_path ?? ''), notes: form.entry?.notes ?? ''}));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const update = (key: string, value: string) => setDraft(current => ({ ...current, [key]: value }));
  const title = `${form.entry ? '编辑' : '新增'}备份位置`;
  async function submit(event: FormEvent) {
    event.preventDefault(); setError(''); setBusy(true);
    try { const data = Object.fromEntries(Object.entries(draft).map(([key, value]) => [key, value.trim()])); data.base_path = pathForSave(draft.base_path, form.entry?.base_path); data.notes = notesForSave(draft.notes, form.entry?.notes); await onSave(form.kind, data, form.entry?.id); onClose(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '保存失败，请重试。'); }
    finally { setBusy(false); }
  }
  return <Modal title={title} onClose={onClose} busy={busy}><form onSubmit={submit} className="form-body"><FormError message={error} />
      <Field label="位置名称"><input required autoFocus maxLength={200} value={draft.name} placeholder="例如：移动硬盘 A" onChange={e => update('name', e.target.value)} /></Field>
      <Field label="类型"><select value={draft.kind} onChange={e => update('kind', e.target.value)}>{Object.entries(locationKinds).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></Field>
      <Field label="基础路径"><input value={draft.base_path} placeholder="例如：E:/备份" onChange={e => update('base_path', e.target.value)} /></Field>
    <Field label="备注（选填）"><textarea rows={3} maxLength={Math.max(MAX_NOTES_CHARS, form.entry?.notes.length ?? 0)} value={draft.notes} onChange={e => update('notes', e.target.value)} /></Field>
    <FormActions onCancel={onClose} busy={busy} /></form></Modal>;
}

export interface DeleteTarget { collection: Collection; id: string; name: string }
export function DeleteDialog({ target, onClose, onRemove }: { target: DeleteTarget; onClose: () => void; onRemove: (collection: Collection, id: string) => Promise<void> }) {
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try { await onRemove(target.collection, target.id); onClose(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '删除失败，请重试。'); }
    finally { setBusy(false); }
  }
  return <Modal title="确认删除" onClose={onClose} busy={busy}><form className="form-body" onSubmit={submit}><p className="delete-description">将「{target.name}」移入回收站？30 天内可恢复。</p>{target.collection !== 'records' ? <p className="muted">有关联备份记录时，系统会保留条目并说明原因。</p> : null}<FormError message={error} /><div className="form-actions"><button type="button" className="button secondary" disabled={busy} onClick={onClose}>取消</button><button className="button danger" type="submit" disabled={busy}>{busy ? '正在删除…' : '确认删除'}</button></div></form></Modal>;
}
