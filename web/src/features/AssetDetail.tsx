import { Pencil, Plus, X } from 'lucide-react';
import { filterRecords, formatTime } from '../selectors';
import type { Asset, LedgerState } from '../types';

interface AssetDetailProps {
  asset: Asset;
  state: LedgerState;
  onClose?: () => void;
  onEditRecords: () => void;
  onAddRecord?: () => void;
}

export function AssetDetail({asset, state, onClose, onEditRecords, onAddRecord}: AssetDetailProps) {
  const records = filterRecords(state, {assetId: asset.id});
  const locations = new Map(state.locations.map(location => [location.id, location.name]));

  return <section className="detail-panel" aria-label={`${asset.name}的备份信息`}>
    <div className="detail-heading"><h2>备份信息 · {asset.name}</h2><div className="detail-actions">
      {onAddRecord ? <button className="button primary compact" aria-label={`新增备份记录 ${asset.name}`} onClick={onAddRecord}><Plus size={16}/>新增记录</button> : null}
      <button className="button secondary compact" aria-label={`编辑备份信息 ${asset.name}`} onClick={onEditRecords}><Pencil size={16}/>编辑</button>
      {onClose ? <button className="icon-button" aria-label="关闭备份信息" onClick={onClose}><X size={20}/></button> : null}
    </div></div>
    <div className="table-scroll record-table-scroll"><table className="detail-table record-detail-table">
      <thead><tr><th>备份时间</th><th>备份位置</th><th>备注</th></tr></thead>
      <tbody>{records.length ? records.map(record => <tr key={record.id}>
        <td><time dateTime={record.backed_up_at}>{formatTime(record.backed_up_at)}</time></td>
        <td>{locations.get(record.location_id) ?? '—'}</td>
        <td className="record-notes">{record.notes || '—'}</td>
      </tr>) : <tr><td colSpan={3} className="muted">尚未登记备份。</td></tr>}</tbody>
    </table></div>
  </section>;
}
