import test from 'node:test';
import assert from 'node:assert/strict';
import * as draft from '../src/backupDraft.ts';

const disk = { id: 'disk', base_path: '/disk' };
const cloud = { id: 'cloud', base_path: '/cloud' };
const original = { disk: {selected: true, time: '2026-10-11T09:15', path: '/edited/path', notes: 'keep my edits'} };

test('a refreshed location list has complete safe drafts before the next render or submission', () => {
  assert.equal(typeof draft.reconcileDestinations, 'function');
  const beforeEffect = draft.reconcileDestinations(original, [disk, cloud], [], '2026-10-11T10:00');
  assert.doesNotThrow(() => [disk, cloud].map(location => beforeEffect[location.id].selected));
  assert.equal(beforeEffect.disk, original.disk);
  assert.deepEqual(beforeEffect.cloud, {selected: false, time: '2026-10-11T10:00', path: '/cloud', notes: ''});
  const submitted = [disk, cloud].filter(location => beforeEffect[location.id].selected).map(location => beforeEffect[location.id]);
  assert.deepEqual(submitted, [original.disk]);
  assert.deepEqual(Object.keys(original), ['disk'], 'reconciliation never mutates the active edit dictionary');
});

test('reconciliation removes deleted selected locations and preserves edits on retained locations', () => {
  assert.equal(typeof draft.reconcileDestinations, 'function');
  const refreshed = draft.reconcileDestinations({...original, cloud: {selected: true, time: '2026-10-11T10:00', path: '/cloud/edited', notes: 'cloud edit'}}, [cloud], [], '2026-10-11T11:00');
  assert.equal('disk' in refreshed, false);
  assert.equal(Object.values(refreshed).filter(item => item.selected).length, 1);
  assert.equal(refreshed.cloud.path, '/cloud/edited');
  assert.equal(refreshed.cloud.notes, 'cloud edit');
});

test('new target locations use selected defaults and unchanged lists preserve dictionary identity', () => {
  assert.equal(typeof draft.reconcileDestinations, 'function');
  const refreshed = draft.reconcileDestinations(original, [disk, cloud], ['cloud'], '2026-10-11T10:00');
  assert.equal(refreshed.cloud.selected, true);
  assert.equal(draft.reconcileDestinations(refreshed, [disk, cloud], [], '2026-10-12T11:00'), refreshed);
  assert.equal(refreshed.cloud.time, '2026-10-11T10:00', 'later refreshes retain the initialized backup time');
  assert.deepEqual(draft.reconcileDestinations(original, [], [], '2026-10-11T10:00'), {});
});

test('new destination defaults display legacy paths without replacing existing edits', () => {
 const locations=[{id:'unc',base_path:String.raw`\\fixture\backup`}];
 const initialized=draft.reconcileDestinations({},locations,['unc'],'2026-10-11T10:00');
 assert.equal(initialized.unc.path,'//fixture/backup');
 assert.equal(locations[0].base_path,String.raw`\\fixture\backup`);
});
