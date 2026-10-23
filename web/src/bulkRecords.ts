import { buildHierarchy } from './hierarchy.ts';
import { MAX_RECORDS_PER_BATCH, notesForSave } from './inputLimits.ts';
import { createLocationResolver } from './recordLocations.ts';
import { toBackupTimestamp } from './selectors.ts';
import type { Asset, LedgerState, Version } from './types.ts';

export interface BulkSharedFields { time: string; location: string; notes: string }
export interface BulkInputRow extends BulkSharedFields { assetId?: string; path?: string; rowNumber?: number }
export interface BulkPreviewRow extends BulkSharedFields {
  assetId: string | null; path: string; rowNumber: number; backedUpAt: string | null; error: string;
}
export interface BulkPreview { rows: BulkPreviewRow[]; errors: string[] }
type BulkRequest = <T>(path: string, method?: string, body?: unknown) => Promise<T>;

export function collectBulkTargets(state: LedgerState, selectedIds: readonly string[]): Asset[] {
  const tree = buildHierarchy(state.assets);
  const selected = new Set<string>();
  for (const id of selectedIds) {
    if (!tree.byId.has(id)) continue;
    selected.add(id);
    for (const child of tree.descendants(id)) selected.add(child.id);
  }
  return state.assets.filter(asset => selected.has(asset.id) && asset.backup_target === true && tree.isLeaf(asset.id));
}

export function createBulkSelectionRows(state: LedgerState, selectedIds: readonly string[], shared: BulkSharedFields): BulkInputRow[] {
  const tree = buildHierarchy(state.assets);
  return collectBulkTargets(state, selectedIds).map(asset => ({assetId: asset.id, path: tree.path(asset.id), ...shared}));
}

function tableDelimiter(text: string): '\t' | ',' {
  let quoted = false, tabs = 0, content = false;
  for (let index = 0; index < text.length; index++) {
    const char = text[index];
    if (char === '"') {
      if (quoted && text[index + 1] === '"') index++;
      else quoted = !quoted;
    } else if (!quoted && char === '\t') tabs++;
    else if (!quoted && (char === '\n' || char === '\r')) {
      if (content) return tabs ? '\t' : ',';
      tabs = 0;
    } else if (char.trim() && char !== ',') content = true;
  }
  return tabs ? '\t' : ',';
}

function readCells(text: string): {cells: string[]; line: number}[] {
  const delimiter = tableDelimiter(text);
  const result: {cells: string[]; line: number}[] = [];
  let cells: string[] = [], cell = '', quoted = false, closed = false, line = 1, rowLine = 1;
  const finishCell = () => { cells.push(cell); cell = ''; closed = false; };
  const finishRow = () => {
    finishCell();
    if (cells.some(value => value.trim())) result.push({cells, line: rowLine});
    cells = [];
  };
  for (let index = 0; index < text.length; index++) {
    const char = text[index];
    if (quoted) {
      if (char === '"') {
        if (text[index + 1] === '"') { cell += '"'; index++; }
        else { quoted = false; closed = true; }
      } else if (char === '\n' || char === '\r') {
        if (char === '\r' && text[index + 1] === '\n') index++;
        cell += '\n'; line++;
      } else cell += char;
      continue;
    }
    if (char === delimiter) finishCell();
    else if (char === '\n' || char === '\r') {
      if (char === '\r' && text[index + 1] === '\n') index++;
      finishRow(); line++; rowLine = line;
    } else if (char === '"') {
      if (cell || closed) throw new Error(`第 ${line} 行：表格引号格式有误。`);
      quoted = true;
    } else if (closed) {
      if (char !== ' ') throw new Error(`第 ${line} 行：表格引号后有多余内容。`);
    } else cell += char;
  }
  if (quoted) throw new Error(`第 ${rowLine} 行：表格引号未闭合。`);
  finishRow();
  return result;
}

const headerNames: Record<string, keyof BulkSharedFields | 'path'> = {
  '名称': 'path', '资料': 'path', '路径': 'path', '资料路径': 'path', '相对路径': 'path',
  '时间': 'time', '备份时间': 'time', '位置': 'location', '备份位置': 'location', '备注': 'notes',
};

export function parseBackupTable(value: string): BulkInputRow[] {
  const records = readCells(value.replace(/^\uFEFF/, ''));
  if (!records.length) return [];
  const first = records[0].cells.map(cell => headerNames[cell.trim()]);
  const hasHeader = first.includes('path') && first.includes('time');
  let columns: (keyof BulkSharedFields | 'path')[] = ['path', 'time', 'location', 'notes'];
  if (hasHeader) {
    if (first.some(column => !column) || new Set(first).size !== first.length || !first.includes('location')) {
      throw new Error('表头应包含路径、备份时间、备份位置和可选的备注列。');
    }
    columns = first;
    records.shift();
  }
  const errors: string[] = [];
  const rows = records.map(({cells, line}) => {
    if ((hasHeader && cells.length !== columns.length) || (!hasHeader && (cells.length < 3 || cells.length > 4))) {
      errors.push(`第 ${line} 行：请粘贴路径、备份时间、备份位置和可选的备注，共 3–4 列。`);
    }
    const row: BulkInputRow = {path: '', time: '', location: '', notes: '', rowNumber: line};
    for (let index = 0; index < Math.min(cells.length, columns.length); index++) row[columns[index]] = cells[index];
    return row;
  });
  if (errors.length) throw new Error(errors.join('\n'));
  return rows;
}

function relativePath(value: string): string {
  let path = value.trim().replaceAll('\\', '/');
  if (!path || path.startsWith('/') || /^[a-z]:/i.test(path) || /^[a-z][a-z\d+.-]*:\/\//i.test(path)) {
    throw new Error('请填写已登记资料的相对路径。');
  }
  while (path.startsWith('./')) path = path.slice(2);
  if (path.startsWith('/') || /^[a-z]:/i.test(path)) throw new Error('请填写已登记资料的相对路径。');
  path = path.replace(/\/+$/g, '').replace(/\/{2,}/g, '/');
  if (!path || path.split('/').some(part => part === '.' || part === '..')) throw new Error('请填写已登记资料的相对路径。');
  return path;
}

function backupTime(value: string): string {
  const text = value.trim();
  const parts = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?(Z|[+-]\d{2}:\d{2})?$/i.exec(text);
  if (!parts) throw new Error('请填写有效的备份时间（YYYY-MM-DD HH:mm）。');
  const [year, month, day, hour, minute, second] = [parts[1], parts[2], parts[3], parts[4], parts[5], parts[6] ?? '0'].map(Number);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (!year || month < 1 || month > 12 || day < 1 || day > days[month - 1] || hour > 23 || minute > 59 || second > 59) {
    throw new Error('请填写有效的备份时间。');
  }
  const normalized = text.replace(' ', 'T').replace(/z$/i, 'Z');
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime()) || (!parts[8] && (date.getFullYear() !== year || date.getMonth() + 1 !== month || date.getDate() !== day || date.getHours() !== hour || date.getMinutes() !== minute))) {
    throw new Error('请填写有效的备份时间。');
  }
  return toBackupTimestamp(normalized);
}

export function previewBulkRecords(state: LedgerState, input: readonly BulkInputRow[]): BulkPreview {
  const tree = buildHierarchy(state.assets);
  const paths = state.assets.map(asset => ({asset, path: tree.path(asset.id)}));
  const errors: string[] = [];
  if (!input.length) errors.push('请选择备份对象或粘贴备份记录。');
  if (input.length > MAX_RECORDS_PER_BATCH) errors.push(`单次最多登记 ${MAX_RECORDS_PER_BATCH} 条备份记录，当前共 ${input.length} 条。`);
  const seen = new Map<string, number>();
  const rows = input.map((draft, index): BulkPreviewRow => {
    const rowNumber = draft.rowNumber ?? index + 1;
    const problems: string[] = [];
    let asset: Asset | undefined, path = draft.path ?? '', backedUpAt: string | null = null, notes = draft.notes;
    try {
      if (draft.assetId) {
        asset = tree.byId.get(draft.assetId);
        if (!asset) throw new Error('资料已移除或不存在，请重新选择。');
      } else {
        path = relativePath(path);
        const full = paths.filter(item => item.path === path);
        const matches = full.length ? full : paths.filter(item => item.path.endsWith(`/${path}`));
        if (!matches.length) throw new Error('未找到已登记的资料路径。');
        if (matches.length > 1) throw new Error('存在多个重名资料，请填写完整相对路径。');
        asset = matches[0].asset;
      }
      path = tree.path(asset.id);
      if (!tree.isLeaf(asset.id)) throw new Error('只有末级文件或文件夹可以登记备份。');
      if (asset.backup_target !== true) throw new Error('请先将这项资料设为备份对象。');
    } catch (reason) { problems.push(reason instanceof Error ? reason.message : '资料路径有误。'); }
    try { backedUpAt = backupTime(draft.time); }
    catch (reason) { problems.push(reason instanceof Error ? reason.message : '备份时间有误。'); }
    const location = draft.location.trim();
    if (!location) problems.push('请填写备份位置。');
    else if (location.length > 200) problems.push('备份位置最多 200 个字符。');
    try { notes = notesForSave(notes); }
    catch (reason) { problems.push(reason instanceof Error ? reason.message : '备注有误。'); }
    if (!problems.length && asset && backedUpAt) {
      const key = JSON.stringify([asset.id, backedUpAt, location.toLowerCase(), notes]);
      const previous = seen.get(key);
      if (previous !== undefined) problems.push(`与第 ${previous} 行的记录重复，请删除重复行。`);
      else seen.set(key, rowNumber);
    }
    const error = problems.length ? `第 ${rowNumber} 行：${problems.join(' ')}` : '';
    if (error) errors.push(error);
    return {assetId: asset?.id ?? null, path, rowNumber, time: draft.time, location, notes, backedUpAt, error};
  });
  return {rows, errors};
}

export function createBulkSubmission(send: BulkRequest) {
  let committed = false, pending = false;
  return {
    get committed() { return committed; },
    get pending() { return pending; },
    async submit(input: readonly BulkInputRow[], onSaved: () => Promise<void>): Promise<boolean> {
      if (pending) return false;
      pending = true;
      try {
        if (!committed) {
          const fresh = await send<LedgerState>('state');
          const preview = previewBulkRecords(fresh, input);
          if (preview.errors.length) throw new Error(preview.errors.join('\n'));
          const versions = new Map(fresh.versions.filter(version => version.label === '未标注').map(version => [version.asset_id, version]));
          const resolveLocation = createLocationResolver(fresh.locations, send);
          const records = [];
          for (const row of preview.rows) {
            const assetId = row.assetId!;
            let version = versions.get(assetId);
            if (!version) {
              try { version = await send<Version>('versions', 'POST', {asset_id: assetId, label: '未标注', notes: ''}); }
              catch (reason) {
                let recovered: Version | undefined;
                try { recovered = (await send<LedgerState>('state')).versions.find(item => item.asset_id === assetId && item.label === '未标注'); }
                catch { /* Preserve the original failure for retry. */ }
                if (!recovered) throw reason;
                version = recovered;
              }
              versions.set(assetId, version);
            }
            const location = await resolveLocation(row.location);
            records.push({version_id: version.id, location_id: location.id, backed_up_at: row.backedUpAt!, notes: row.notes});
          }
          await send('records/batch', 'POST', {records});
          committed = true;
        }
        await onSaved();
        return true;
      } finally { pending = false; }
    },
  };
}
