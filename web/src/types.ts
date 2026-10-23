export interface Asset {
  id: string;
  name: string;
  kind: 'file' | 'folder';
  source_path: string;
  parent_id: string | null;
  manual_order?: number;
  backup_interval_days: number;
  backup_target: boolean;
  private?: boolean;
  backup_enabled: boolean;
  target_location_ids: string[];
  notes: string;
  created_at: string;
}
export interface Version {
  id: string;
  asset_id: string;
  label: string;
  notes: string;
  created_at: string;
}
export interface Location {
  id: string;
  name: string;
  kind: 'local' | 'external' | 'cloud' | 'nas' | 'other';
  base_path: string;
  notes: string;
  created_at: string;
}
export interface BackupRecord {
  id: string;
  version_id: string;
  location_id: string;
  backed_up_at: string;
  stored_path: string;
  notes: string;
  created_at: string;
}
export interface LedgerState {
  assets: Asset[];
  versions: Version[];
  locations: Location[];
  records: BackupRecord[];
}
export type Collection = keyof LedgerState;
export interface RecordFilters {
  keyword?: string;
  assetId?: string;
  includeDescendants?: boolean;
  locationId?: string;
  dateFrom?: string;
  dateTo?: string;
}
export type Page = 'overview' | 'history' | 'data';
export interface SessionInfo {
  authenticated: boolean;
  auth_required: boolean;
  storage: 'sqlite' | 'mongodb';
}
