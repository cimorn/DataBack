import { useEffect, useSyncExternalStore } from 'react';
import { sessionController } from './sessionClient';

export function useSession() {
  const snapshot = useSyncExternalStore(sessionController.subscribe, sessionController.getSnapshot, sessionController.getSnapshot);
  useEffect(() => { void sessionController.bootstrap(); }, []);
  return {snapshot, login: sessionController.login, logout: sessionController.logout, retry: sessionController.bootstrap};
}
