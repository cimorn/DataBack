import type { LedgerState } from './types';
export interface ExportData extends LedgerState { format: string; schema_version: number; exported_at: string }
export interface ImportFile { size: number; text: () => Promise<string> }
export function createLatestImportReader() {
  let selectionId = 0;
  return async (file: ImportFile): Promise<ExportData | undefined> => {
    const currentId = ++selectionId;
    try {
      if (file.size > 10 * 1024 * 1024) throw new Error('文件超过 10 MiB，请选择更小的台账 JSON 文件。');
      const text = await file.text();
      if (currentId !== selectionId) return undefined;
      return parseImportPreview(text);
    } catch (reason) {
      if (currentId !== selectionId) return undefined;
      throw reason;
    }
  };
}
export function parseImportPreview(text: string): ExportData {
  let data: unknown;
  try { data = JSON.parse(text); }
  catch { throw new Error('JSON 内容无法解析，尚未写入任何数据。'); }
  if (!data || typeof data !== 'object') throw new Error('文件不是支持的备份台账格式。请选择本应用导出的 JSON。');
  const candidate = data as ExportData;
  if (candidate.format !== 'filebackup-ledger' || ![1, 2, 3].includes(candidate.schema_version) || !['assets', 'versions', 'locations', 'records'].every(key => Array.isArray(candidate[key as keyof LedgerState]))) throw new Error('文件不是支持的备份台账格式。请选择本应用导出的 JSON。');
  for (const asset of candidate.assets) {
    if (asset && Object.hasOwn(asset, 'manual_order') && (!Number.isInteger(asset.manual_order) || typeof asset.manual_order !== 'number' || asset.manual_order < 0 || asset.manual_order > 2147483647)) throw new Error('手动排序须为 0 到 2147483647 之间的整数。');
  }
  return candidate;
}
