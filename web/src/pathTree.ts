import type { Asset, BackupRecord } from './types';

export interface AssetSummary { asset: Asset; recent?: BackupRecord; locations: Map<string, BackupRecord> }
export interface PathTreeNode { label: string; path: string; children: Map<string, PathTreeNode>; assets: AssetSummary[]; count: number; hasRecords: boolean }
export function buildRegisteredTree(summaries: AssetSummary[]): PathTreeNode {
  const root: PathTreeNode = { label: '', path: '', children: new Map(), assets: [], count: 0, hasRecords: false };
  for (const summary of summaries) {
    const parts = summary.asset.source_path.trim().replaceAll('\\', '/').split('/').filter(Boolean);
    root.count++;
    let current = root;
    if (!parts.length) {
      let unassigned = root.children.get('unassigned');
      if (!unassigned) {
        unassigned = { label: '未设置路径', path: 'unassigned', children: new Map(), assets: [], count: 0, hasRecords: false };
        root.children.set('unassigned', unassigned);
      }
      current = unassigned; current.count++; current.hasRecords ||= summary.locations.size > 0;
    } else {
      for (const part of parts) {
        const key = part.toLocaleLowerCase();
        let child = current.children.get(key);
        if (!child) { child = { label: part, path: `${current.path}/${part}`, children: new Map(), assets: [], count: 0, hasRecords: false }; current.children.set(key, child); }
        current = child; current.count++; current.hasRecords ||= summary.locations.size > 0;
      }
    }
    current.assets.push(summary);
  }
  return root;
}
