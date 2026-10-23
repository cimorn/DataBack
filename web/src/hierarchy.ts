import type { Asset } from './types.ts';

export function buildHierarchy(assets: Asset[]) {
  const byId = new Map(assets.map(asset => [asset.id, asset]));
  const byParent = new Map<string | null, Asset[]>();
  for (const asset of assets) {
    const parent = asset.parent_id ?? null;
    const entries = byParent.get(parent) ?? [];
    entries.push(asset);
    byParent.set(parent, entries);
  }
  const children = (parentId: string | null) => byParent.get(parentId) ?? [];
  const isLeaf = (id: string) => !children(id).length;
  function ancestors(id: string) {
    const result: Asset[] = [];
    const visited = new Set([id]);
    let parentId = byId.get(id)?.parent_id;
    while (parentId && !visited.has(parentId)) {
      visited.add(parentId);
      const parent = byId.get(parentId);
      if (!parent) break;
      result.unshift(parent);
      parentId = parent.parent_id;
    }
    return result;
  }
  function descendants(id: string): Asset[] {
    const result: Asset[] = [];
    const visited = new Set([id]);
    const pending = [...children(id)].reverse();
    while (pending.length) {
      const asset = pending.pop()!;
      if (visited.has(asset.id)) continue;
      visited.add(asset.id);
      result.push(asset);
      pending.push(...[...children(asset.id)].reverse());
    }
    return result;
  }
  const path = (id: string) => [...ancestors(id), ...(byId.has(id) ? [byId.get(id)!] : [])].map(asset => asset.name).join('/');
  const leafDescendants = (id: string) => descendants(id).filter(asset => isLeaf(asset.id));
  function moveOptions(id?: string) {
    const excluded = new Set(id ? [id, ...descendants(id).map(asset => asset.id)] : []);
    return assets.filter(asset => asset.kind === 'folder' && !excluded.has(asset.id));
  }
  return { byId, children, isLeaf, ancestors, descendants, leafDescendants, path, moveOptions };
}
export type Hierarchy = ReturnType<typeof buildHierarchy>;
