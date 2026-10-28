import { useEffect, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { request, useLedger } from './api';
import { useDirectoryNavigation } from './useDirectoryNavigation';
import type { Asset, BackupRecord, Page, RecordFilters } from './types';
import type { SessionInfo } from './types';
import { useSession } from './useSession';
import { buildHierarchy } from './hierarchy';
import { SessionGate } from './components/SessionGate';
import { Shell } from './components/Shell';
import { BackupDialog, RecordEditDialog } from './components/BackupForms';
import { BackupInfoDialog } from './components/BackupInfoDialog';
import { BulkBackupDialog } from './components/BulkBackupDialog';
import { RecordBulkEditDialog } from './components/RecordBulkEditDialog';
import { PrivacyWorkspace } from './components/PrivacyWorkspace';
import { DeleteDialog, EntityDialog } from './components/EntityForms';
import type { DeleteTarget, EntityForm } from './components/EntityForms';
import { Overview } from './features/Overview';
import { AssetDetail } from './features/AssetDetail';
import { History } from './features/History';

export default function App() {
  const session = useSession();
  if (session.snapshot.status !== 'authenticated' || !session.snapshot.session) return <SessionGate snapshot={session.snapshot} onLogin={session.login} onRetry={session.retry} onLogout={session.logout} />;
  return <PrivacyWorkspace key={session.snapshot.generation} loadingLabel={session.snapshot.session.auth_required?'正在读取台账…':'正在读取本机台账…'}>{controls=><LedgerApp session={session.snapshot.session!} onLogout={session.logout} {...controls}/>}</PrivacyWorkspace>;
}

function LedgerApp({session, onLogout,privacyOpen,onTogglePrivacy,onSetPrivate}: {session: SessionInfo; onLogout: () => Promise<void>;privacyOpen:boolean;onTogglePrivacy:()=>void;onSetPrivate:(asset:Asset)=>void}) {
  const ledger = useLedger();
  const {route, navigate} = useDirectoryNavigation(ledger.state);
  const [entity, setEntity] = useState<EntityForm | null>(null);
  const [deletion, setDeletion] = useState<DeleteTarget | null>(null);
  const [backup, setBackup] = useState<{ assetId?: string } | null>(null);
  const [bulk,setBulk]=useState<{assetIds?:string[];mode:'selection'|'paste'}|null>(null);
  const [record, setRecord] = useState<BackupRecord | null>(null);
  const [bulkRecordIds, setBulkRecordIds] = useState<string[] | null>(null);
  const [detailId, setDetailId] = useState('');
  const pendingDetail = useRef<{folderId: string | null; id: string} | null>(null);
  const [recordEditor, setRecordEditor] = useState<{assetId: string; mode: 'edit' | 'new'} | null>(null);
  const [marking, setMarking] = useState(false);
  const markPending = useRef(false);
  const [actionError, setActionError] = useState('');
  const [history, setHistory] = useState<{ filters: RecordFilters; key: number }>({ filters: {}, key: 0 });
  const [notice, setNoticeValue] = useState<{text: string; trashId?: string} | null>(null);
  const [undoBusy, setUndoBusy] = useState(false);
  const undoPending = useRef(false);
  const setNotice = (text: string, trashId?: string) => setNoticeValue({text, trashId});
  useEffect(() => { if (!notice || undoBusy) return; const timer = setTimeout(() => setNoticeValue(null), notice.trashId ? 15000 : 5000); return () => clearTimeout(timer); }, [notice, undoBusy]);
  const undoDeletion = async () => {
    if (!notice?.trashId || undoPending.current) return;
    undoPending.current = true; setUndoBusy(true); setActionError('');
    try {
      await request(`trash/${notice.trashId}/restore`, 'POST', {});
      setNotice('已恢复。');
      await ledger.refresh();
    } catch (reason) { setActionError(reason instanceof Error ? reason.message : '恢复失败，请重试。'); }
    finally { undoPending.current = false; setUndoBusy(false); }
  };
  useEffect(() => {
    const target = pendingDetail.current;
    setDetailId(route.page === 'overview' && target?.folderId === route.folderId ? target.id : '');
    pendingDetail.current = null;
  }, [route.page, route.folderId]);
  function navigatePage(page: Page) {
    pendingDetail.current = null;
    if (page === 'history') setHistory(current => ({ filters: {}, key: current.key + 1 }));
    navigate({page, folderId: null});
  }
  const openFolder = (folderId: string | null) => { pendingDetail.current = null; setDetailId(''); navigate({page: 'overview', folderId}); };
  const locateAsset = (asset: Asset) => {
    const folderId = asset.parent_id ?? null;
    if (route.page === 'overview' && route.folderId === folderId) setDetailId(asset.id);
    else { pendingDetail.current = {folderId, id: asset.id}; navigate({page: 'overview', folderId}); }
  };
  const createAsset = (initialKind: Asset['kind'], parentId: string | null) => setEntity({kind: 'assets', initialKind, parentId});
  const markAsset = async (entry: Asset) => {
    if (markPending.current) return;
    markPending.current = true; setMarking(true); setActionError('');
    const enabled = entry.backup_target !== true;
    try { await ledger.save('assets', {backup_target: enabled}, entry.id); setNotice(enabled ? '已设为备份对象。' : '已取消备份对象。'); }
    catch (reason) { setActionError(reason instanceof Error ? reason.message : '修改失败，请重试。'); }
    finally { markPending.current = false; setMarking(false); }
  };
  const saved = async () => { await ledger.refresh({throwOnError: true}); setNotice('备份记录已保存。'); };
  const deleted = async (trashId?: string) => { await ledger.refresh({throwOnError: true}); setNotice('已删除。', trashId); };
  const state = ledger.state;
  const detail = state?.assets.find(asset => asset.id === detailId);
  const directRecords = state && route.folderId && state.assets.some(asset => asset.id === route.folderId && asset.backup_target === true) && buildHierarchy(state.assets).isLeaf(route.folderId);
  const recordInfo = state?.assets.find(asset => asset.id === recordEditor?.assetId);
  const nas = session.auth_required;
  return <Shell page={route.page} onNavigate={navigatePage} authRequired={session.auth_required} onLogout={() => void onLogout()} privacyOpen={privacyOpen} onTogglePrivacy={onTogglePrivacy}>
    {notice ? <div className="toast" role="status"><span>{notice.text}</span>{notice.trashId ? <button type="button" className="text-button" disabled={undoBusy} onClick={()=>void undoDeletion()}>{undoBusy ? '正在恢复…' : '撤销'}</button> : null}</div> : null}
    {actionError ? <div className="connection-error" role="alert">{actionError}</div> : null}
    {ledger.error ? <div className="connection-error" role="alert"><span>{ledger.error}</span><button className="button secondary compact" disabled={ledger.loading} onClick={() => void ledger.refresh()}><RefreshCw size={16} />重试读取</button></div> : null}
    {!state ? <div className="initial-state"><h1>DataBack</h1><p>{ledger.loading ? `正在读取${nas ? '' : '本机'}台账…` : '恢复连接后即可继续查看和登记资料。'}</p></div> : <>
      {route.page === 'overview' ? <Overview key={`directory:${route.folderId ?? 'root'}`} state={state} folderId={route.folderId} onFolder={openFolder} onCreate={createAsset} onAssetForm={entry => setEntity({kind: 'assets', entry})} onMark={markAsset} marking={marking} onAssetDelete={asset => setDeletion({collection: 'assets', id: asset.id, name: asset.name})} onRegister={assetId => setBackup({assetId})} onDetail={setDetailId} onLocateAsset={locateAsset} onEditRecords={assetId => setRecordEditor({assetId, mode: 'edit'})} onAddRecord={assetId => setRecordEditor({assetId, mode: 'new'})} onReorder={ledger.reorder} onRefresh={ledger.refresh} onBulk={assetIds=>setBulk({assetIds,mode:'selection'})} onPaste={()=>setBulk({mode:'paste'})} onPrivacy={onSetPrivate}/> : null}
      {route.page === 'history' ? <History key={history.key} state={state} initialFilters={history.filters} onRegister={() => setBackup({})} onBulk={()=>setBulk({mode:'selection'})} onBulkEdit={setBulkRecordIds} onEdit={setRecord} onDelete={entry => setDeletion({collection: 'records', id: entry.id, name: '备份记录'})} /> : null}
      {detail && route.page === 'overview' && !directRecords ? <AssetDetail key={`detail:${detail.id}`} asset={detail} state={state} onClose={() => setDetailId('')} onEditRecords={() => setRecordEditor({assetId: detail.id, mode: 'edit'})} onAddRecord={buildHierarchy(state.assets).isLeaf(detail.id) ? () => setRecordEditor({assetId: detail.id, mode: 'new'}) : undefined} /> : null}
      {recordInfo && recordEditor ? <BackupInfoDialog key={`record-editor:${recordInfo.id}:${recordEditor.mode}`} state={state} asset={recordInfo} initialMode={recordEditor.mode} onClose={() => setRecordEditor(null)} onSaved={saved} onDeleted={deleted} /> : null}
      {backup ? <BackupDialog state={state} initialAssetId={backup.assetId} onClose={() => setBackup(null)} onSaved={saved} onChooseTargets={() => { setBackup(null); openFolder(null); }} /> : null}
      {bulk?<BulkBackupDialog state={state} initialAssetIds={bulk.assetIds} initialMode={bulk.mode} onClose={()=>setBulk(null)} onSaved={saved}/>:null}
      {record ? <RecordEditDialog state={state} record={record} onClose={() => setRecord(null)} onSaved={saved} /> : null}
      {bulkRecordIds ? <RecordBulkEditDialog state={state} ids={bulkRecordIds} onClose={()=>setBulkRecordIds(null)} onSaved={saved}/> : null}
      {entity ? <EntityDialog key={`${entity.kind}-${entity.entry?.id ?? 'new'}`} state={state} form={entity} onClose={() => setEntity(null)} onBulkSaved={async()=>{await ledger.refresh({throwOnError:true}); setNotice('资料已批量创建。');}} onSave={async (collection, data, id) => {
        await ledger.save(collection, data, id);
        if (collection === 'assets') openFolder((data as {parent_id: string | null}).parent_id);
        setNotice(nas ? '已保存。' : '已保存到本机台账。');
      }} /> : null}
    </>}
    {deletion ? <DeleteDialog target={deletion} onClose={() => setDeletion(null)} onRemove={async (collection, id) => { const receipt = await ledger.remove(collection, id); setNotice('已删除。', receipt.trash_id); }} /> : null}
  </Shell>;
}
