import {latestBackupForAsset, toBackupTimestamp, toDatetimeLocal} from './selectors';
import {notesForSave} from './inputLimits';
import type {LedgerState} from './types';

export interface RecordDraft {time: string; location: string; notes: string}
export interface RecordChangeFields {time: boolean; location: boolean; notes: boolean}
export interface BulkRecordChanges {backed_up_at?: string; location_name?: string; notes?: string}

export function reuseLatestRecordDraft(state: LedgerState, assetId: string, time = toDatetimeLocal()): RecordDraft | undefined {
  const latest = latestBackupForAsset(state, assetId);
  if (!latest) return undefined;
  return {time, location: state.locations.find(location => location.id === latest.location_id)?.name ?? '', notes: latest.notes};
}

export function buildBulkRecordChanges(fields: RecordChangeFields, draft: RecordDraft): BulkRecordChanges {
  if (!fields.time && !fields.location && !fields.notes) throw new Error('请选择要修改的字段。');
  const changes: BulkRecordChanges = {};
  if (fields.time) changes.backed_up_at = toBackupTimestamp(draft.time);
  if (fields.location) {
    const name = draft.location.trim();
    if (!name) throw new Error('请填写备份位置。');
    if (name.length > 200) throw new Error('备份位置最多 200 字。');
    changes.location_name = name;
  }
  if (fields.notes) changes.notes = notesForSave(draft.notes);
  return changes;
}
