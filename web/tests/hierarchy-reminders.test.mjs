import test from 'node:test';
import assert from 'node:assert/strict';
import * as hierarchy from '../src/hierarchy.ts';
import * as reminders from '../src/reminders.ts';

const asset = (id, name, parent_id = null, kind = 'folder', extra = {}) => ({ id, name, parent_id, kind, source_path: '', backup_target: true, backup_enabled: true, backup_interval_days: 30, target_location_ids: [], notes: '', created_at: '', ...extra });
const assets = [asset('emotion', 'Emotion'), asset('letter', 'Letter', 'emotion'), asset('photos', 'Photos'), asset('me', 'Me', 'photos'), asset('year', '2025', 'me'), asset('file', 'note.txt', 'me', 'file')];
const version = (id, asset_id, created_at = '2026-01-01T00:00:00Z') => ({ id, asset_id, label: id, notes: '', created_at });
const record = (id, version_id, location_id, backed_up_at) => ({ id, version_id, location_id, backed_up_at, stored_path: '/backup', notes: '', created_at: '' });
const state = { assets, versions: [version('parent', 'photos'), version('old', 'year'), version('new', 'year', '2026-09-01T00:00:00Z')], locations: [{id: 'disk', name: '硬盘'}, {id: 'cloud', name: '网盘'}], records: [record('parent-record', 'parent', 'disk', '2026-10-10T00:00:00Z'), record('recent-old', 'old', 'disk', '2026-10-01T00:00:00Z'), record('new-version', 'new', 'disk', '2026-09-01T00:00:00Z')] };
const now = Date.parse('2026-10-11T00:00:00Z');

test('manual hierarchy supports mixed depths and never infers parents from source text', () => {
  assert.equal(typeof hierarchy.buildHierarchy, 'function');
  const tree = hierarchy.buildHierarchy([...assets, asset('independent', 'Separate', null, 'folder', {source_path: 'Photos/Me/2025'})]);
  assert.deepEqual(tree.children(null).map(a => a.id), ['emotion', 'photos', 'independent']);
  assert.deepEqual(tree.children('me').map(a => a.id), ['year', 'file']);
  assert.deepEqual(tree.leafDescendants('photos').map(a => a.id), ['year', 'file']);
  assert.deepEqual(tree.ancestors('year').map(a => a.name), ['Photos', 'Me']);
  assert.equal(tree.path('year'), 'Photos/Me/2025');
  assert.equal(tree.isLeaf('letter'), true);
  assert.equal(tree.isLeaf('me'), false);
});

test('move choices exclude files, the edited folder and every descendant', () => {
  assert.equal(typeof hierarchy.buildHierarchy, 'function');
  const tree = hierarchy.buildHierarchy(assets);
  assert.deepEqual(tree.moveOptions('photos').map(a => a.id), ['emotion', 'letter']);
});

test('reminders use latest actual backup across all versions', () => {
  assert.equal(typeof reminders.leafReminder, 'function');
  const result = reminders.leafReminder(state, assets.find(a => a.id === 'year'), now);
  assert.equal(result.latest.id, 'recent-old');
  assert.equal(result.status, 'current');
  assert.equal(result.nextDue, Date.parse('2026-10-31T00:00:00Z'));
});

test('location targets retain metadata while latest actual copy defines overall due', () => {
  assert.equal(typeof reminders.leafReminder, 'function');
  const target = asset('year', '2025', 'me', 'folder', { target_location_ids: ['disk', 'cloud'] });
  const result = reminders.leafReminder(state, target, now);
  assert.deepEqual(result.targets.map(t => t.status), ['current', 'never']);
  assert.equal(result.status, 'current');
  assert.equal(result.nextDue, Date.parse('2026-10-31T00:00:00Z'));
  assert.equal(result.actionable, false);
  const cloudOnly = reminders.leafReminder(state, {...target, target_location_ids: ['cloud']}, now);
  assert.equal(cloudOnly.targets[0].latest, undefined);
});

test('unregistered leaves return never and null deadline before dereferencing a record', () => {
  assert.equal(typeof reminders.leafReminder, 'function');
  const result = reminders.leafReminder(state, assets[1], now);
  assert.equal(result.latest, undefined);
  assert.equal(result.status, 'never');
  assert.equal(result.nextDue, null);
});

test('empty target list uses any location and exact deadline is due', () => {
  assert.equal(typeof reminders.leafReminder, 'function');
  const leaf = assets.find(a => a.id === 'year');
  assert.equal(reminders.leafReminder(state, leaf, Date.parse('2026-10-31T00:00:00Z') - 1).status, 'current');
  assert.equal(reminders.leafReminder(state, leaf, Date.parse('2026-10-31T00:00:00Z')).status, 'due');
  assert.equal(reminders.leafReminder(state, {...leaf, backup_interval_days: 5}, now).status, 'due');
});

test('due leaves exclude all parents and parent rollup ignores historical parent records', () => {
  assert.equal(typeof reminders.dueLeaves, 'function');
  assert.deepEqual(reminders.dueLeaves(state, now).map(item => item.asset.id), ['letter', 'file']);
  const tree = hierarchy.buildHierarchy(assets);
  const rollup = reminders.folderRollup(state, tree, 'photos', now);
  assert.equal(rollup.leafCount, 2);
  assert.equal(rollup.actionableCount, 1);
  assert.equal(rollup.latest.id, 'recent-old');
});

test('overall deadline ignores older target copies when another copy is newer', () => {
  assert.equal(typeof reminders.leafReminder, 'function');
  const updated = {...state, records: [...state.records, record('cloud', 'old', 'cloud', '2026-09-10T00:00:00Z')]};
  const target = {...assets.find(a => a.id === 'year'), target_location_ids: ['disk', 'cloud']};
  const result = reminders.leafReminder(updated, target, now);
  assert.equal(result.latest.id, 'recent-old');
  assert.equal(result.nextDue, Date.parse('2026-10-31T00:00:00Z'));
  assert.equal(result.status, 'current');
});

test('the backup-target flag is the only reminder switch even for legacy reminder-off leaves', () => {
  const leaf = {...assets.find(a => a.id === 'year'), backup_interval_days: 5, backup_enabled: false};
  const result = reminders.leafReminder(state, leaf, now);
  assert.equal(result.status, 'due');
  assert.equal(result.actionable, true);
  assert.equal(result.nextDue, Date.parse('2026-10-06T00:00:00Z'));
  assert.equal(result.latest.id, 'recent-old');
  assert.equal(result.targets[0].status, 'due');
  assert.equal(reminders.leafReminder(state, {...leaf, backup_target: false}, now).status, 'ordinary');
  assert.equal(reminders.leafReminder(state, {...leaf, backup_enabled: true}, now).status, 'due');
  const updated = {...state, assets: state.assets.map(a => a.id === 'year' ? leaf : a)};
  const rollup = reminders.folderRollup(updated, hierarchy.buildHierarchy(updated.assets), 'photos', now);
  assert.equal(rollup.activeCount, 2);
  assert.equal(rollup.inactiveCount, 0);
  assert.equal(rollup.actionableCount, 2);
  assert.equal(rollup.latest.id, 'recent-old');
});

test('a disabled intermediate directory never disables its children', () => {
  const updated = {...state, assets: state.assets.map(a => a.id === 'photos' ? {...a, backup_enabled: false} : a)};
  assert.deepEqual(reminders.dueLeaves(updated, now).map(item => item.asset.id), ['letter', 'file']);
});
