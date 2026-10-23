import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUp, ChevronRight, File, FilePlus2, Folder, FolderPlus, GripVertical, ListPlus, Search } from 'lucide-react';
import { buildHierarchy } from '../hierarchy';
import { formatTime, latestBackupForAsset } from '../selectors';
import type { Asset, LedgerState } from '../types';
import { EmptyState, PageHeading } from '../components/ui';
import { AssetDetail } from './AssetDetail';
import { ActionMenu } from '../components/ActionMenu';
import { advanceDirectoryDrag, finishDirectoryDrag, moveDirectoryAsset, sortDirectoryAssets, startDirectoryDrag } from '../directoryOrder';
import type { DirectoryDrag, DirectorySort } from '../directoryOrder';
import { directoryNavigation, searchDirectoryAssets } from '../directoryTools';
import type { DirectorySearchScope } from '../directoryTools';

interface OverviewActions {
  state: LedgerState; folderId: string | null;
  onFolder: (id: string | null) => void;
  onCreate: (kind: Asset['kind'], parentId: string | null) => void;
  onAssetForm: (asset: Asset) => void;
  onMark: (asset: Asset) => void;
  marking?: boolean;
  onAssetDelete: (asset: Asset) => void;
  onDetail: (id: string) => void;
  onLocateAsset?: (asset: Asset) => void;
  onEditRecords: (id: string) => void;
  onAddRecord: (id: string) => void;
  onRegister: (assetId: string) => void;
  onReorder: (parentId: string | null, ids: string[]) => Promise<void>;
  onRefresh: () => Promise<void>;
  onBulk?: (assetIds:string[])=>void;
  onPaste?:()=>void;
  onPrivacy?: (asset:Asset)=>void;
}
export function Overview(actions: OverviewActions) {
  const {state, folderId} = actions;
  const tree = useMemo(() => buildHierarchy(state.assets), [state.assets]);
  const [keyword, setKeyword] = useState('');
  const [searchScope, setSearchScope] = useState<DirectorySearchScope>('current');
  const [sort, setSort] = useState<DirectorySort>('manual');
  const [selected,setSelected]=useState<string[]>([]);
  const [bulkSelecting,setBulkSelecting]=useState(false);
  const [pending, setPending] = useState(false);
  const [orderError, setOrderError] = useState('');
  const dragging = useRef<{gesture: DirectoryDrag; pointerId: number; handle: HTMLButtonElement} | null>(null);
  const [dragVisual, setDragVisual] = useState<{sourceId: string; targetId: string | null} | null>(null);
  const cancelDrag = () => {
    const current = dragging.current;
    dragging.current = null;
    setDragVisual(null);
    if (current?.handle.hasPointerCapture(current.pointerId)) current.handle.releasePointerCapture(current.pointerId);
  };
  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') cancelDrag(); };
    document.addEventListener('keydown', escape);
    window.addEventListener('blur', cancelDrag);
    return () => { document.removeEventListener('keydown', escape); window.removeEventListener('blur', cancelDrag); cancelDrag(); };
  }, []);
  useEffect(() => { cancelDrag(); }, [sort, keyword, searchScope, pending]);
  useEffect(() => { setKeyword(''); setSearchScope('current'); setSelected([]); setBulkSelecting(false); cancelDrag(); }, [folderId]);
  const saving = useRef(false);
  const folder = folderId ? tree.byId.get(folderId) : undefined;
  const showingRecords = folder?.backup_target === true && tree.isLeaf(folder.id);
  const ancestors = folderId ? tree.ancestors(folderId) : [];
  const siblings = sortDirectoryAssets(state, tree.children(folderId), sort);
  const searchingAll = searchScope === 'all' && !!keyword.trim();
  const visibleAssets = sortDirectoryAssets(state, searchDirectoryAssets(state.assets, folderId, keyword, searchScope), sort);
  const showBackupNotes = visibleAssets.some(asset => asset.backup_target === true && tree.isLeaf(asset.id));
  const rows = visibleAssets.map(asset => {
    const leaf = tree.isLeaf(asset.id);
    const latest = leaf ? latestBackupForAsset(state, asset.id) : tree.leafDescendants(asset.id)
      .filter(entry => entry.backup_target === true)
      .flatMap(entry => { const record = latestBackupForAsset(state, entry.id); return record ? [record] : []; })
      .sort((a, b) => Date.parse(b.backed_up_at) - Date.parse(a.backed_up_at))[0];
    return {asset, leaf, latest};
  });
  const canReorder = sort === 'manual' && !keyword.trim() && !pending && !bulkSelecting;
  const selectableIds=rows.filter(({asset,leaf})=>leaf?asset.backup_target===true:tree.leafDescendants(asset.id).some(child=>child.backup_target===true)).map(({asset})=>asset.id);
  const selectedVisible=selectableIds.filter(id=>selected.includes(id));
  const allSelected=selectableIds.length>0&&selectedVisible.length===selectableIds.length;
  const toggle=(id:string)=>setSelected(current=>current.includes(id)?current.filter(value=>value!==id):[...current,id]);
  const cancelSelection=()=>{setSelected([]);setBulkSelecting(false);};
  const registerSelected=()=>{
    const ids=selectedVisible;
    if(!ids.length)return;
    cancelSelection();actions.onBulk?.(ids);
  };
  const openEntry = (asset: Asset, marked: boolean) => {
    const target = directoryNavigation(asset, marked, searchingAll);
    if ('folderId' in target && target.detailId && actions.onLocateAsset) {
      setKeyword(''); setSearchScope('current'); cancelSelection();
      actions.onLocateAsset(asset);
      return;
    }
    if ('folderId' in target) {
      setKeyword(''); setSearchScope('current'); cancelSelection();
      actions.onFolder(target.folderId ?? null);
    }
    if (target.detailId) actions.onDetail(target.detailId);
  };
  const move = async (source: string, target: string) => {
    if (!canReorder || saving.current) return;
    const ids = moveDirectoryAsset(siblings.map(asset => asset.id), source, target);
    if (!ids) return;
    saving.current = true; setPending(true); setOrderError(''); cancelDrag();
    try { await actions.onReorder(folderId, ids); }
    catch (reason) { setOrderError(reason instanceof Error ? reason.message : '保存排序失败，请重试。'); }
    finally { saving.current = false; setPending(false); }
  };
  return <><PageHeading title="资料目录" action={folder?.kind !== 'file' ? <div className="heading-actions">{actions.onBulk&&!showingRecords?bulkSelecting?<><button className="button primary" disabled={!selectedVisible.length||pending} onClick={registerSelected}><ListPlus size={18}/>{selectedVisible.length?`登记所选（${selectedVisible.length}）`:'登记所选'}</button>{actions.onPaste?<button className="button secondary" onClick={()=>{cancelSelection();actions.onPaste!();}}>粘贴表格</button>:null}<button className="button secondary" aria-label="取消批量登记" onClick={cancelSelection}>取消</button></>:<button className="button secondary" onClick={()=>{cancelDrag();setSelected([]);setBulkSelecting(true);}}><ListPlus size={18}/>批量登记</button>:null}{!bulkSelecting?<><button className="button secondary" onClick={() => actions.onCreate('file', folderId)}><FilePlus2 size={19} />添加文件</button><button className="button primary" onClick={() => actions.onCreate('folder', folderId)}><FolderPlus size={20} />新建文件夹</button></>:null}</div> : undefined} />
    <div className="directory-toolbar"><nav className="breadcrumbs" aria-label="资料目录路径">{folder ? <button className="up-button" aria-label="返回上级目录" onClick={() => actions.onFolder(folder.parent_id ?? null)}><ArrowUp size={18} /></button> : null}<button onClick={() => actions.onFolder(null)}>全部资料</button>{[...ancestors, ...(folder ? [folder] : [])].map((entry, index, all) => <span key={entry.id}><ChevronRight size={16} />{index === all.length - 1 ? <strong aria-current="location">{entry.name}</strong> : <button onClick={() => actions.onFolder(entry.id)}>{entry.name}</button>}</span>)}</nav>
    {showingRecords ? null : <div className="toolbar explorer-toolbar"><div className="directory-search"><select aria-label="搜索范围" value={searchScope} onChange={event => { setSearchScope(event.target.value as DirectorySearchScope); setSelected([]); }}><option value="current">当前目录</option><option value="all">全部资料</option></select><label className="search-field"><Search size={19} /><input aria-label={searchScope === 'all' ? '搜索全部资料' : '搜索当前目录'} placeholder={searchScope === 'all' ? '搜索全部资料' : '搜索当前目录'} value={keyword} onChange={event => { setKeyword(event.target.value); setSelected([]); }} /></label></div><select aria-label="目录排序" value={sort} onChange={e => { cancelDrag(); setSort(e.target.value as DirectorySort); }}><option value="manual">手动排序</option><option value="name-asc">名称升序</option><option value="name-desc">名称降序</option><option value="newest">最新备份优先</option><option value="oldest">最久未备份优先</option></select></div>}</div>
    {orderError ? <div className="connection-error" role="alert"><span>{orderError}</span><button className="button secondary compact" disabled={pending} onClick={()=>void actions.onRefresh().then(()=>setOrderError(''))}>刷新目录</button></div> : null}
    {showingRecords ? <AssetDetail asset={folder} state={state} onEditRecords={() => actions.onEditRecords(folder.id)} onAddRecord={() => actions.onAddRecord(folder.id)} /> : <div className="surface explorer-surface" aria-busy={pending}>{!rows.length ? keyword ? <EmptyState title={searchingAll ? '没有匹配的资料' : '当前目录没有匹配的资料'} action={<button className="text-button" onClick={() => { setKeyword(''); setSelected([]); }}>清除搜索</button>} /> : <EmptyState title={folder ? '暂无下级资料' : '暂无资料'} action={folder ? <div className="empty-actions"><button className="button secondary" onClick={() => actions.onDetail(folder.id)}>备份信息</button><button className="button primary" disabled={actions.marking} onClick={() => actions.onMark(folder)}>设为备份对象</button></div> : undefined} /> : <table className={`explorer-table${showBackupNotes ? ' with-backup-notes' : ''}`}><thead><tr><th>{actions.onBulk&&bulkSelecting?<label className="directory-select-all"><input type="checkbox" aria-label={searchingAll ? '全选搜索结果备份资料' : '全选当前目录备份资料'} checked={allSelected} disabled={!selectableIds.length||pending} ref={node=>{if(node)node.indeterminate=selectedVisible.length>0&&!allSelected;}} onChange={()=>setSelected(current=>allSelected?current.filter(id=>!selectableIds.includes(id)):[...new Set([...current,...selectableIds])])}/><span>名称</span></label>:'名称'}</th><th>最新备份</th>{showBackupNotes ? <th>备份说明</th> : null}<th>操作</th></tr></thead><tbody>{rows.map(({asset, leaf, latest}) => {
      const Icon = asset.kind === 'folder' ? Folder : File;
      const marked = leaf && asset.backup_target === true;
      const index=siblings.findIndex(entry=>entry.id===asset.id);
      return <tr key={asset.id} data-order-id={asset.id} className={dragVisual?.targetId===asset.id ? 'sorting-target' : dragVisual?.sourceId===asset.id ? 'sorting-source' : undefined}><td data-label="名称"><div className="explorer-identity">{actions.onBulk&&bulkSelecting?<input type="checkbox" className="directory-selection" aria-label={`选择 ${asset.name}`} checked={selected.includes(asset.id)} disabled={!selectableIds.includes(asset.id)||pending} onChange={()=>toggle(asset.id)}/>:null}{!bulkSelecting&&!searchingAll?<button type="button" className="drag-handle" aria-label={`拖动排序 ${asset.name}`} title="拖动排序" draggable={false} disabled={!canReorder} onPointerDown={event=>{
        if (!canReorder || !event.isPrimary || event.button!==0) return;
        const gesture=startDirectoryDrag(asset.id,folderId,siblings.map(entry=>entry.id),event.clientX,event.clientY);
        if (!gesture) return;
        event.preventDefault();
        dragging.current={gesture,pointerId:event.pointerId,handle:event.currentTarget};
        event.currentTarget.setPointerCapture(event.pointerId);
        setDragVisual({sourceId:asset.id,targetId:null});
      }} onPointerMove={event=>{
        const current=dragging.current;
        if (!current || current.pointerId!==event.pointerId) return;
        if (!canReorder) { cancelDrag(); return; }
        event.preventDefault();
        const hit=document.elementFromPoint(event.clientX,event.clientY)?.closest<HTMLTableRowElement>('tr[data-order-id]');
        const body=event.currentTarget.closest('tbody');
        const target=hit && body?.contains(hit) ? hit.dataset.orderId ?? null : null;
        const gesture=advanceDirectoryDrag(current.gesture,siblings.map(entry=>entry.id),folderId,event.clientX,event.clientY,target);
        if (!gesture) { cancelDrag(); return; }
        current.gesture=gesture;
        setDragVisual(previous=>previous?.sourceId===gesture.sourceId && previous.targetId===gesture.targetId ? previous : {sourceId:gesture.sourceId,targetId:gesture.targetId});
      }} onPointerUp={event=>{
        const current=dragging.current;
        if (!current || current.pointerId!==event.pointerId) return;
        event.preventDefault();
        const hit=document.elementFromPoint(event.clientX,event.clientY)?.closest<HTMLTableRowElement>('tr[data-order-id]');
        const body=event.currentTarget.closest('tbody');
        const target=hit && body?.contains(hit) ? hit.dataset.orderId ?? null : null;
        const ids=siblings.map(entry=>entry.id);
        const gesture=advanceDirectoryDrag(current.gesture,ids,folderId,event.clientX,event.clientY,target);
        const next=canReorder ? finishDirectoryDrag(gesture,ids,folderId) : null;
        cancelDrag();
        if (next && gesture?.targetId) void move(gesture.sourceId,gesture.targetId);
      }} onPointerCancel={cancelDrag} onLostPointerCapture={cancelDrag} onClick={event=>event.preventDefault()}><GripVertical size={17}/></button>:null}<Icon size={28} strokeWidth={1.6} className={asset.kind === 'folder' ? 'folder-icon' : 'file-icon'} /><div><button className="asset-name" onClick={() => openEntry(asset, marked)}>{asset.name}</button>{searchingAll ? <span className="directory-result-path">{tree.path(asset.id)}</span> : null}</div></div></td><td data-label="最新备份"><time dateTime={latest?.backed_up_at}>{latest ? formatTime(latest.backed_up_at) : marked ? '未登记' : '—'}</time></td>{showBackupNotes ? <td data-label="备份说明"><span className="directory-backup-notes">{marked ? latest?.notes || '—' : '—'}</span></td> : null}<td data-label="操作"><ActionMenu label={`编辑 ${asset.name}`} actions={[
        {label:'备份信息',onSelect:()=>actions.onDetail(asset.id)},
        ...(marked ? [{label:'登记备份',onSelect:()=>actions.onRegister(asset.id)}] : []),
        ...(leaf || asset.backup_target === true ? [{label:asset.backup_target === true ? '取消备份对象' : '设为备份对象',disabled:actions.marking,onSelect:()=>actions.onMark(asset)}] : []),
        {label:'编辑资料',onSelect:()=>actions.onAssetForm(asset)},
        ...(asset.kind==='folder'&&actions.onPrivacy?[{label:asset.private?'取消隐私':'设为隐私',onSelect:()=>actions.onPrivacy!(asset)}]:[]),
        ...(asset.kind==='folder' ? [{label:'添加子文件夹',onSelect:()=>actions.onCreate('folder',asset.id)},{label:'添加子文件',onSelect:()=>actions.onCreate('file',asset.id)}] : []),
        {label:'上移',disabled:!canReorder || index===0,onSelect:()=>void move(asset.id,siblings[index-1].id)},
        {label:'下移',disabled:!canReorder || index===siblings.length-1,onSelect:()=>void move(asset.id,siblings[index+1].id)},
        {label:'删除',danger:true,onSelect:()=>actions.onAssetDelete(asset)},
      ]}/></td></tr>;
    })}</tbody></table>}</div>}
  </>;
}
