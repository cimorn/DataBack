import { useMemo, useState } from 'react';
import { ListPlus, Pencil, Search, Trash2 } from 'lucide-react';
import { filterRecords, formatTime } from '../selectors';
import { buildHierarchy } from '../hierarchy';
import type { BackupRecord, LedgerState, RecordFilters } from '../types';
import { EmptyState, Field, PageHeading } from '../components/ui';
import { RegisterButton } from '../components/Shell';
import { MAX_RECORDS_PER_BATCH } from '../inputLimits';

export function History({ state, initialFilters, onRegister, onBulk, onEdit, onDelete, onBulkEdit }: { state: LedgerState; initialFilters: RecordFilters; onRegister: () => void; onBulk?:()=>void; onEdit: (record: BackupRecord) => void; onDelete: (record: BackupRecord) => void; onBulkEdit?: (ids: string[]) => void }) {
  const [filters, setFilters] = useState<RecordFilters>(initialFilters);
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const tree = useMemo(() => buildHierarchy(state.assets), [state.assets]);
  const records = useMemo(() => filterRecords(state, {...filters, includeDescendants: true}), [state, filters]);
  const scopes = tree.children(null).flatMap(root => [root, ...tree.children(root.id)]);
  const selectedVisible = selected.filter(id => records.some(record => record.id === id));
  const toggle = (id: string) => setSelected(current => current.includes(id) ? current.filter(item => item !== id) : current.length < MAX_RECORDS_PER_BATCH ? [...current, id] : current);
  const cancel = () => {setSelecting(false); setSelected([]);};
  const update = (key: 'assetId' | 'locationId' | 'dateFrom' | 'dateTo' | 'keyword', value: string) => {setSelected([]); setFilters(current => ({...current, [key]: value}));};
  return <><PageHeading title="历史记录" action={<div className="heading-actions">{selecting ? <>
    <button className="button primary" disabled={!selectedVisible.length} onClick={() => {const ids = [...selectedVisible]; cancel(); onBulkEdit?.(ids);}}>修改所选{selectedVisible.length ? `（${selectedVisible.length}）` : ''}</button>
    <button className="button secondary" aria-label="取消批量修改" onClick={cancel}>取消</button>
  </> : <>{onBulkEdit ? <button className="button secondary" disabled={!records.length} onClick={() => {setSelecting(true);setSelected([]);}}><Pencil size={18}/>批量修改</button> : null}{onBulk?<button className="button secondary" onClick={onBulk}><ListPlus size={18}/>批量登记</button>:null}<RegisterButton onClick={onRegister} /></>}</div>} />
    <div className="history-filters surface"><label className="search-field"><Search size={18} /><input aria-label="搜索备份记录" value={filters.keyword ?? ''} placeholder="搜索资料、位置或备注" onChange={e => update('keyword', e.target.value)} /></label>
      <Field label="资料"><select value={filters.assetId ?? ''} onChange={e => update('assetId', e.target.value)}><option value="">资料</option>{scopes.map(asset => <option key={asset.id} value={asset.id}>{tree.path(asset.id)}</option>)}</select></Field>
      <Field label="位置"><select value={filters.locationId ?? ''} onChange={e => update('locationId', e.target.value)}><option value="">全部位置</option>{state.locations.map(location => <option key={location.id} value={location.id}>{location.name}</option>)}</select></Field>
      <Field label="开始日期"><input type="date" value={filters.dateFrom ?? ''} onChange={e => update('dateFrom', e.target.value)} /></Field>
      <Field label="结束日期（含当天）"><input type="date" value={filters.dateTo ?? ''} onChange={e => update('dateTo', e.target.value)} /></Field>
      <button className="text-button" onClick={() => {setFilters({});setSelected([]);}}>清除筛选</button></div>
    {filters.dateFrom && filters.dateTo && filters.dateFrom > filters.dateTo ? <p className="error-message" role="alert">开始日期不能晚于结束日期。</p> : null}
    {selecting && records.length > MAX_RECORDS_PER_BATCH ? <p className="muted">每次最多选择 {MAX_RECORDS_PER_BATCH} 条记录。</p> : null}
    <div className="surface">{!records.length ? <EmptyState title={state.records.length ? '没有符合条件的记录' : '还没有备份记录'} /> : <div className="table-scroll"><table className="history-table"><thead><tr>
      {selecting ? <th className="history-select-cell"><input type="checkbox" aria-label={records.length > MAX_RECORDS_PER_BATCH ? `选择前 ${MAX_RECORDS_PER_BATCH} 条记录` : '选择当前记录'} checked={selectedVisible.length === Math.min(records.length,MAX_RECORDS_PER_BATCH)} onChange={event => setSelected(event.target.checked ? records.slice(0,MAX_RECORDS_PER_BATCH).map(record => record.id) : [])}/></th> : null}
      <th>名称</th><th>备份位置</th><th>备份时间</th><th>备注</th>{!selecting ? <th>操作</th> : null}</tr></thead><tbody>{records.map(record => { const version = state.versions.find(item => item.id === record.version_id); const asset = state.assets.find(item => item.id === version?.asset_id); const location = state.locations.find(item => item.id === record.location_id); return <tr key={record.id}>
        {selecting ? <td className="history-select-cell"><input type="checkbox" aria-label={`选择记录 ${asset?.name ?? '资料'} ${formatTime(record.backed_up_at)} ${location?.name ?? ''}`} checked={selectedVisible.includes(record.id)} disabled={selectedVisible.length >= MAX_RECORDS_PER_BATCH && !selectedVisible.includes(record.id)} onChange={() => toggle(record.id)}/></td> : null}
        <td><strong className="path">{asset ? tree.path(asset.id) : '资料已移除'}</strong></td><td>{location?.name ?? '位置已移除'}</td><td><time dateTime={record.backed_up_at}>{formatTime(record.backed_up_at)}</time></td><td className="record-notes">{record.notes || '—'}</td>{!selecting ? <td><div className="row-actions"><button className="icon-button" aria-label={`编辑记录 ${asset?.name} ${location?.name}`} onClick={() => onEdit(record)}><Pencil size={16} /></button><button className="icon-button" aria-label={`删除记录 ${asset?.name} ${location?.name}`} onClick={() => onDelete(record)}><Trash2 size={16} /></button></div></td> : null}</tr>; })}</tbody></table></div>}</div>
  </>;
}


