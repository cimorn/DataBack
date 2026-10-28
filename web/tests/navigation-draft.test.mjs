import test from 'node:test';
import assert from 'node:assert/strict';
import * as navigation from '../src/navigation.ts';
import * as drafts from '../src/assetDraft.ts';

test('folder hashes preserve exact IDs, pages and invalid or deleted folders fall back to root', () => {
  assert.equal(typeof navigation.parseRoute, 'function');
  const assets = [{id: 'space/id', kind: 'folder'}, {id: 'file', kind: 'file'}];
  const route = {page: 'overview', folderId: 'space/id'};
  assert.deepEqual(navigation.parseRoute(navigation.routeHash(route)), route);
  assert.deepEqual(navigation.resolveRoute(navigation.parseRoute('#/folder/deleted'), assets), {page: 'overview', folderId: null});
  assert.deepEqual(navigation.resolveRoute(navigation.parseRoute('#/folder/file'), assets), {page: 'overview', folderId: null});
  assert.deepEqual(navigation.parseRoute('#/folder/%ZZ'), {page: 'overview', folderId: null});
  assert.deepEqual(navigation.parseRoute('#/due'), {page: 'overview', folderId: null});
});

test('removed data-management links resolve to the directory without reviving a retired page', () => {
  const route = navigation.parseRoute('#/data');
  assert.deepEqual(route, {page: 'overview', folderId: null});
  assert.equal(navigation.routeHash(route), '#/');
  assert.deepEqual(navigation.parseRoute('#/history'), {page: 'history', folderId: null});
});

test('marked terminal file routes resolve to their own record page', () => {
 const route={page:'overview',folderId:'file'};
 assert.deepEqual(navigation.resolveRoute(route,[{id:'file',kind:'file',backup_target:true}]),route);
 assert.deepEqual(navigation.resolveRoute(route,[{id:'file',kind:'file',backup_target:false}]),{page:'overview',folderId:null});
});

test('asset payload preserves explicit disabled state and defaults missing flag to enabled', () => {
  const draft = {name: 'Archive', kind: 'folder', source_path: '', notes: '', parent_id: '', backup_interval_days: '30', target_location_ids: []};
  assert.equal(drafts.assetPayload({...draft, backup_enabled: false}).backup_enabled, false);
  assert.equal(drafts.assetPayload(draft).backup_enabled, true);
});

test('asset payload retains numbers, selected targets and null root independently of text trimming', () => {
  assert.equal(typeof drafts.assetPayload, 'function');
  const draft = {name: ' 2025 ', kind: 'folder', source_path: ' ', notes: ' note ', parent_id: '', backup_interval_days: '14', target_location_ids: ['disk', 'cloud']};
  assert.deepEqual(drafts.assetPayload(draft), {name: '2025', kind: 'folder', source_path: '', notes: 'note', parent_id: null, backup_interval_days: 14, target_location_ids: ['disk', 'cloud'], backup_target: false, backup_enabled: true});
  assert.throws(() => drafts.assetPayload({...draft, backup_interval_days: '0'}), /1.*3650/);
  assert.throws(() => drafts.assetPayload({...draft, backup_interval_days: '1.5'}), /1.*3650/);
});

test('asset edit normalizes its displayed path but preserves untouched imported text during another edit', () => {
 const entry={id:'legacy',name:'original',kind:'folder',source_path:String.raw`\\fixture-nas\personal_folder`,notes:'',parent_id:null,backup_interval_days:30,target_location_ids:[],backup_target:true,backup_enabled:true};
 const draft=drafts.createAssetDraft(entry);
 assert.equal(draft.source_path,'//fixture-nas/personal_folder');
 assert.equal(drafts.assetPayload({...draft,name:'renamed'}).source_path,entry.source_path);
 assert.equal(drafts.assetPayload({...draft,source_path:String.raw`D:\New`}).source_path,'D:/New');
 assert.equal(entry.source_path,String.raw`\\fixture-nas\personal_folder`);
});
