import test from 'node:test';
import assert from 'node:assert/strict';
import * as requests from '../src/latestLoad.ts';

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
function harness() {
  assert.equal(typeof requests.createLatestLoader, 'function');
  const pending = [];
  const view = {state: null, loading: false, error: ''};
  const events = [];
  const load = requests.createLatestLoader(() => {const request = deferred(); pending.push(request); return request.promise;}, {
    start: () => {view.loading = true; view.error = ''; events.push('start');},
    success: value => {view.state = value; events.push(`success:${value}`);},
    failure: reason => {view.error = reason.message; events.push(`failure:${reason.message}`);},
    settled: () => {view.loading = false; events.push('settled');},
  });
  return {load, pending, view, events};
}

test('an older import refresh cannot overwrite the newer save when responses finish in reverse order', async () => {
  const {load, pending, view, events} = harness();
  const oldImport = load();
  const newSave = load();
  pending[1].resolve('saved-folder');
  await newSave;
  assert.deepEqual(view, {state: 'saved-folder', loading: false, error: ''});
  pending[0].resolve('old-snapshot');
  await oldImport;
  assert.deepEqual(view, {state: 'saved-folder', loading: false, error: ''});
  assert.deepEqual(events, ['start', 'start', 'success:saved-folder', 'settled']);
});

test('a stale failure cannot add an error or finish loading while the current refresh is pending', async () => {
  const {load, pending, view, events} = harness();
  const old = load();
  const latest = load();
  pending[0].reject(new Error('old connection failure'));
  await old;
  assert.deepEqual(view, {state: null, loading: true, error: ''});
  assert.deepEqual(events, ['start', 'start']);
  pending[1].resolve('latest-data');
  await latest;
  assert.deepEqual(view, {state: 'latest-data', loading: false, error: ''});
});

test('current errors remain visible after a stale success and a later retry can recover', async () => {
  const {load, pending, view, events} = harness();
  const old = load();
  const latest = load();
  pending[1].reject(new Error('current failure'));
  await latest;
  pending[0].resolve('stale-data');
  await old;
  assert.deepEqual(view, {state: null, loading: false, error: 'current failure'});
  assert.deepEqual(events, ['start', 'start', 'failure:current failure', 'settled']);
  const retry = load();
  assert.equal(view.error, '');
  assert.equal(view.loading, true);
  pending[2].resolve('recovered-data');
  await retry;
  assert.deepEqual(view, {state: 'recovered-data', loading: false, error: ''});
});

test('strict refresh reports read failure to the record editor and a later read can recover', async () => {
  const {load,pending,view}=harness();
  const failed=load({throwOnError:true});
  const rejection=assert.rejects(failed,/refresh failed/);
  pending[0].reject(new Error('refresh failed'));await rejection;
  assert.deepEqual(view,{state:null,loading:false,error:'refresh failed'});
  const retry=load({throwOnError:true});pending[1].resolve('confirmed-write');await retry;
  assert.deepEqual(view,{state:'confirmed-write',loading:false,error:''});
});

test('a stale strict failure rejects its caller without changing the newer refresh lifecycle', async () => {
  const {load,pending,view,events}=harness();
  const stale=load({throwOnError:true});const rejection=assert.rejects(stale,/old strict failure/);
  const latest=load();pending[0].reject(new Error('old strict failure'));await rejection;
  assert.deepEqual(view,{state:null,loading:true,error:''});assert.deepEqual(events,['start','start']);
  pending[1].resolve('newest');await latest;
  assert.deepEqual(view,{state:'newest',loading:false,error:''});
});
