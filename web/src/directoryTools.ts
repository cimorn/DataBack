import type { Asset } from './types.ts';
import { buildHierarchy } from './hierarchy.ts';

export type DirectorySearchScope = 'current' | 'all';
export interface DirectoryBatchRow { path: string; kind: Asset['kind']; status: 'create' | 'skip' }
export interface DirectoryBatchPreview {
  paths: string[]; rows: DirectoryBatchRow[]; created: number; skipped: number; valid: boolean; error: string;
}

const nameKey = (value: string) => value.toLowerCase().replaceAll('ß', 'ss').replaceAll('ς', 'σ');

export function previewDirectoryBatch(assets: Asset[], parentId: string | null, kind: Asset['kind'], input: string): DirectoryBatchPreview {
  const preview: DirectoryBatchPreview = {paths: [], rows: [], created: 0, skipped: 0, valid: false, error: ''};
  const fail = (message: string) => ({...preview, error: message});
  if (parentId && !assets.some(asset => asset.id === parentId && asset.kind === 'folder')) return fail('所属目录不存在或不是文件夹。');
  const lines = input.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  if (!lines.length) return preview;
  if (lines.length > 200) return fail('一次最多添加 200 行。');
  for (const [index, line] of lines.entries()) {
    if (/[\\:\u0000-\u001f\u007f]/.test(line)) return fail(`第 ${index + 1} 行：请填写以 / 分隔的相对路径。`);
    const parts = line.split('/').map(part => part.trim());
    if (parts.some(part => !part || part === '.' || part === '..')) return fail(`第 ${index + 1} 行：路径不能包含空目录、. 或 ..。`);
    if (parts.some(part => [...part].length > 200)) return fail(`第 ${index + 1} 行：每个名称最多 200 个字符。`);
    preview.paths.push(parts.join('/'));
  }
  type Node = Pick<Asset, 'id' | 'kind'>;
  const children = new Map<string | null, Map<string, Node[]>>();
  const put = (parent: string | null, name: string, node: Node) => {
    if (!children.has(parent)) children.set(parent, new Map());
    const branch = children.get(parent)!;
    const key = nameKey(name);
    branch.set(key, [...(branch.get(key) ?? []), node]);
  };
  for (const asset of assets) put(asset.parent_id ?? null, asset.name, asset);
  for (const path of preview.paths) {
    let parent = parentId;
    const parts = path.split('/');
    for (const [index, part] of parts.entries()) {
      const leaf = index === parts.length - 1;
      const expectedKind = leaf ? kind : 'folder';
      const found = children.get(parent)?.get(nameKey(part)) ?? [];
      const partialPath = parts.slice(0, index + 1).join('/');
      if (found.length > 1) return fail(`${partialPath} 存在同名冲突，请先整理该目录。`);
      if (found.length) {
        if (found[0].kind !== expectedKind) return fail(`${partialPath} 与现有${found[0].kind === 'file' ? '文件' : '文件夹'}类型冲突。`);
        parent = found[0].id;
        if (leaf) { preview.skipped++; preview.rows.push({path, kind, status: 'skip'}); }
        continue;
      }
      if (preview.created >= 500) return fail('展开后的新增目录和文件不能超过 500 项。');
      const node = {id: `\u0000preview-${preview.created++}`, kind: expectedKind};
      put(parent, part, node);
      parent = node.id;
      preview.rows.push({path: partialPath, kind: expectedKind, status: 'create'});
    }
  }
  return {...preview, valid: true};
}

export function searchDirectoryAssets(assets: Asset[], parentId: string | null, keyword: string, scope: DirectorySearchScope): Asset[] {
  const tree = buildHierarchy(assets);
  const needle = keyword.trim().toLocaleLowerCase();
  if (scope === 'all' && needle) return assets.filter(asset => tree.path(asset.id).toLocaleLowerCase().includes(needle));
  return tree.children(parentId).filter(asset => !needle || asset.name.toLocaleLowerCase().includes(needle));
}

export function directoryNavigation(asset: Asset, marked: boolean, globalSearch: boolean): {folderId?: string | null; detailId?: string} {
  if (asset.kind === 'folder' || marked) return {folderId: asset.id};
  return globalSearch ? {folderId: asset.parent_id ?? null, detailId: asset.id} : {detailId: asset.id};
}
