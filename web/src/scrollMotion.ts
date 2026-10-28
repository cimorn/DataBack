const revealSelector = [
  '.page-heading',
  '.directory-toolbar',
  '.history-filters',
  '.detail-heading',
  '.empty-state',
  '.explorer-table tbody > tr',
  '.history-table tbody > tr',
  '.record-detail-table tbody > tr',
].join(', ');

/** Adds optional motion without changing the document's default visibility. */
export function mountScrollMotion(content: HTMLElement, background: HTMLElement): () => void {
  const win = content.ownerDocument.defaultView;
  if (!win || typeof win.matchMedia !== 'function'
    || typeof win.IntersectionObserver !== 'function'
    || typeof win.MutationObserver !== 'function'
    || typeof content.animate !== 'function'
    || typeof win.requestAnimationFrame !== 'function'
    || typeof win.cancelAnimationFrame !== 'function') return () => {};

  const preference = win.matchMedia('(prefers-reduced-motion: reduce)');
  if (typeof preference.addEventListener !== 'function'
    || typeof preference.removeEventListener !== 'function') return () => {};
  const played = new WeakSet<Element>();
  const observed = new Set<HTMLElement>();
  const animations = new Map<HTMLElement, Animation>();
  const heldPointers = new Map<HTMLElement, number>();
  let intersection: IntersectionObserver | null = null;
  let mutation: MutationObserver | null = null;
  let frame: number | null = null;
  let scrolling = false;
  let disposed = false;

  function clearBackground() {
    if (frame !== null) win!.cancelAnimationFrame(frame);
    frame = null;
    background.style.removeProperty('--parallax-near');
    background.style.removeProperty('--parallax-far');
  }

  function scheduleBackground() {
    if (disposed || preference.matches || !scrolling || frame !== null) return;
    frame = win!.requestAnimationFrame(() => {
      frame = null;
      if (disposed || preference.matches || !scrolling) return;
      const distance = Math.max(win!.scrollY, 0);
      background.style.setProperty('--parallax-near', `${Math.min(distance * .08, 120)}px`);
      background.style.setProperty('--parallax-far', `${Math.min(distance * .035, 56)}px`);
    });
  }

  function resize() {
    const desktop = win!.innerWidth >= 1001;
    if (desktop && !scrolling) {
      scrolling = true;
      win!.addEventListener('scroll', scheduleBackground, { passive: true });
    } else if (!desktop && scrolling) {
      scrolling = false;
      win!.removeEventListener('scroll', scheduleBackground);
      clearBackground();
    }
    if (desktop) scheduleBackground();
  }

  function cancel(element: HTMLElement) {
    const animation = animations.get(element);
    animations.delete(element);
    heldPointers.delete(element);
    if (heldPointers.size === 0) removePointerListeners();
    animation?.cancel();
  }

  function removePointerListeners() {
    win!.removeEventListener('pointerup', releasePointer, true);
    win!.removeEventListener('pointercancel', releasePointer, true);
    win!.removeEventListener('blur', releaseHeldPointers);
  }

  function releasePointer(event: PointerEvent) {
    // Pointerup has already selected its hit target before this capture listener.
    for (const [element, pointerId] of heldPointers) {
      if (pointerId === event.pointerId) cancel(element);
    }
  }

  function releaseHeldPointers() {
    for (const element of heldPointers.keys()) cancel(element);
  }

  function skipPending(target: Node) {
    // Interaction can precede the first observer callback. Keep that content still.
    for (const element of observed) {
      if (element.contains(target)) {
        intersection?.unobserve(element);
        observed.delete(element);
        played.add(element);
      }
    }
  }

  function stopTargetMotion(event: Event) {
    const target = event.target as Node | null;
    if (!target) return;
    skipPending(target);
    for (const element of animations.keys()) {
      if (element.contains(target) && !heldPointers.has(element)) cancel(element);
    }
  }

  function holdTargetMotion(event: PointerEvent) {
    const target = event.target as Node | null;
    if (!target) return;
    skipPending(target);
    const wasEmpty = heldPointers.size === 0;
    for (const [element, animation] of animations) {
      if (element.contains(target)) {
        // Keep the down/up hit area fixed; removing the transform here could lose click.
        const heldTime = animation.currentTime;
        animation.pause();
        if (heldTime !== null) animation.currentTime = heldTime;
        heldPointers.set(element, event.pointerId);
      }
    }
    if (wasEmpty && heldPointers.size > 0) {
      win!.addEventListener('pointerup', releasePointer, true);
      win!.addEventListener('pointercancel', releasePointer, true);
      win!.addEventListener('blur', releaseHeldPointers);
    }
  }

  function register(element: HTMLElement) {
    if (!content.contains(element) || element.closest('dialog, [role="dialog"]')
      || played.has(element) || observed.has(element)) return;
    observed.add(element);
    intersection?.observe(element);
  }

  function scan(node: Node) {
    if (node.nodeType !== 1) return;
    const element = node as HTMLElement;
    if (element !== content && element.matches(revealSelector)) register(element);
    for (const descendant of element.querySelectorAll<HTMLElement>(revealSelector)) register(descendant);
  }

  function release(node: Node) {
    for (const element of observed) {
      if (node.contains(element)) {
        intersection?.unobserve(element);
        observed.delete(element);
      }
    }
    for (const element of animations.keys()) {
      if (node.contains(element)) cancel(element);
    }
  }

  function stop() {
    intersection?.disconnect();
    mutation?.disconnect();
    intersection = null;
    mutation = null;
    observed.clear();
    for (const element of animations.keys()) cancel(element);
    heldPointers.clear();
    removePointerListeners();
    content.removeEventListener('focusin', stopTargetMotion);
    content.removeEventListener('pointerdown', holdTargetMotion, true);
    win!.removeEventListener('resize', resize);
    win!.removeEventListener('scroll', scheduleBackground);
    scrolling = false;
    clearBackground();
  }

  function start() {
    stop();
    if (disposed || preference.matches) return;
    intersection = new win!.IntersectionObserver(entries => {
      if (disposed || preference.matches) return;
      for (const entry of entries) {
        const element = entry.target as HTMLElement;
        if (!entry.isIntersecting || !observed.has(element)) continue;
        observed.delete(element);
        intersection?.unobserve(element);
        if (!content.contains(element) || element.closest('dialog, [role="dialog"]')) continue;
        played.add(element);
        if (element.contains(content.ownerDocument.activeElement)) continue;
        const animation = element.animate([
          { opacity: 0, transform: 'translateY(12px)' },
          { opacity: 1, transform: 'none' },
        ], { duration: 280, easing: 'cubic-bezier(.2, .7, .2, 1)' });
        animations.set(element, animation);
        // Cancellation rejects finished; handle both outcomes and release the element.
        const forget = () => {
          if (animations.get(element) === animation) {
            animations.delete(element);
            heldPointers.delete(element);
            if (heldPointers.size === 0) removePointerListeners();
          }
        };
        void animation.finished.then(forget, forget);
      }
    });
    mutation = new win!.MutationObserver(records => {
      if (disposed || preference.matches) return;
      // Drop detached privacy-sensitive DOM before examining newly rendered content.
      for (const record of records) for (const node of record.removedNodes) release(node);
      for (const record of records) for (const node of record.addedNodes) scan(node);
    });
    mutation.observe(content, { childList: true, subtree: true });
    scan(content);
    content.addEventListener('focusin', stopTargetMotion);
    content.addEventListener('pointerdown', holdTargetMotion, true);
    win!.addEventListener('resize', resize);
    resize();
  }

  preference.addEventListener('change', start);
  start();
  return () => {
    if (disposed) return;
    disposed = true;
    preference.removeEventListener('change', start);
    stop();
  };
}
