export function createVisibilityFence() {
  let generation = 0;
  let privacyToken = '';
  return {
    invalidate() { generation++; privacyToken=''; },
    setPrivacyToken(token:string) { privacyToken=token; },
    privacyToken:()=>privacyToken,
    async guard<T>(read: () => Promise<T>): Promise<T> {
      const current = generation;
      try {
        const result = await read();
        if (current !== generation) throw new Error('资料视图已更新，请重试。');
        return result;
      } catch (reason) {
        if (current !== generation) throw new Error('资料视图已更新，请重试。');
        throw reason;
      }
    },
  };
}

export const visibilityFence = createVisibilityFence();
