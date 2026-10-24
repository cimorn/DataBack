import type { Asset, BackupRecord, LedgerState } from './types.ts';
import { latestBackupForAsset } from './selectors.ts';
import { buildHierarchy } from './hierarchy.ts';
import type { Hierarchy } from './hierarchy.ts';
export type DirectorySort = 'manual' | 'name-asc' | 'name-desc' | 'newest' | 'oldest' | 'notes-asc' | 'notes-desc';
export type DirectorySortColumn = 'name' | 'backup' | 'notes';
const columnSorts = {
  name: ['name-asc', 'name-desc'],
  backup: ['oldest', 'newest'],
  notes: ['notes-asc', 'notes-desc'],
} as const;
export function directoryColumnSort(mode: DirectorySort, column: DirectorySortColumn) {
  const [ascending, descending] = columnSorts[column];
  const direction = mode === ascending ? 'ascending' : mode === descending ? 'descending' : 'none';
  return {direction, next: mode === ascending ? descending : ascending} as const;
}
export function latestDirectoryBackup(state: LedgerState, assetId: string, tree: Hierarchy = buildHierarchy(state.assets)): BackupRecord | undefined {
  if (tree.isLeaf(assetId)) return latestBackupForAsset(state, assetId);
  let latest: BackupRecord | undefined;
  for (const child of tree.leafDescendants(assetId)) {
    if (child.backup_target !== true) continue;
    const record = latestBackupForAsset(state, child.id);
    if (record && (!latest || Date.parse(record.backed_up_at) > Date.parse(latest.backed_up_at))) latest = record;
  }
  return latest;
}
const byName = (a: Asset, b: Asset) => a.name.localeCompare(b.name, 'zh-CN') || a.id.localeCompare(b.id);
const compareNotes = new Intl.Collator('zh-CN', {numeric: true}).compare;
function portableTextOrder(left:string,right:string):number {
  const a=Array.from(left),b=Array.from(right);
  for(let index=0;index<Math.min(a.length,b.length);index++){
    const difference=a[index].codePointAt(0)!-b[index].codePointAt(0)!;
    if(difference)return difference;
  }
  return a.length-b.length;
}
export function sortDirectoryAssets(state: LedgerState, assets: Asset[], mode: DirectorySort): Asset[] {
  const times = new Map<string, number>();
  const notes = new Map<string, string>();
  if (mode === 'newest' || mode === 'oldest' || mode === 'notes-asc' || mode === 'notes-desc') {
    const tree = buildHierarchy(state.assets);
    for (const asset of assets) {
      if (mode === 'notes-asc' || mode === 'notes-desc') {
        notes.set(asset.id, asset.backup_target === true && tree.isLeaf(asset.id) ? latestBackupForAsset(state, asset.id)?.notes?.trim() ?? '' : '');
      } else {
        const latest = latestDirectoryBackup(state, asset.id, tree);
        times.set(asset.id, latest ? Date.parse(latest.backed_up_at) : -Infinity);
      }
    }
  }
  return [...assets].sort((a, b) => {
    if (mode === 'manual') {
      const difference = (a.manual_order ?? Infinity) - (b.manual_order ?? Infinity);
      if (difference && !Number.isNaN(difference)) return difference;
      if (a.kind !== b.kind) return a.kind === 'folder' ? -1 : 1;
      return portableTextOrder(a.name.toLowerCase(),b.name.toLowerCase())||portableTextOrder(a.id,b.id);
    }
    if (mode === 'name-desc') return byName(b,a);
    if (mode === 'name-asc') return byName(a,b);
    if (mode === 'notes-asc' || mode === 'notes-desc') {
      const left = notes.get(a.id)!, right = notes.get(b.id)!;
      if (!left || !right) return Number(!left) - Number(!right) || byName(a,b);
      return (mode === 'notes-asc' ? compareNotes(left,right) : compareNotes(right,left)) || byName(a,b);
    }
    const difference = mode === 'newest' ? times.get(b.id)! - times.get(a.id)! : times.get(a.id)! - times.get(b.id)!;
    return difference || byName(a,b);
  });
}
export function moveDirectoryAsset(ids: string[], source: string, target: string): string[] | null {
  if (source === target || !ids.includes(source) || !ids.includes(target)) return null;
  const from=ids.indexOf(source), to=ids.indexOf(target);
  const next=[...ids]; next.splice(from,1); next.splice(to,0,source); return next;
}

export interface DirectoryDrag {
  sourceId: string;
  parentId: string | null;
  ids: string[];
  x: number;
  y: number;
  active: boolean;
  targetId: string | null;
}
export function startDirectoryDrag(sourceId: string, parentId: string | null, ids: string[], x: number, y: number): DirectoryDrag | null {
  if (!ids.includes(sourceId) || new Set(ids).size !== ids.length) return null;
  return {sourceId, parentId, ids: [...ids], x, y, active: false, targetId: null};
}
function matchingDrag(drag: DirectoryDrag, ids: string[], parentId: string | null): boolean {
  return drag.parentId === parentId && ids.length === drag.ids.length && ids.every((id, index) => drag.ids[index] === id);
}
export function advanceDirectoryDrag(drag: DirectoryDrag | null, ids: string[], parentId: string | null, x: number, y: number, targetId: string | null): DirectoryDrag | null {
  if (!drag || !matchingDrag(drag, ids, parentId)) return null;
  const active = drag.active || Math.hypot(x - drag.x, y - drag.y) >= 6;
  return {...drag, active, targetId: active && targetId && ids.includes(targetId) ? targetId : null};
}
export function finishDirectoryDrag(drag: DirectoryDrag | null, ids: string[], parentId: string | null): string[] | null {
  if (!drag?.active || !drag.targetId || !matchingDrag(drag, ids, parentId)) return null;
  return moveDirectoryAsset(ids, drag.sourceId, drag.targetId);
}
