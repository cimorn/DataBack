import test from 'node:test';
import assert from 'node:assert/strict';
import * as reminders from '../src/reminders.ts';
import * as drafts from '../src/assetDraft.ts';
import { buildHierarchy } from '../src/hierarchy.ts';

const asset = (id, parent_id = null, extra = {}) => ({id, name: id, parent_id, kind: 'folder', source_path: '', notes: '', created_at: '', backup_target: false, backup_enabled: true, backup_interval_days: 30, target_location_ids: [], ...extra});
const assets = [asset('Emotion'), asset('Letter', 'Emotion', {backup_target: true}), asset('Photos', null, {backup_target: true}), asset('Me', 'Photos'), asset('2025', 'Me', {backup_target: true, backup_enabled: false}), asset('ordinary.txt', 'Me', {kind: 'file'}), asset('empty')];
const state = {assets, versions: [{id: 'v', asset_id: '2025', label: 'old', notes: '', created_at: ''}], locations: [{id: 'disk', name: 'Disk'}], records: [{id: 'r', version_id: 'v', location_id: 'disk', backed_up_at: '2026-09-01T00:00:00Z', stored_path: '/history', notes: '', created_at: ''}]};
const now = Date.parse('2026-10-12T00:00:00Z');

test('ordinary leaves have no reminder targets, next deadline or actionable state', () => {
  for (const entry of [assets[5], assets[6]]) {
    const result = reminders.leafReminder(state, entry, now);
    assert.equal(result.status, 'ordinary');
    assert.equal(result.nextDue, null);
    assert.equal(result.actionable, false);
    assert.deepEqual(result.targets, []);
  }
});

test('marking is exact true and missing or truthy flags cannot create reminders', () => {
  for (const flag of [undefined, 'true', 1, false]) {
    assert.equal(reminders.leafReminder(state, {...assets[1], backup_target: flag}, now).status, 'ordinary');
  }
});

test('mixed-depth registration and due eligibility include only marked leaves', () => {
  assert.equal(typeof reminders.backupTargets, 'function');
  assert.deepEqual(reminders.backupTargets(state).map(entry => entry.id), ['Letter', '2025']);
  assert.deepEqual(reminders.dueLeaves(state, now).map(entry => entry.asset.id), ['Letter', '2025']);
});

test('rollups count marked leaves and retain their latest record independently of legacy switches', () => {
  const result = reminders.folderRollup(state, buildHierarchy(assets), 'Photos', now);
  assert.equal(result.leafCount, 1);
  assert.equal(result.activeCount, 1);
  assert.equal(result.inactiveCount, 0);
  assert.equal(result.actionableCount, 1);
  assert.equal(result.latest.id, 'r');
});

test('unmarking keeps history and policy while remarking recomputes the retained deadline', () => {
  const chosen = {...assets[4], backup_enabled: true, target_location_ids: ['disk'], backup_interval_days: 7};
  const ordinary = reminders.leafReminder(state, {...chosen, backup_target: false}, now);
  assert.equal(ordinary.status, 'ordinary');
  assert.equal(ordinary.latest.id, 'r');
  assert.equal(ordinary.locations.get('disk').id, 'r');
  assert.deepEqual(ordinary.targets, []);
  const restored = reminders.leafReminder(state, chosen, now);
  assert.equal(restored.status, 'due');
  assert.equal(restored.latest.id, 'r');
  assert.equal(restored.nextDue, Date.parse('2026-09-08T00:00:00Z'));
  assert.equal(restored.targets[0].locationId, 'disk');
});

test('a marked directory with children is dormant and never passes its marker to ordinary children', () => {
  const parent = reminders.leafReminder(state, assets[2], now);
  assert.equal(parent.actionable, false);
  assert.equal(parent.nextDue, null);
  assert.deepEqual(parent.targets, []);
  const afterRemovingChildren = {...state, assets: assets.filter(entry => !['Me', '2025', 'ordinary.txt'].includes(entry.id))};
  assert.deepEqual(reminders.backupTargets(afterRemovingChildren).map(entry => entry.id), ['Letter', 'Photos']);
  assert.equal(reminders.leafReminder(afterRemovingChildren, assets[2], now).status, 'never');
});

test('new typed draft is unmarked with retained inactive reminder preference and chosen parent', () => {
  assert.equal(typeof drafts.createAssetDraft, 'function');
  const draft = drafts.createAssetDraft(undefined, 'file', 'Me');
  assert.equal(draft.backup_target, false);
  assert.equal(draft.backup_enabled, true);
  assert.equal(draft.parent_id, 'Me');
  assert.equal(draft.kind, 'file');
  assert.equal(drafts.assetPayload(draft).backup_target, false);
});

test('mark entry starts a checked draft without modifying the source asset or its policy', () => {
  assert.equal(typeof drafts.createAssetDraft, 'function');
  const original = {...assets[4], backup_target: false, backup_interval_days: 14, target_location_ids: ['disk']};
  const draft = drafts.createAssetDraft(original, 'folder', null, true);
  assert.equal(draft.backup_target, true);
  assert.equal(draft.backup_enabled, false);
  assert.equal(draft.backup_interval_days, '14');
  assert.deepEqual(draft.target_location_ids, ['disk']);
  assert.equal(original.backup_target, false);
  const unmarked = drafts.assetPayload({...draft, backup_target: false});
  assert.equal(unmarked.backup_enabled, false);
  assert.equal(unmarked.backup_target, false);
  assert.equal(unmarked.backup_interval_days, 14);
  assert.deepEqual(unmarked.target_location_ids, ['disk']);
});
