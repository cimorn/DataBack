// A successful write must not be repeated merely because the following refresh failed.
export function createRefreshSafeAction<T>(mutate: () => Promise<T>) {
  let committed = false;
  let result: T;
  let pending: Promise<T> | undefined;
  return {
    committed: () => committed,
    run(refresh: () => Promise<void>): Promise<T> {
      if (pending) return pending;
      pending = (async () => {
        if (!committed) { result = await mutate(); committed = true; }
        await refresh();
        return result;
      })().finally(() => { pending = undefined; });
      return pending;
    },
  };
}
