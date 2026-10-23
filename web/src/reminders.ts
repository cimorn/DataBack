import type { Asset, BackupRecord, LedgerState } from './types.ts';
import { buildHierarchy } from './hierarchy.ts';
import type { Hierarchy } from './hierarchy.ts';
import { latestBackupForAsset, latestRecordsForAsset } from './selectors.ts';

export type ReminderStatus = 'ordinary' | 'never' | 'due' | 'current' | 'disabled';
export interface TargetReminder {
  locationId: string | null;
  latest?: BackupRecord;
  deadline: number | null;
  status: ReminderStatus;
}
export function targetReminder(latest: BackupRecord | undefined, intervalDays: number, now: number, locationId: string | null = null): TargetReminder {
  if (!latest) return { locationId, latest, deadline: null, status: 'never' };
  const deadline = Date.parse(latest.backed_up_at) + intervalDays * 86400000;
  return { locationId, latest, deadline, status: deadline <= now ? 'due' : 'current' };
}
export function backupTargets(state: LedgerState, tree: Hierarchy = buildHierarchy(state.assets)): Asset[] {
  return state.assets.filter(asset => asset.backup_target === true && tree.isLeaf(asset.id));
}
export function leafReminder(state: LedgerState, asset: Asset, now: number, tree: Hierarchy = buildHierarchy(state.assets)) {
  const latest = latestBackupForAsset(state, asset.id);
  const locations = latestRecordsForAsset(state, asset.id);
  if (asset.backup_target !== true || !tree.isLeaf(asset.id)) return {asset, latest, locations, targets: [] as TargetReminder[], status: 'ordinary' as const, nextDue: null, actionable: false};
  const interval = asset.backup_interval_days ?? 30;
  const ids = asset.target_location_ids ?? [];
  const targets = ids.length ? ids.map(id => targetReminder(locations.get(id), interval, now, id)) : [targetReminder(latest, interval, now)];
  const overall = targetReminder(latest, interval, now);
  const status: ReminderStatus = overall.status;
  const nextDue = overall.deadline;
  return { asset, latest, locations, targets, status, nextDue, actionable: status === 'never' || status === 'due' };
}
export type LeafReminder = ReturnType<typeof leafReminder>;
export function dueLeaves(state: LedgerState, now: number): LeafReminder[] {
  const tree = buildHierarchy(state.assets);
  return backupTargets(state, tree).map(asset => leafReminder(state, asset, now, tree)).filter(item => item.actionable).sort((a, b) => {
    if (a.status !== b.status) return a.status === 'never' ? -1 : 1;
    return (a.nextDue ?? 0) - (b.nextDue ?? 0) || tree.path(a.asset.id).localeCompare(tree.path(b.asset.id), 'zh-CN');
  });
}
export function folderRollup(state: LedgerState, tree: Hierarchy, id: string, now: number) {
  const leaves = tree.leafDescendants(id).filter(asset => asset.backup_target === true).map(asset => leafReminder(state, asset, now, tree));
  let latest: BackupRecord | undefined;
  for (const leaf of leaves) if (leaf.latest && (!latest || Date.parse(leaf.latest.backed_up_at) > Date.parse(latest.backed_up_at))) latest = leaf.latest;
  return { leafCount: leaves.length, activeCount: leaves.filter(leaf => leaf.status !== 'disabled').length, inactiveCount: leaves.filter(leaf => leaf.status === 'disabled').length, actionableCount: leaves.filter(leaf => leaf.actionable).length, latest };
}
