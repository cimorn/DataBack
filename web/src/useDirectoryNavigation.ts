import { useCallback, useEffect, useState } from 'react';
import { parseRoute, resolveRoute, routeHash } from './navigation';
import type { Route } from './navigation';
import type { LedgerState } from './types';

export function useDirectoryNavigation(state: LedgerState | null) {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  useEffect(() => {
    const changed = () => { setRoute(parseRoute(window.location.hash)); window.scrollTo({top: 0}); };
    window.addEventListener('hashchange', changed);
    return () => window.removeEventListener('hashchange', changed);
  }, []);
  const resolved = state ? resolveRoute(route, state.assets) : route;
  useEffect(() => {
    if (state && resolved.folderId !== route.folderId) {
      window.history.replaceState(null, '', routeHash(resolved));
      setRoute(resolved);
    }
  }, [state, route.folderId, resolved.folderId, resolved.page]);
  const navigate = useCallback((next: Route) => {
    const hash = routeHash(next);
    setRoute(next);
    if (window.location.hash !== hash) window.location.hash = hash;
    window.scrollTo({top: 0});
  }, []);
  return { route: resolved, navigate };
}
