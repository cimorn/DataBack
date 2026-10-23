import type { LedgerState, Location } from './types.ts';

type RecordRequest = <T>(path: string, method?: string, body?: unknown) => Promise<T>;
const nameKey = (name: string) => name.trim().toLowerCase();

// Locations remain internal references so existing records and exports keep
// their IDs. Users enter the name directly instead of maintaining a catalog.
export function createLocationResolver(locations: readonly Location[], send: RecordRequest) {
  const known = new Map(locations.map(location => [nameKey(location.name), location]));
  return async (value: string): Promise<Location> => {
    const name = value.trim();
    if (!name) throw new Error('请填写备份位置。');
    if (name.length > 200) throw new Error('备份位置最多 200 个字符。');
    const key = nameKey(name);
    const existing = known.get(key);
    if (existing) return existing;
    let location: Location;
    try {
      location = await send<Location>('locations/resolve', 'POST', {name});
    } catch (reason) {
      // A concurrent creation or lost response may already have saved the name.
      let recovered: Location | undefined;
      try { recovered = (await send<LedgerState>('state')).locations.find(item => nameKey(item.name) === key); }
      catch { /* Keep the original write failure and the form's inputs. */ }
      if (!recovered) throw reason;
      location = recovered;
    }
    known.set(key, location);
    return location;
  };
}
