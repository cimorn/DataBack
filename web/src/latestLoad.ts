export interface LoadCallbacks<T> { start: () => void; success: (data: T) => void; failure: (reason: unknown) => void; settled: () => void }

export function createLatestLoader<T>(read: () => Promise<T>, callbacks: LoadCallbacks<T>) {
  let latestGeneration = 0;
  return async ({throwOnError = false}: {throwOnError?: boolean} = {}) => {
    const generation = ++latestGeneration;
    callbacks.start();
    try {
      const data = await read();
      if (generation === latestGeneration) callbacks.success(data);
    }
    catch (reason) {
      if (generation === latestGeneration) callbacks.failure(reason);
      if (throwOnError) throw reason;
    }
    finally { if (generation === latestGeneration) callbacks.settled(); }
  };
}
