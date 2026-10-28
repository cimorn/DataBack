import { useMemo, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import type { Asset, LedgerState } from '../types';
import { buildHierarchy } from '../hierarchy';
import { previewDirectoryBatch } from '../directoryTools';
import { request } from '../api';
import { Field, FormActions, FormError, Modal } from './ui';

export function AssetDialog({ state, entry, initialKind = 'folder', parentId = null, onClose, onSave, onBulkSaved }: {
  state: LedgerState; entry?: Asset; initialKind?: Asset['kind']; parentId?: string | null;
  onClose: () => void; onSave: (data: unknown, id?: string) => Promise<void>;
  onBulkSaved?: () => Promise<void>;
}) {
  const tree = useMemo(() => buildHierarchy(state.assets), [state.assets]);
  const [draft, setDraft] = useState(() => ({name: entry?.name ?? '', kind: entry?.kind ?? initialKind, parent_id: entry?.parent_id ?? parentId ?? ''}));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [mode, setMode] = useState<'single' | 'batch'>('single');
  const [paths, setPaths] = useState('');
  const submitted = useRef(false);
  const pending = useRef(false);
  const batch = !entry && mode === 'batch';
  const preview = useMemo(() => previewDirectoryBatch(state.assets, draft.parent_id || null, draft.kind, paths), [state.assets, draft.parent_id, draft.kind, paths]);
  const hasChildren = entry ? !tree.isLeaf(entry.id) : false;
  function update<K extends keyof typeof draft>(key: K, value: (typeof draft)[K]) { setDraft(current => ({...current, [key]: value})); }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending.current || (batch && !submitted.current && (!preview.valid || !preview.created))) return;
    pending.current = true;
    setBusy(true); setError('');
    try {
      if (batch) {
        if (!submitted.current) {
          await request('assets/bulk', 'POST', {parent_id: draft.parent_id || null, kind: draft.kind, paths: preview.paths});
          submitted.current = true;
        }
        await onBulkSaved?.();
      } else await onSave({name: draft.name.trim(), kind: draft.kind, parent_id: draft.parent_id || null}, entry?.id);
      onClose();
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : '保存失败，请重试。'); }
    finally { pending.current = false; setBusy(false); }
  }
  return <Modal title={entry ? '编辑资料' : initialKind === 'file' ? '添加文件' : '新建文件夹'} wide={batch} busy={busy} onClose={onClose}><form className="form-body" onSubmit={submit}><FormError message={error} />
    {!entry ? <div className="directory-create-modes" role="tablist" aria-label="添加方式"><button type="button" role="tab" aria-selected={!batch} disabled={busy || submitted.current} onClick={() => { setMode('single'); setError(''); }}>单个</button><button type="button" role="tab" aria-selected={batch} disabled={busy || submitted.current} onClick={() => { setMode('batch'); setError(''); }}>批量</button></div> : null}
    {!batch ? <Field label="资料名称"><input autoFocus required disabled={busy} maxLength={200} placeholder={draft.kind === 'file' ? '例如：旅行清单.pdf' : '例如：Photos 或 2025'} value={draft.name} onChange={e => update('name', e.target.value)} /></Field> : null}
    <div className="form-grid"><Field label="类型" hint={hasChildren ? '已有下级，仅可选文件夹。' : undefined}><select disabled={busy || submitted.current} value={draft.kind} onChange={e => update('kind', e.target.value as Asset['kind'])}><option value="folder">文件夹</option><option value="file" disabled={hasChildren}>文件</option></select></Field>
    <Field label="所属目录"><select disabled={busy || submitted.current} value={draft.parent_id} onChange={e => update('parent_id', e.target.value)}><option value="">全部（根目录）</option>{tree.moveOptions(entry?.id).map(asset => <option key={asset.id} value={asset.id}>{tree.path(asset.id)}</option>)}</select></Field></div>
    {batch ? <><Field label="名称或相对路径" hint="每行一项，最多 200 行；用 / 分隔目录。"><textarea required rows={6} maxLength={200000} disabled={busy || submitted.current} value={paths} placeholder={draft.kind === 'file' ? 'Photos/2026/旅行.jpg\n笔记/清单.md' : 'Photos/2026\nEmotion/Letter'} onChange={event => { setPaths(event.target.value); setError(''); }} /></Field><FormError message={preview.error} />
      {preview.valid ? <section className="directory-batch-preview" aria-label="创建预览"><div className="directory-batch-summary" role="status">新增 {preview.created} 项 · 已有跳过 {preview.skipped} 项</div><div className="directory-batch-rows"><table><thead><tr><th>名称</th><th>类型</th><th>处理</th></tr></thead><tbody>{preview.rows.map((row, index) => <tr key={`${index}-${row.path}`}><td>{row.path}</td><td>{row.kind === 'file' ? '文件' : '文件夹'}</td><td>{row.status === 'create' ? '新增' : '跳过'}</td></tr>)}</tbody></table></div></section> : null}
      <div className="form-actions"><button type="button" className="button secondary" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="button primary" disabled={busy || (!submitted.current && (!preview.valid || !preview.created))}>{busy ? '正在保存…' : submitted.current ? '重试读取' : '确认添加'}</button></div></> : <FormActions busy={busy} onCancel={onClose} />}
  </form></Modal>;
}
