import type { Asset } from './types.ts';
import { displayPath, pathForSave } from './pathDisplay.ts';

export interface AssetDraft {
  name: string; kind: Asset['kind']; source_path: string; notes: string;
  parent_id: string; backup_interval_days: string; target_location_ids: string[]; backup_target: boolean; backup_enabled: boolean;
  original_source_path?: string;
}
export function createAssetDraft(entry?: Asset, initialKind: Asset['kind'] = 'folder', parentId: string | null = null, initialBackupTarget?: boolean): AssetDraft {
  return {name: entry?.name ?? '', kind: entry?.kind ?? initialKind, source_path: displayPath(entry?.source_path ?? ''), original_source_path: entry?.source_path, notes: entry?.notes ?? '', parent_id: entry?.parent_id ?? parentId ?? '', backup_interval_days: String(entry?.backup_interval_days ?? 30), target_location_ids: [...(entry?.target_location_ids ?? [])], backup_target: initialBackupTarget ?? entry?.backup_target === true, backup_enabled: entry?.backup_enabled !== false};
}
export function assetPayload(draft: AssetDraft) {
  const interval = Number(draft.backup_interval_days);
  if (!Number.isInteger(interval) || interval < 1 || interval > 3650) throw new Error('备份间隔须为 1 至 3650 天的整数。');
  return { name: draft.name.trim(), kind: draft.kind, source_path: pathForSave(draft.source_path, draft.original_source_path), notes: draft.notes.trim(), parent_id: draft.parent_id || null, backup_interval_days: interval, target_location_ids: [...new Set(draft.target_location_ids)], backup_target: draft.backup_target === true, backup_enabled: draft.backup_enabled !== false };
}
