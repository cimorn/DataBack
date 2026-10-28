import type { Asset, Page } from './types.ts';

export interface Route { page: Page; folderId: string | null }
const root: Route = { page: 'overview', folderId: null };
export function parseRoute(hash: string): Route {
  if (hash.startsWith('#/folder/')) {
    try { return {page: 'overview', folderId: decodeURIComponent(hash.slice(9)) || null}; }
    catch { return {...root}; }
  }
  const page = hash.slice(2);
  return page === 'history' ? { page, folderId: null } : {...root};
}
export function routeHash(route: Route): string {
  return route.page === 'overview' ? route.folderId ? `#/folder/${encodeURIComponent(route.folderId)}` : '#/' : `#/${route.page}`;
}
export function resolveRoute(route: Route, assets: Asset[]): Route {
  if (route.page === 'overview' && route.folderId && !assets.some(asset => asset.id === route.folderId && (asset.kind === 'folder' || asset.backup_target === true))) return {...root};
  return route;
}
