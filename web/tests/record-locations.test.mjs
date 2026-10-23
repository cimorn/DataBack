import test from 'node:test';
import assert from 'node:assert/strict';

test('typed locations reuse existing names and repeated rows without changing old locations', async () => {
  const module = await import('../src/recordLocations.ts').catch(() => ({}));
  assert.equal(typeof module.createLocationResolver, 'function', 'direct location entry is not implemented');
  const existing = [{id: 'old', name: 'Hard Disk', kind: 'external', base_path: '/original', notes: 'keep'}];
  let creates = 0;
  const resolve = module.createLocationResolver(existing, async (path, method, body) => {
    assert.equal(path, 'locations/resolve');
    assert.equal(method, 'POST');
    creates++;
    return {id: 'new', ...body};
  });
  assert.equal((await resolve('  hard disk  ')).id, 'old');
  assert.equal((await resolve('网盘')).id, 'new');
  assert.equal((await resolve('网盘 ')).id, 'new');
  assert.equal(creates, 1);
  assert.equal(existing.length, 1);
  assert.equal(existing[0].base_path, '/original');
});

test('a lost location-create response or concurrent duplicate is recovered by reading current state', async () => {
  const module = await import('../src/recordLocations.ts').catch(() => ({}));
  assert.equal(typeof module.createLocationResolver, 'function');
  const resolve = module.createLocationResolver([], async (path, method) => {
    if (method === 'POST') throw new Error('response lost');
    assert.equal(path, 'state');
    return {locations: [{id: 'recovered', name: 'NAS'}]};
  });
  assert.equal((await resolve('NAS')).id, 'recovered');
});

test('unrecoverable writes fail and blank locations never trigger requests', async () => {
  const module = await import('../src/recordLocations.ts').catch(() => ({}));
  assert.equal(typeof module.createLocationResolver, 'function');
  let calls = 0;
  const resolve = module.createLocationResolver([], async () => {calls++; throw new Error('offline');});
  await assert.rejects(resolve('   '), /备份位置/);
  assert.equal(calls, 0);
  await assert.rejects(resolve('NAS'), /offline/);
});

test('Unicode casefold variants use the backend canonical resolver', async () => {
  const {createLocationResolver} = await import('../src/recordLocations.ts');
  const existing = {id:'old',name:'Straße',kind:'nas',base_path:'/keep',notes:'preserve'};
  const resolve = createLocationResolver([existing], async (path, method, body) => {
    assert.equal(path, 'locations/resolve');
    assert.equal(method, 'POST');
    assert.deepEqual(body, {name:'STRASSE'});
    return existing;
  });
  assert.equal((await resolve('STRASSE')).id, 'old');
});
