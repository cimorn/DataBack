import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';

const source = new URL('../src/scrollMotion.ts', import.meta.url);
const module = existsSync(source) ? await import(source.href) : {};
const selectorClasses = ['page-heading', 'directory-toolbar', 'history-filters', 'detail-heading', 'empty-state'];

class Events {
  listeners = new Map();
  addEventListener(type, callback, options) { this.listeners.set(type, [...(this.listeners.get(type) ?? []), { callback, options }]); }
  removeEventListener(type, callback) { this.listeners.set(type, (this.listeners.get(type) ?? []).filter(item => item.callback !== callback)); }
  emit(type, event = {}) { for (const item of this.listeners.get(type) ?? []) item.callback(event); }
  count(type) { return (this.listeners.get(type) ?? []).length; }
}

class Element extends Events {
  nodeType = 1;
  children = [];
  parentElement = null;
  animations = [];
  properties = new Map();
  style = {
    setProperty: (name, value) => this.properties.set(name, value),
    removeProperty: name => this.properties.delete(name),
  };
  constructor(document, className = '', tagName = 'DIV') { super(); this.ownerDocument = document; this.className = className; this.tagName = tagName; }
  append(child) { child.parentElement?.remove(child); this.children.push(child); child.parentElement = this; return child; }
  remove(child) { this.children = this.children.filter(item => item !== child); child.parentElement = null; }
  contains(node) { return this === node || this.children.some(child => child.contains(node)); }
  matches(selector) {
    if (selector === 'dialog, [role="dialog"]' || selector === 'dialog,[role="dialog"]') return this.tagName === 'DIALOG' || this.role === 'dialog';
    if (selectorClasses.some(name => selector.includes('.' + name) && this.className.split(' ').includes(name))) return true;
    return this.tagName === 'TR' && this.parentElement?.tagName === 'TBODY' && ['explorer-table', 'history-table', 'record-detail-table'].includes(this.parentElement.parentElement?.className);
  }
  closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) ?? null; }
  querySelectorAll(selector) { return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]); }
  animate(keyframes, options) {
    let finish, reject;
    const animation = { keyframes, options, cancelled: false, paused: false, currentTime: 0, finished: new Promise((resolve, fail) => { finish = resolve; reject = fail; }), finish: () => finish(), pause() { this.paused = true; }, cancel() { this.cancelled = true; this.paused = false; reject(new Error('Animation cancelled')); } };
    this.animations.push(animation);
    return animation;
  }
}

function fixture({ reduced = false, width = 1280, missing } = {}) {
  const win = new Events();
  const document = { defaultView: win };
  const media = Object.assign(new Events(), { matches: reduced });
  const frames = new Map();
  let nextFrame = 1;
  const intersections = [], mutations = [];
  win.innerWidth = width;
  win.scrollY = 0;
  win.matchMedia = () => media;
  win.requestAnimationFrame = callback => { const id = nextFrame++; frames.set(id, callback); return id; };
  win.cancelAnimationFrame = id => frames.delete(id);
  win.IntersectionObserver = class {
    targets = new Set();
    constructor(callback) { this.callback = callback; intersections.push(this); }
    observe(target) { this.targets.add(target); }
    unobserve(target) { this.targets.delete(target); }
    disconnect() { this.targets.clear(); this.disconnected = true; }
    enter(...targets) { this.callback(targets.map(target => ({ target, isIntersecting: true }))); }
  };
  win.MutationObserver = class {
    constructor(callback) { this.callback = callback; mutations.push(this); }
    observe(target, options) { this.target = target; this.options = options; }
    disconnect() { this.target = null; this.disconnected = true; }
    change(addedNodes = [], removedNodes = []) { this.callback([{ addedNodes, removedNodes }]); }
  };
  const content = new Element(document), background = new Element(document);
  if (missing === 'animate') content.animate = undefined;
  else if (missing) win[missing] = undefined;
  return { win, document, media, content, background, intersections, mutations, frames,
    element: (className, tagName) => new Element(document, className, tagName),
    flush: () => { const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(callback => callback()); },
    mount: () => { assert.equal(typeof module.mountScrollMotion, 'function'); return module.mountScrollMotion(content, background); },
  };
}

test('content remains visible with reduced motion or unavailable browser animation APIs', () => {
  for (const options of [{ reduced: true }, ...['IntersectionObserver', 'MutationObserver', 'animate', 'matchMedia', 'requestAnimationFrame'].map(missing => ({ missing }))]) {
    const f = fixture(options), heading = f.content.append(f.element('page-heading'));
    const cleanup = f.mount();
    assert.deepEqual([...heading.properties], []);
    assert.equal(heading.animations.length, 0);
    assert.equal(f.intersections.length, 0);
    assert.equal(f.frames.size, 0);
    assert.equal(f.win.count('scroll'), 0);
    cleanup();
  }
});

test('eligible content enters once without hiding pending content or animating dialogs and surfaces', async () => {
  const f = fixture();
  const heading = f.content.append(f.element('page-heading'));
  const surface = f.content.append(f.element('surface'));
  const dialog = f.content.append(f.element('', 'DIALOG'));
  const dialogHeading = dialog.append(f.element('page-heading'));
  const cleanup = f.mount(), observer = f.intersections[0];
  assert.ok(observer.targets.has(heading));
  assert.ok(!observer.targets.has(surface));
  assert.ok(!observer.targets.has(dialogHeading));
  assert.deepEqual([...heading.properties], []);
  observer.enter(heading);
  observer.enter(heading);
  assert.equal(heading.animations.length, 1);
  assert.ok(!observer.targets.has(heading));
  assert.equal(heading.animations[0].options.duration, 280);
  assert.ok(!heading.animations[0].options.fill || heading.animations[0].options.fill === 'none');
  heading.animations[0].finish();
  await Promise.resolve();
  cleanup();
  assert.equal(heading.animations[0].cancelled, false);
});

test('new rows register and removed private subtrees release observers and running animations', async () => {
  const f = fixture(), cleanup = f.mount();
  const table = f.content.append(f.element('explorer-table', 'TABLE'));
  const body = table.append(f.element('', 'TBODY'));
  const first = body.append(f.element('', 'TR')), second = body.append(f.element('', 'TR'));
  f.mutations[0].change([table]);
  assert.ok(f.intersections[0].targets.has(first));
  assert.ok(f.intersections[0].targets.has(second));
  f.intersections[0].enter(first);
  f.content.remove(table);
  f.mutations[0].change([], [table]);
  assert.equal(first.animations[0].cancelled, true);
  assert.equal(f.intersections[0].targets.size, 0);
  f.intersections[0].enter(second);
  assert.equal(second.animations.length, 0);
  await Promise.resolve();
  cleanup();
});

test('focus cancels movement for the containing row and absorbs animation cancellation', async () => {
  const f = fixture();
  const heading = f.content.append(f.element('page-heading'));
  const button = heading.append(f.element('', 'BUTTON'));
  const cleanup = f.mount();
  f.intersections[0].enter(heading);
  f.content.emit('focusin', { target: button });
  assert.equal(heading.animations[0].cancelled, true);
  await Promise.resolve();
  cleanup();
});

test('pointer press freezes movement through focus and releases it after the up target is determined', async () => {
  const f = fixture();
  const heading = f.content.append(f.element('page-heading'));
  const handle = heading.append(f.element('', 'BUTTON'));
  handle.click = () => assert.fail('Do not synthesize clicks');
  handle.setPointerCapture = () => assert.fail('Do not take pointer capture');
  const cleanup = f.mount();
  f.intersections[0].enter(heading);
  assert.equal(f.content.listeners.get('pointerdown')?.[0].options, true);
  f.content.emit('pointerdown', { target: handle, pointerId: 17, preventDefault() { assert.fail('Pointer interaction must continue'); }, stopPropagation() { assert.fail('Pointer interaction must continue'); } });
  assert.equal(heading.animations[0].paused, true);
  assert.equal(heading.animations[0].cancelled, false);
  f.content.emit('focusin', { target: handle });
  assert.equal(heading.animations[0].cancelled, false);
  assert.equal(f.win.listeners.get('pointerup')?.[0].options, true);
  f.win.emit('pointerup', { target: handle, pointerId: 99 });
  assert.equal(heading.animations[0].cancelled, false);
  f.win.emit('pointerup', { target: handle, pointerId: 17 });
  assert.equal(heading.animations[0].cancelled, true);
  assert.equal(f.win.count('pointerup') + f.win.count('pointercancel') + f.win.count('blur'), 0);
  await Promise.resolve();
  cleanup();
  assert.equal(f.content.count('pointerdown'), 0);
});

test('pointer press synchronously holds the sampled animation time and leaves null time unset', async () => {
  for (const initialTime of [42, null]) {
    const f = fixture(), heading = f.content.append(f.element('page-heading'));
    const cleanup = f.mount();
    f.intersections[0].enter(heading);
    const animation = heading.animations[0], writes = [];
    let currentTime = initialTime;
    Object.defineProperty(animation, 'currentTime', {
      get: () => currentTime,
      set: value => { writes.push(value); currentTime = value; },
    });
    // A pending pause may otherwise advance the timeline before it takes effect.
    animation.pause = () => { animation.paused = true; if (currentTime !== null) currentTime += 1; };
    f.content.emit('pointerdown', { target: heading, pointerId: 3 });
    assert.equal(animation.paused, true);
    assert.equal(currentTime, initialTime);
    assert.deepEqual(writes, initialTime === null ? [] : [initialTime]);
    cleanup();
    await Promise.resolve();
  }
});

test('pointer cancellation outside content and window blur release frozen animation without a click', async () => {
  for (const event of ['pointercancel', 'blur']) {
    const f = fixture(), heading = f.content.append(f.element('page-heading'));
    const cleanup = f.mount();
    f.intersections[0].enter(heading);
    f.content.emit('pointerdown', { target: heading, pointerId: 7 });
    assert.equal(heading.animations[0].paused, true);
    f.win.emit(event, { target: f.element('outside'), pointerId: 7 });
    assert.equal(heading.animations[0].cancelled, true);
    assert.equal(f.win.count('pointerup') + f.win.count('pointercancel') + f.win.count('blur'), 0);
    await Promise.resolve();
    cleanup();
  }
});

test('each pointer releases its own frozen content while keyboard focus still cancels other movement', async () => {
  const f = fixture();
  const first = f.content.append(f.element('page-heading'));
  const second = f.content.append(f.element('directory-toolbar'));
  const keyboardTarget = f.content.append(f.element('empty-state'));
  const cleanup = f.mount();
  f.intersections[0].enter(first, second, keyboardTarget);
  f.content.emit('pointerdown', { target: first, pointerId: 1 });
  f.content.emit('pointerdown', { target: second, pointerId: 2 });
  assert.equal(f.win.count('pointerup'), 1);
  f.content.emit('focusin', { target: keyboardTarget });
  assert.equal(keyboardTarget.animations[0].cancelled, true);
  f.win.emit('pointerup', { pointerId: 1 });
  assert.equal(first.animations[0].cancelled, true);
  assert.equal(second.animations[0].cancelled, false);
  assert.equal(f.win.count('pointerup'), 1);
  f.win.emit('pointerup', { pointerId: 2 });
  assert.equal(second.animations[0].cancelled, true);
  assert.equal(f.win.count('pointerup'), 0);
  await Promise.resolve();
  cleanup();
});

test('removal, reduced motion and cleanup release frozen elements and their global pointer listeners', async () => {
  for (const action of ['remove', 'reduce', 'cleanup']) {
    const f = fixture(), heading = f.content.append(f.element('page-heading'));
    const cleanup = f.mount();
    f.intersections[0].enter(heading);
    f.content.emit('pointerdown', { target: heading, pointerId: 5 });
    assert.equal(heading.animations[0].paused, true);
    if (action === 'remove') { f.content.remove(heading); f.mutations[0].change([], [heading]); }
    if (action === 'reduce') { f.media.matches = true; f.media.emit('change'); }
    if (action === 'cleanup') cleanup();
    assert.equal(heading.animations[0].cancelled, true);
    assert.equal(f.win.count('pointerup') + f.win.count('pointercancel') + f.win.count('blur'), 0);
    await Promise.resolve();
    cleanup();
  }
});

test('focus and pointer interaction before an intersection permanently skip pending row animation', () => {
  for (const event of ['focusin', 'pointerdown']) {
    const f = fixture();
    const table = f.content.append(f.element('explorer-table', 'TABLE'));
    const body = table.append(f.element('', 'TBODY'));
    const row = body.append(f.element('', 'TR'));
    const button = row.append(f.element('', 'BUTTON'));
    const cleanup = f.mount(), observer = f.intersections[0];
    f.content.emit(event, { target: button });
    assert.ok(!observer.targets.has(row));
    observer.enter(row);
    assert.equal(row.animations.length, 0);
    body.remove(row); f.mutations[0].change([], [row]);
    body.append(row); f.mutations[0].change([row]);
    assert.ok(!observer.targets.has(row));
    cleanup();
  }
});

test('autofocus established before mounting skips animation when the intersection arrives', () => {
  const f = fixture();
  const heading = f.content.append(f.element('page-heading'));
  const input = heading.append(f.element('', 'INPUT'));
  f.document.activeElement = input;
  const cleanup = f.mount(), observer = f.intersections[0];
  observer.enter(heading);
  assert.equal(heading.animations.length, 0);
  assert.ok(!observer.targets.has(heading));
  f.document.activeElement = null;
  f.content.remove(heading); f.mutations[0].change([], [heading]);
  f.content.append(heading); f.mutations[0].change([heading]);
  assert.ok(!observer.targets.has(heading));
  cleanup();
});

test('legacy media query objects without modern event methods fall back to visible static content', () => {
  for (const missing of ['addEventListener', 'removeEventListener']) {
    const f = fixture(), heading = f.content.append(f.element('page-heading'));
    f.media[missing] = undefined;
    const cleanup = f.mount();
    assert.equal(heading.animations.length, 0);
    assert.deepEqual([...heading.properties], []);
    assert.equal(f.intersections.length, 0);
    assert.equal(f.frames.size, 0);
    assert.equal(f.win.count('scroll') + f.win.count('resize') + f.content.count('focusin') + f.content.count('pointerdown'), 0);
    assert.doesNotThrow(cleanup);
  }
});

test('desktop parallax batches passive scrolls, caps distance and disables on small resize', () => {
  const f = fixture(), cleanup = f.mount();
  assert.equal(f.win.listeners.get('scroll')[0].options.passive, true);
  f.win.scrollY = 500;
  f.win.emit('scroll'); f.win.emit('scroll');
  assert.equal(f.frames.size, 1);
  f.flush();
  assert.equal(f.background.properties.get('--parallax-near'), '40px');
  assert.equal(f.background.properties.get('--parallax-far'), '17.5px');
  f.win.scrollY = 5000; f.win.emit('scroll'); f.flush();
  assert.equal(f.background.properties.get('--parallax-near'), '120px');
  assert.equal(f.background.properties.get('--parallax-far'), '56px');
  f.win.innerWidth = 1000; f.win.emit('resize');
  assert.equal(f.win.count('scroll'), 0);
  assert.equal(f.frames.size, 0);
  assert.equal(f.background.properties.size, 0);
  f.win.innerWidth = 1001; f.win.scrollY = -5; f.win.emit('resize'); f.flush();
  assert.equal(f.background.properties.get('--parallax-near'), '0px');
  cleanup();
});

test('live reduced-motion changes cancel all work and can register unseen content when re-enabled', async () => {
  const f = fixture(), heading = f.content.append(f.element('page-heading'));
  const cleanup = f.mount();
  f.intersections[0].enter(heading);
  f.media.matches = true; f.media.emit('change');
  assert.equal(heading.animations[0].cancelled, true);
  assert.equal(f.intersections[0].targets.size, 0);
  assert.equal(f.mutations[0].target, null);
  assert.equal(f.frames.size, 0);
  assert.equal(f.win.count('scroll'), 0);
  assert.equal(f.background.properties.size, 0);
  const later = f.content.append(f.element('empty-state'));
  f.media.matches = false; f.media.emit('change');
  assert.ok(f.intersections[1].targets.has(later));
  assert.ok(!f.intersections[1].targets.has(heading));
  await Promise.resolve();
  cleanup();
});

test('cleanup is idempotent and leaves no observed DOM, listeners, frames or animation references', async () => {
  const f = fixture(), heading = f.content.append(f.element('page-heading'));
  const cleanup = f.mount();
  f.intersections[0].enter(heading);
  cleanup(); cleanup();
  assert.equal(heading.animations[0].cancelled, true);
  assert.equal(f.intersections[0].targets.size, 0);
  assert.equal(f.mutations[0].target, null);
  assert.equal(f.win.count('scroll') + f.win.count('resize') + f.content.count('focusin') + f.media.count('change'), 0);
  assert.equal(f.frames.size, 0);
  assert.equal(f.background.properties.size, 0);
  f.media.emit('change'); f.win.emit('scroll'); f.intersections[0].enter(heading);
  f.mutations[0].change([f.element('empty-state')]);
  assert.equal(heading.animations.length, 1);
  assert.equal(f.frames.size, 0);
  await Promise.resolve();
});
