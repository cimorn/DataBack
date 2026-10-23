import type { Asset, LedgerState } from './types.ts';
import { latestBackupForAsset } from './selectors.ts';
export type DirectorySort = 'manual' | 'name-asc' | 'name-desc' | 'newest' | 'oldest';
const byName = (a: Asset, b: Asset) => a.name.localeCompare(b.name, 'zh-CN') || a.id.localeCompare(b.id);
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
  if (mode === 'newest' || mode === 'oldest') for (const asset of assets) {
    const latest = latestBackupForAsset(state, asset.id);
    times.set(asset.id, latest ? Date.parse(latest.backed_up_at) : -Infinity);
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
