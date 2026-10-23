import type { Location } from './types.ts';
import { displayPath } from './pathDisplay.ts';

export interface DestinationDraft { selected: boolean; time: string; path: string; notes: string }
export type DestinationDrafts = Record<string, DestinationDraft>;

// Reconcile before consuming a refreshed list, including the render before effects run.
// Existing objects retain all user edits; only added/removed locations change the map.
export function reconcileDestinations(current: DestinationDrafts, locations: Pick<Location, 'id' | 'base_path'>[], initialTargetIds: readonly string[], defaultTime: string): DestinationDrafts {
  let changed = Object.keys(current).length !== locations.length;
  const entries = locations.map(location => {
    const previous = current[location.id];
    if (previous) return [location.id, previous] as const;
    changed = true;
    return [location.id, {selected: initialTargetIds.includes(location.id), time: defaultTime, path: displayPath(location.base_path), notes: ''}] as const;
  });
  return changed ? Object.fromEntries(entries) : current;
}
