import type { BackupRecord, LedgerState, RecordFilters, Version } from './types.ts';
import { buildHierarchy } from './hierarchy.ts';

export function latestRecordsForAsset(state: LedgerState, assetId: string): Map<string, BackupRecord> {
  const versions = new Set(state.versions.filter(version => version.asset_id === assetId).map(version => version.id));
  const latest = new Map<string, BackupRecord>();
  for (const record of state.records) {
    if (!versions.has(record.version_id)) continue;
    const previous = latest.get(record.location_id);
    if (!previous || Date.parse(record.backed_up_at) >= Date.parse(previous.backed_up_at)) latest.set(record.location_id, record);
  }
  return latest;
}
export function latestBackupForAsset(state: LedgerState, assetId: string): BackupRecord | undefined {
  let latest: BackupRecord | undefined;
  for (const record of latestRecordsForAsset(state, assetId).values()) {
    if (!latest || Date.parse(record.backed_up_at) > Date.parse(latest.backed_up_at)) latest = record;
  }
  return latest;
}

export function latestVersion(versions: Version[], assetId: string): Version | undefined {
  let latest: Version | undefined;
  for (const version of versions) {
    if (version.asset_id === assetId && (!latest || Date.parse(version.created_at) >= Date.parse(latest.created_at))) latest = version;
  }
  return latest;
}
export function latestRecordsByLocation(records: BackupRecord[], versionId: string): Map<string, BackupRecord> {
  const latest = new Map<string, BackupRecord>();
  for (const record of records) {
    if (record.version_id !== versionId) continue;
    const previous = latest.get(record.location_id);
    if (!previous || Date.parse(record.backed_up_at) >= Date.parse(previous.backed_up_at)) latest.set(record.location_id, record);
  }
  return latest;
}
export function filterRecords(state: LedgerState, filters: RecordFilters): BackupRecord[] {
  const versions = new Map(state.versions.map(version => [version.id, version]));
  const assets = new Map(state.assets.map(asset => [asset.id, asset]));
  const locations = new Map(state.locations.map(location => [location.id, location]));
  const assetIds = filters.assetId ? new Set([filters.assetId, ...(filters.includeDescendants ? buildHierarchy(state.assets).descendants(filters.assetId).map(asset => asset.id) : [])]) : undefined;
  const keyword = filters.keyword?.trim().toLocaleLowerCase();
  const from = filters.dateFrom ? new Date(`${filters.dateFrom}T00:00:00`).getTime() : -Infinity;
  const until = filters.dateTo ? new Date(`${filters.dateTo}T00:00:00`) : undefined;
  // Advancing the local calendar day also handles days with a daylight-saving transition.
  if (until) until.setDate(until.getDate() + 1);
  const end = until?.getTime() ?? Infinity;
  return state.records.filter(record => {
    const version = versions.get(record.version_id);
    const asset = version ? assets.get(version.asset_id) : undefined;
    const location = locations.get(record.location_id);
    const time = Date.parse(record.backed_up_at);
    if (assetIds && (!version || !assetIds.has(version.asset_id))) return false;
    if (filters.locationId && record.location_id !== filters.locationId) return false;
    if (time < from || time >= end) return false;
    if (keyword && ![asset?.name, location?.name, record.notes].join(' ').toLocaleLowerCase().includes(keyword)) return false;
    return true;
  }).sort((a, b) => Date.parse(b.backed_up_at) - Date.parse(a.backed_up_at));
}
export function toDatetimeLocal(iso?: string): string {
  const date = iso ? new Date(iso) : new Date();
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function toBackupTimestamp(local: string, previousIso?: string): string {
  if (previousIso && toDatetimeLocal(previousIso) === local) return previousIso;
  const date = new Date(local);
  if (Number.isNaN(date.getTime())) throw new Error('请填写有效的备份时间。');
  return date.toISOString();
}

export function formatTime(iso?: string): string {
  if (!iso) return '未登记';
  const date = new Date(iso);
  return new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date).replaceAll('/', '-');
}
